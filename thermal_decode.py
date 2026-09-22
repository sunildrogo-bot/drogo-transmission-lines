"""
thermal_decode.py — point-temperature readout from DJI R-JPEG thermal photos,
using DJI's own Thermal SDK — matches DJI Thermal Analysis Tool 3's
readings exactly, since it's calling the same underlying calibration DJI's
own tools use.

SDK IS BUNDLED
--------------
DJI's Thermal SDK (the native libdirp library and its dependencies) ships
in this repo under vendor/dji_thermal_sdk/ for BOTH Linux (.so, for the
server this app actually deploys to) and Windows (.dll, for local dev) —
see that folder's README.md for license details. This module picks the
right one automatically based on the platform it's running on.

THERMAL_SDK_DIR in .env overrides the bundled default — e.g. point it at
your own local SDK install (C:\\SDKs\\DJI_Thermal_SDK\\tsdk-core\\lib\\
windows\\release_x64, or wherever) if you'd rather use that copy.

APPROACH
--------
Decoding is done once per photo (the whole frame, at the thermal sensor's
native resolution — read from the R-JPEG itself via dirp_get_rjpeg_
resolution, not hardcoded, since it varies by camera model), then cached
to a fingerprinted ".thermal.<hash>.npy" sidecar next to the image — the
hash covers every measurement parameter below, so changing distance/
emissivity/etc automatically invalidates old cached results instead of
silently keeping stale numbers around. Every point click after that first
decode is just an array index. Point coordinates from the frontend arrive
as % of the image's rendered bounds (0-100, same convention as defect
shape_coords) and get mapped onto the cached matrix's own resolution, not
the display size.
"""
import ctypes
import hashlib
import os
import platform
import threading

import numpy as np
from PIL import Image

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_IS_WINDOWS = platform.system() == 'Windows'
_BUNDLED_SDK_DIR = os.path.join(
    _MODULE_DIR, 'vendor', 'dji_thermal_sdk', 'lib',
    'windows' if _IS_WINDOWS else 'linux', 'release_x64',
)
_LIB_FILENAME = 'libdirp.dll' if _IS_WINDOWS else 'libdirp.so'

THERMAL_SDK_DIR = os.environ.get('THERMAL_SDK_DIR', '').strip() or _BUNDLED_SDK_DIR

_lib_lock = threading.Lock()
_lib = None
_lib_load_error = None


class ThermalSDKNotConfigured(Exception):
    pass


class ThermalDecodeError(Exception):
    pass


# ── ctypes bindings ──────────────────────────────────────────────────────
# Verified against DJI's own tsdk-core/api/dirp_api.h (the header that
# ships alongside the bundled libraries) and against a real sample R-JPEG.

class DirpResolution(ctypes.Structure):
    _fields_ = [
        ('width', ctypes.c_int32),
        ('height', ctypes.c_int32),
    ]


class DirpMeasurementParams(ctypes.Structure):
    # IMPORTANT: this must match dirp_measurement_params_t in dirp_api.h
    # EXACTLY — field-for-field, same order — since ctypes reads raw
    # memory by offset. The SDK build bundled here has 5 fields (newer
    # DTSDK versions added ambient_temp); passing a 4-field struct to a
    # function expecting 5 reads past the end of allocated memory —
    # undefined behavior, not a clean failure, so this is worth verifying
    # against your actual dirp_api.h if you ever swap in a different SDK
    # build via THERMAL_SDK_DIR.
    _fields_ = [
        ('distance', ctypes.c_float),      # metres, [1-25]
        ('humidity', ctypes.c_float),      # % relative humidity, [20-100]
        ('emissivity', ctypes.c_float),    # [0.10-1.00]
        ('reflection', ctypes.c_float),    # reflected apparent temp, deg C, [-40.0-500.0]
        ('ambient_temp', ctypes.c_float),  # ambient air temp, deg C, [-50-80]
    ]


# Default environmental measurement parameters — used whenever a specific
# measurement doesn't override them (see DEFAULT_PARAMS_DICT / the params
# argument threaded through get_shape_stats below). Adjust these to your
# typical inspection conditions.
DEFAULT_PARAMS_DICT = {
    'distance': 5.0,        # adjust to your typical inspection distance
    'humidity': 70.0,       # adjust to typical site conditions
    'emissivity': 0.95,     # ~0.95 is standard for painted/oxidised steel transmission structures
    'reflection': 25.0,     # reflected apparent temperature, deg C
    'ambient_temp': 20.0,   # ambient air temperature, deg C
}


def _params_struct(params_dict):
    return DirpMeasurementParams(
        distance=params_dict.get('distance', DEFAULT_PARAMS_DICT['distance']),
        humidity=params_dict.get('humidity', DEFAULT_PARAMS_DICT['humidity']),
        emissivity=params_dict.get('emissivity', DEFAULT_PARAMS_DICT['emissivity']),
        reflection=params_dict.get('reflection', DEFAULT_PARAMS_DICT['reflection']),
        ambient_temp=params_dict.get('ambient_temp', DEFAULT_PARAMS_DICT['ambient_temp']),
    )


def _load_lib():
    global _lib, _lib_load_error
    with _lib_lock:
        if _lib is not None or _lib_load_error is not None:
            return _lib
        if not THERMAL_SDK_DIR:
            _lib_load_error = 'THERMAL_SDK_DIR resolved empty — this should not happen with the bundled SDK present.'
            return None
        so_path = os.path.join(THERMAL_SDK_DIR, _LIB_FILENAME)
        if not os.path.exists(so_path):
            _lib_load_error = (
                f'{_LIB_FILENAME} not found at {so_path}. If THERMAL_SDK_DIR is set in .env, check it points at a '
                f'valid SDK folder; otherwise the bundled copy under vendor/dji_thermal_sdk/ may be missing.'
            )
            return None
        try:
            if _IS_WINDOWS and hasattr(os, 'add_dll_directory'):
                # So libdirp.dll's own dependencies (libv_dirp.dll etc, in
                # the same folder) get found.
                os.add_dll_directory(THERMAL_SDK_DIR)
            else:
                # Linux: libv_hirp.so (needed for newer camera models —
                # H20/M30-series use this path internally) has its own
                # further dependencies (libMicroJPEG/MicroTA/MicroIA,
                # libexif) that the dynamic linker won't find just from
                # being in the same folder — LD_LIBRARY_PATH only gets
                # consulted at process START, too late to set from here.
                # Explicitly pre-loading them with RTLD_GLOBAL registers
                # them in the process so libdirp.so's own internal loading
                # of libv_hirp.so finds them already resident instead of
                # searching and failing (this is what DIRP_ERROR_INVALID_
                # SUB_DLL, code -16, means when it happens).
                for dep_name in ('libexif.so.12', 'libMicroJPEG_Release_x64.so',
                                  'libMicroTA_Release_x64.so', 'libMicroIA_Release_x64.so'):
                    dep_path = os.path.join(THERMAL_SDK_DIR, dep_name)
                    if os.path.exists(dep_path):
                        ctypes.CDLL(dep_path, mode=ctypes.RTLD_GLOBAL)
            lib = ctypes.CDLL(so_path)
            lib.dirp_create_from_rjpeg.argtypes = [ctypes.c_char_p, ctypes.c_int32, ctypes.POINTER(ctypes.c_void_p)]
            lib.dirp_create_from_rjpeg.restype = ctypes.c_int32
            lib.dirp_get_rjpeg_resolution.argtypes = [ctypes.c_void_p, ctypes.POINTER(DirpResolution)]
            lib.dirp_get_rjpeg_resolution.restype = ctypes.c_int32
            lib.dirp_set_measurement_params.argtypes = [ctypes.c_void_p, ctypes.POINTER(DirpMeasurementParams)]
            lib.dirp_set_measurement_params.restype = ctypes.c_int32
            lib.dirp_measure_ex.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int32]
            lib.dirp_measure_ex.restype = ctypes.c_int32
            lib.dirp_destroy.argtypes = [ctypes.c_void_p]
            lib.dirp_destroy.restype = ctypes.c_int32
            _lib = lib
        except OSError as e:
            dep_hint = 'libv_dirp.dll/libv_girp.dll/libv_iirp.dll' if _IS_WINDOWS else 'libstdc++.so.6/libgomp1'
            _lib_load_error = f'Could not load {_LIB_FILENAME} (missing a dependency such as {dep_hint}?): {e}'
            return None
        return _lib


def _calibration_fingerprint(params_dict):
    """Short hash of every parameter that affects the decoded result.
    Baked into the cache filename so changing distance/emissivity/etc
    automatically invalidates old cached results instead of silently
    keeping stale numbers around."""
    values = tuple(params_dict.get(k, DEFAULT_PARAMS_DICT[k]) for k in
                    ('distance', 'humidity', 'emissivity', 'reflection', 'ambient_temp'))
    return hashlib.sha256(repr(values).encode()).hexdigest()[:12]


def _decode_full_matrix(image_abs_path, params_dict):
    """Runs the native SDK once for this image and returns a 2D numpy
    array of temperatures in °C, shaped (rows, cols) at the thermal
    sensor's own native resolution. Raises ThermalSDKNotConfigured /
    ThermalDecodeError on failure."""
    lib = _load_lib()
    if lib is None:
        raise ThermalSDKNotConfigured(_lib_load_error)

    with open(image_abs_path, 'rb') as f:
        data = f.read()

    handle = ctypes.c_void_p()
    rc = lib.dirp_create_from_rjpeg(data, len(data), ctypes.byref(handle))
    if rc != 0:
        raise ThermalDecodeError(
            f'dirp_create_from_rjpeg failed (code {rc}) — this file has no readable DJI thermal data. '
            f'It is usually a photo that was re-saved or compressed before upload (WhatsApp, Google Photos, '
            f'Windows Photos, a screenshot), which strips the radiometric data, or a non-radiometric image. '
            f'Upload the original _T.JPG straight off the drone/SD card.'
        )
    try:
        resolution = DirpResolution()
        rc = lib.dirp_get_rjpeg_resolution(handle, ctypes.byref(resolution))
        if rc != 0:
            raise ThermalDecodeError(f'dirp_get_rjpeg_resolution failed (code {rc}).')
        cols, rows = resolution.width, resolution.height

        params = _params_struct(params_dict)
        rc = lib.dirp_set_measurement_params(handle, ctypes.byref(params))
        if rc != 0:
            raise ThermalDecodeError(f'dirp_set_measurement_params failed (code {rc}).')

        count = rows * cols
        buf = (ctypes.c_float * count)()
        # size is the buffer size in BYTES per dirp_api.h, not element count.
        rc = lib.dirp_measure_ex(handle, buf, count * ctypes.sizeof(ctypes.c_float))
        if rc != 0:
            raise ThermalDecodeError(f'dirp_measure_ex failed (code {rc}).')
        matrix = np.ctypeslib.as_array(buf).reshape((rows, cols)).copy()
        return matrix
    finally:
        lib.dirp_destroy(handle)


def get_temperature_matrix(image_abs_path, params_dict=None):
    """Cached full-frame temperature matrix for one photo. Decodes once via
    the native SDK, then reuses the fingerprinted .thermal.<hash>.npy
    sidecar on every subsequent point click for that same photo (with
    those same params — a different params_dict gets its own cache entry,
    it never overwrites another one)."""
    params_dict = params_dict or {}
    cache_path = f'{image_abs_path}.thermal.{_calibration_fingerprint(params_dict)}.npy'
    if os.path.exists(cache_path):
        return np.load(cache_path)
    matrix = _decode_full_matrix(image_abs_path, params_dict)
    try:
        np.save(cache_path, matrix)
    except OSError:
        pass  # cache is an optimization, not a requirement
    return matrix


def get_raw_matrix(image_abs_path):
    """The RAW sensor reading behind every temperature — the number DJI's
    calibration in _decode_full_matrix() actually converts into °C. Same
    (rows, cols) shape/indexing as get_temperature_matrix(), read directly
    from the R-JPEG's embedded APP3 marker segments (pure file parsing —
    no SDK call, doesn't depend on THERMAL_SDK_DIR being configured at
    all). Cached separately since it never changes with recalibration —
    unlike the temperature matrix, there's no fingerprint in this cache's
    filename because nothing here depends on MEASUREMENT_PARAMS."""
    cache_path = image_abs_path + '.thermal_raw.npy'
    if os.path.exists(cache_path):
        return np.load(cache_path)

    with Image.open(image_abs_path) as im:
        applist = getattr(im, 'applist', None)
        raw_bytes = b''.join(content for tag, content in (applist or []) if tag == 'APP3')
    if not raw_bytes:
        raise ThermalDecodeError('No APP3 segments found — could not read the raw sensor values for this photo.')

    # Match against whatever resolution the SDK itself reported for this
    # photo, rather than assuming a fixed sensor size — different camera
    # models use different resolutions (640x512 vs others).
    temp_matrix = get_temperature_matrix(image_abs_path)
    rows, cols = temp_matrix.shape
    expected_bytes = rows * cols * 2
    if len(raw_bytes) != expected_bytes:
        raise ThermalDecodeError(
            f'Raw data size ({len(raw_bytes)} bytes) does not match the {cols}x{rows} resolution SDK reported '
            f'({expected_bytes} bytes expected) — cannot reliably map raw values to the same pixel grid.'
        )
    raw = np.frombuffer(raw_bytes, dtype='<u2').reshape((rows, cols)).astype(np.float64)

    try:
        np.save(cache_path, raw)
    except OSError:
        pass
    return raw


def get_point_temperature(image_abs_path, x_pct, y_pct, params_dict=None):
    """x_pct/y_pct: 0-100, same convention as TowerDefect.shape_coords.
    Returns (temperature_c: float | None, error: str)."""
    avg_c, _min_c, _max_c, _raw_avg, _raw_min, _raw_max, error = get_shape_stats(image_abs_path, 'point', [(x_pct, y_pct)], params_dict)
    return avg_c, error


def get_shape_stats(image_abs_path, shape_type, coords_pct, params_dict=None):
    """shape_type: 'point' (1 coord), 'rect' or 'line' (2 coords — corners
    or endpoints respectively). coords_pct: list of (x_pct, y_pct) tuples,
    0-100, same convention as TowerDefect.shape_coords. params_dict:
    optional overrides for distance/humidity/emissivity/reflection/
    ambient_temp — any key not given falls back to DEFAULT_PARAMS_DICT.

    Returns (avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error) — all
    None except error if decoding fails. For 'point', avg_c == min_c ==
    max_c (a single reading, not a range) — same for the raw_* values.
    raw_* is the actual sensor number DJI's calibration converted into
    avg_c/min_c/max_c — it can come back None even when the temperature
    succeeds (raw extraction is a separate, simpler read of the same file
    and can fail independently; that's not treated as fatal since the
    temperature reading itself is still valid and more useful)."""
    try:
        matrix = get_temperature_matrix(image_abs_path, params_dict)
    except ThermalSDKNotConfigured as e:
        return None, None, None, None, None, None, str(e)
    except ThermalDecodeError as e:
        return None, None, None, None, None, None, str(e)
    except Exception as e:  # noqa: BLE001 — surface anything unexpected to the UI rather than 500ing
        return None, None, None, None, None, None, f'Unexpected decode error: {e}'

    try:
        raw_matrix = get_raw_matrix(image_abs_path)
    except Exception:  # noqa: BLE001 — raw value is a bonus, not essential; don't fail the whole measurement over it
        raw_matrix = None

    rows, cols = matrix.shape

    def to_px(pt):
        x_pct, y_pct = pt
        col = min(cols - 1, max(0, int(round((x_pct / 100.0) * (cols - 1)))))
        row = min(rows - 1, max(0, int(round((y_pct / 100.0) * (rows - 1)))))
        return row, col

    def raw_stats(region_slice):
        if raw_matrix is None:
            return None, None, None
        r = raw_matrix[region_slice]
        if r.size == 0:
            return None, None, None
        return float(r.mean()), float(r.min()), float(r.max())

    if shape_type == 'point':
        if len(coords_pct) < 1:
            return None, None, None, None, None, None, 'Point measurement needs 1 coordinate.'
        row, col = to_px(coords_pct[0])
        v = float(matrix[row, col])
        ra, rmn, rmx = raw_stats((slice(row, row + 1), slice(col, col + 1)))
        return v, v, v, ra, rmn, rmx, ''

    if len(coords_pct) < 2:
        return None, None, None, None, None, None, f'{shape_type} measurement needs 2 coordinates.'
    r1, c1 = to_px(coords_pct[0])
    r2, c2 = to_px(coords_pct[1])

    if shape_type == 'rect':
        r_lo, r_hi = sorted((r1, r2))
        c_lo, c_hi = sorted((c1, c2))
        region_slice = (slice(r_lo, r_hi + 1), slice(c_lo, c_hi + 1))
        region = matrix[region_slice]
        if region.size == 0:
            region_slice = (slice(r_lo, r_lo + 1), slice(c_lo, c_lo + 1))
            region = matrix[region_slice]
        ra, rmn, rmx = raw_stats(region_slice)
        return float(region.mean()), float(region.min()), float(region.max()), ra, rmn, rmx, ''

    if shape_type == 'line':
        n = max(abs(r2 - r1), abs(c2 - c1), 1) + 1
        rows_s = np.clip(np.round(np.linspace(r1, r2, n)).astype(int), 0, rows - 1)
        cols_s = np.clip(np.round(np.linspace(c1, c2, n)).astype(int), 0, cols - 1)
        vals = matrix[rows_s, cols_s]
        ra = rmn = rmx = None
        if raw_matrix is not None:
            raw_vals = raw_matrix[rows_s, cols_s]
            ra, rmn, rmx = float(raw_vals.mean()), float(raw_vals.min()), float(raw_vals.max())
        return float(vals.mean()), float(vals.min()), float(vals.max()), ra, rmn, rmx, ''

    return None, None, None, None, None, None, f'Unknown shape_type: {shape_type}'


def sdk_health():
    """Diagnostic used by the admin-only /api/thermal/health endpoint.
    Reports whether the native DJI Thermal SDK can be loaded on THIS
    machine and, if not, the exact reason — so a thermal failure on a
    deployed server can be diagnosed without digging through logs. Note:
    this only checks that the SDK loads; a photo can still fail to decode
    if it isn't a genuine radiometric R-JPEG (see get_shape_stats errors)."""
    lib = _load_lib()
    lib_path = os.path.join(THERMAL_SDK_DIR, _LIB_FILENAME) if THERMAL_SDK_DIR else ''
    return {
        'platform': platform.system(),
        'sdk_dir': THERMAL_SDK_DIR,
        'lib_filename': _LIB_FILENAME,
        'lib_file_present': bool(lib_path) and os.path.exists(lib_path),
        'sdk_loaded': lib is not None,
        'error': _lib_load_error,
    }


def _interpret_create_code(rc):
    """Human explanation for a dirp_create_from_rjpeg return code, for the
    per-photo probe below."""
    if rc == 0:
        return 'OK — valid radiometric R-JPEG.'
    if rc == -7:
        return ('Code -7 (R-JPEG parse): the SDK loaded fine but this file is not a parseable DJI '
                'radiometric R-JPEG. Usual causes: (1) the photo was re-saved/compressed somewhere '
                'between the drone and upload (WhatsApp, Google Photos, Windows Photos auto-rotate, a '
                'screenshot) which strips DJI\'s embedded thermal payload; (2) it is a colourised thermal '
                'preview with no radiometric data, not the real _T.JPG; or (3) it is from a camera model '
                'this SDK build does not support. Upload the original _T.JPG straight off the SD card.')
    return f'Code {rc}: the SDK could not process this file (see DJI dirp_api.h ret codes).'


def probe_rjpeg(image_abs_path):
    """Admin diagnostic for a single photo behind /api/thermal/probe/<id>.
    Inspects the STORED file and reports whether it is a genuine
    radiometric DJI R-JPEG — so a -7 failure can be pinned to the file
    itself (stripped/compressed/unsupported) rather than the SDK."""
    info = {'path_exists': os.path.exists(image_abs_path)}
    if not info['path_exists']:
        info['verdict'] = 'No saved image file at the expected path.'
        return info
    with open(image_abs_path, 'rb') as f:
        data = f.read()
    n = len(data)
    info['size_bytes'] = n
    info['is_jpeg'] = data[:2] == b'\xff\xd8'

    # Walk JPEG marker segments and list the APPn blocks (DJI's thermal
    # payload lives in APP markers; a re-saved file loses them).
    apps = []
    has_dji = False
    i = 2
    while i + 4 <= n and data[i] == 0xFF:
        marker = data[i + 1]
        if marker in (0xD9,):            # EOI
            break
        if marker == 0xDA:               # SOS — compressed scan follows
            break
        if 0xD0 <= marker <= 0xD7 or marker == 0x01:  # RSTn / TEM: no length
            i += 2
            continue
        if i + 4 > n:
            break
        seglen = int.from_bytes(data[i + 2:i + 4], 'big')
        if 0xE0 <= marker <= 0xEF:
            seg = data[i + 4:i + 2 + seglen]
            apps.append(f'APP{marker - 0xE0} ({seglen} bytes)')
            if b'DJI' in seg[:64]:
                has_dji = True
        i += 2 + seglen
    info['app_segments'] = apps
    info['has_dji_marker'] = has_dji

    eoi = data.rfind(b'\xff\xd9')
    info['bytes_after_eoi'] = (n - (eoi + 2)) if eoi != -1 else None

    try:
        with Image.open(image_abs_path) as im:
            applist = getattr(im, 'applist', None) or []
        info['app3_thermal_bytes'] = sum(len(c) for t, c in applist if t == 'APP3')
    except Exception:
        info['app3_thermal_bytes'] = None

    lib = _load_lib()
    if lib is None:
        info['sdk_loaded'] = False
        info['sdk_error'] = _lib_load_error
        info['create_code'] = None
        info['verdict'] = 'Thermal SDK is not loading on this server — see /api/thermal/health.'
        return info
    info['sdk_loaded'] = True
    handle = ctypes.c_void_p()
    rc = lib.dirp_create_from_rjpeg(data, len(data), ctypes.byref(handle))
    info['create_code'] = rc
    if rc == 0:
        res = DirpResolution()
        if lib.dirp_get_rjpeg_resolution(handle, ctypes.byref(res)) == 0:
            info['thermal_resolution'] = f'{res.width}x{res.height}'
        lib.dirp_destroy(handle)
    info['verdict'] = _interpret_create_code(rc)
    return info
