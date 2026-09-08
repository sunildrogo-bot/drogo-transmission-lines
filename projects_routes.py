"""
projects_routes.py — Blueprint for all dynamic Project / Division / Line data.

Replaces the old hardcoded MPPTCL / DVC project cards with a database-backed
system shared by every module:

    Module ("Transmission Line", "Land Survey", ...)
      └── Project   (+ Add Project: name, contact no, email, country, state, logo)
            └── Division   (Transmission Line only — + Add Division: name, lat, lng)
                  └── Line     (+ Add Line: name, start/end lat-lng, length, towers, KML)

Register in app.py with: app.register_blueprint(projects_bp)
"""
import os
import shutil
import math
import json
import re
import hashlib
import csv
import io
import numpy as np
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app, Response, send_file
from werkzeug.utils import secure_filename
from sqlalchemy.orm import selectinload
from models import db, Project, Division, Line, ActivityLog, TowerPhoto, TowerDefect, DefectResolutionEvent, DefectAnnotationEvent, InspectionComponent, InspectionDefectType, TowerReport, AiInspectionSummary, User, TowerInspectionStatus, PilotAssignment, ThermalPoint, CorridorPhoto, SmeAssignment, PilotLocation, UploadBatch, UploadBatchItem
from access_control import (
    can_access_division,
    can_access_line,
    can_access_photo,
    can_access_project,
    client_can_access_tower,
    has_module_access,
    visible_line_ids,
    visible_project_ids,
)
import settings as app_settings
import thermal_decode
from storage_cleanup import (
    collect_division_files,
    collect_line_files,
    collect_project_files,
    delete_stored_files,
)


def _storage_working_path(value):
    """Return a local cache path for local or object-backed application files."""
    if not value:
        return ''
    try:
        from storage_service import get_storage
        return get_storage().local_working_path(value)
    except (OSError, RuntimeError, ValueError):
        return ''
from notification_service import admin_user_ids, notify_user, notify_users
from storage_service import get_storage, stored_url

projects_bp = Blueprint('projects_bp', __name__)

LOGO_EXTS  = {'jpg', 'jpeg', 'png'}
KML_EXTS   = {'kml', 'kmz'}
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}

UPLOAD_BASE = os.path.join('static', 'uploads')


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


def _admin_guard():
    """Blocks write actions for anyone whose active session role isn't
    Admin — tower photo upload/defect marking/deletion is Admin-only;
    Client User sessions get view-only access."""
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Your account has view-only access to this module.'}), 403
    return None


def _cleanup_deleted_files(stored_paths, entity_label):
    """Remove tracked files after the owning database rows are committed."""
    cleanup = delete_stored_files(stored_paths)
    if cleanup['errors']:
        current_app.logger.warning(
            'File cleanup after deleting %s was incomplete: %s',
            entity_label,
            cleanup['errors'],
        )
    return cleanup


def _admin_or_client_guard():
    """For actions Client sessions get a real write ability for, unlike
    everything else in this module — right now just closing a defect
    once it's been rectified in the field, since that's a real-world
    fact both the client and Drogo need to be able to record."""
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') not in ('Admin', 'Client User'):
        return jsonify({'error': 'This action is only available to Admin and Client accounts.'}), 403
    return None


def _pilot_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Pilot':
        return jsonify({'error': 'This is only available to Pilot accounts.'}), 403
    return None


def _sme_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'SME':
        return jsonify({'error': 'This is only available to SME accounts.'}), 403
    return None


def _inspect_guard(line_id):
    """Admin can always inspect (mark defects, take thermal measurements,
    mark inspection done, generate reports); an SME can do the same, but
    only on a Line they've actually been assigned — this is the
    per-action enforcement that backs that up (the UI-level "can this
    role see the inspection tools" check is broader/simpler — see
    can_inspect in project_map() — but this is what actually decides
    whether a given write succeeds)."""
    guard = _login_guard()
    if guard:
        return guard
    role = session.get('role')
    if role == 'Admin':
        return None
    if role == 'SME':
        assigned = SmeAssignment.query.filter_by(line_id=line_id, sme_user_id=session.get('user_id')).first()
        if assigned:
            return None
        return jsonify({'error': "You haven't been assigned to this line."}), 403
    return jsonify({'error': 'Your account has view-only access to this module.'}), 403


def _inspect_guard_for_photo(tower_photo_id):
    """Same as _inspect_guard, but resolves the Line from a TowerPhoto id
    first — for routes (defect/thermal-point create+delete) that only
    have a photo id to work with, not a line id directly."""
    photo = TowerPhoto.query.get(tower_photo_id)
    if not photo:
        return jsonify({'error': 'Photo not found.'}), 404
    return _inspect_guard(photo.line_id)


def _visible_project_ids(module_name=None):
    """Compatibility wrapper used by routes and the Gemini assistant."""
    return visible_project_ids(module_name)


def _project_access_guard(project):
    """For direct-URL access to a specific project's pages (map/overview/
    info) — the list endpoint filtering above only helps if someone
    actually goes through the list; this closes the gap for anyone who
    has (or guesses) a direct link to a project they're not allowed to
    see. Returns a Flask response to abort with, or None if access is OK."""
    if not can_access_project(project):
        return jsonify({'error': "You don't have access to this project."}), 403
    return None


# ── Project-access guards for the JSON API ─────────────────────────────────────
# The page routes (map/overview/info) call _project_access_guard directly, but
# most /api/... data endpoints take a division/line/photo/report id and only
# checked _login_guard — so a Client User restricted to one project could read
# another project's data by guessing ids (IDOR). These resolve whatever id the
# endpoint has up to its owning Project and apply the same project-access check.
# Admin bypasses these checks. Client access is project-based; Pilot and SME
# access is derived from exact line assignments. Call at the top of an endpoint:
#     guard = _line_access_guard(line_id)
#     if guard: return guard
def _project_access_guard_by_id(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({'error': 'Project not found.'}), 404
    return _project_access_guard(project)


def _division_access_guard(division_id):
    division = Division.query.get(division_id)
    if not division:
        return jsonify({'error': 'Division not found.'}), 404
    if not can_access_division(division):
        return jsonify({'error': "You don't have access to this division."}), 403
    return None


def _line_access_guard(line_id):
    line = Line.query.get(line_id)
    if not line:
        return jsonify({'error': 'Line not found.'}), 404
    if not can_access_line(line):
        return jsonify({'error': "You haven't been assigned access to this line."}), 403
    return None


def _photo_access_guard(photo_id):
    photo = TowerPhoto.query.get(photo_id)
    if not photo:
        return jsonify({'error': 'Photo not found.'}), 404
    if not can_access_photo(photo):
        return jsonify({'error': "You don't have access to this photo."}), 403
    return None


def _client_tower_release_guard(line_id, tower_label):
    if not client_can_access_tower(line_id, tower_label):
        return jsonify({'error': 'Inspection results for this tower have not been released yet.'}), 403
    return None


def _client_photo_release_guard(photo):
    return _client_tower_release_guard(photo.line_id, photo.tower_label)


def _module_access_guard(module_name):
    if not module_name:
        return None
    if not has_module_access(module_name):
        return jsonify({'error': "You don't have access to this module."}), 403
    return None


def _visible_division_dict(division):
    data = division.to_dict()
    if session.get('role') in ('Pilot', 'SME'):
        data['line_count'] = sum(1 for line in division.lines if can_access_line(line))
    return data


def _visible_project_dict(project):
    data = project.to_dict()
    if session.get('role') in ('Pilot', 'SME'):
        lines = [line for division in project.divisions for line in division.lines if can_access_line(line)]
        data['line_count'] = len(lines)
        data['division_count'] = len({line.division_id for line in lines})
    return data


def _ext_ok(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


def _validate_uploaded_image(file_storage, allowed_types=None):
    """Validate an image without changing or storing it.

    Returns normalized metadata plus warnings. Hard failures are reserved for
    unreadable/unsupported files; incomplete metadata remains an explicit
    warning so Admin can still upload legitimate processed imagery.
    """
    from PIL import Image, ImageOps
    warnings = []
    filename = file_storage.filename or ''
    stem = re.sub(r'\.[^.]+$', '', filename)
    filename_thermal = bool(re.search(r'_T(_\S+)?$', stem, re.IGNORECASE))
    try:
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as image:
            image.verify()
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as image:
            width, height = ImageOps.exif_transpose(image).size
            exif = image.getexif()
            captured_raw = exif.get(36867) or exif.get(306)
    except Exception as exc:
        file_storage.stream.seek(0)
        return {'valid': False, 'status': 'Invalid', 'error': f'Image is unreadable or corrupted: {str(exc)[:180]}'}
    finally:
        file_storage.stream.seek(0)

    if width < 640 or height < 480:
        warnings.append(f'Low resolution ({width}×{height}).')
    captured_at = None
    if captured_raw:
        try:
            captured_at = datetime.strptime(str(captured_raw), '%Y:%m:%d %H:%M:%S')
        except (TypeError, ValueError):
            warnings.append('Capture date is present but unreadable.')
    else:
        warnings.append('Capture date/time metadata is missing.')

    media_type = 'thermal' if filename_thermal else 'rgb'
    if allowed_types and media_type not in allowed_types:
        return {'valid': False, 'status': 'Wrong Type', 'error':
                f'{media_type.upper()} image is not enabled for this project.'}
    if filename_thermal:
        warnings.append('Thermal classification will be confirmed from radiometric data when measured.')
    return {
        'valid': True, 'status': 'Warning' if warnings else 'Ready',
        'warnings': warnings, 'media_type': media_type,
        'width': width, 'height': height, 'captured_at': captured_at,
    }


def _save_upload(file_storage, subfolder, allowed_exts):
    """Save an uploaded file under static/uploads/<subfolder>/ and return the
    path relative to /static (or '' if no valid file was supplied)."""
    if not file_storage or not file_storage.filename:
        return ''
    if not _ext_ok(file_storage.filename, allowed_exts):
        return None  # signals an invalid file type to the caller
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    folder_fs = os.path.join(base_dir, UPLOAD_BASE, subfolder)
    os.makedirs(folder_fs, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    # avoid collisions
    name_root, name_ext = os.path.splitext(safe_name)
    final_name = safe_name
    i = 1
    while (os.path.exists(os.path.join(folder_fs, final_name)) or
           get_storage().exists(f"uploads/{subfolder}/{final_name}")):
        final_name = f"{name_root}_{i}{name_ext}"
        i += 1
    full_path = os.path.join(folder_fs, final_name)
    file_storage.save(full_path)
    stored = f"uploads/{subfolder}/{final_name}"
    get_storage().publish(stored, full_path)
    return stored


def _generate_thumbnail(base_dir, subfolder_raw, filename):
    """Create a high-quality grid thumbnail without touching the original."""
    from PIL import Image, ImageOps
    raw_dir = os.path.normpath(os.path.join(base_dir, UPLOAD_BASE, subfolder_raw))
    raw_full_path = os.path.join(raw_dir, filename)
    thumb_dir = os.path.join(os.path.dirname(raw_dir), 'thumb')
    thumb_subfolder = os.path.relpath(thumb_dir, os.path.join(base_dir, UPLOAD_BASE)).replace('\\', '/')
    thumb_name = secure_filename(filename) + '.thumb.jpg'
    thumb_full_path = os.path.join(thumb_dir, thumb_name)
    temp_path = thumb_full_path + '.tmp'
    try:
        os.makedirs(thumb_dir, exist_ok=True)
        with Image.open(raw_full_path) as im:
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((800, 800), Image.Resampling.LANCZOS)
            im.save(temp_path, 'JPEG', quality=90, optimize=True)
        os.replace(temp_path, thumb_full_path)
        stored = f"uploads/{thumb_subfolder}/{thumb_name}"
        get_storage().publish(stored, thumb_full_path)
        return stored
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return None


def _generate_flat_thumbnail(base_dir, flat_subfolder, filename):
    """Same idea as _generate_thumbnail, for photo types stored in a
    single flat folder (corridor photos) rather than the nested
    project/division/line/tower/raw layout — thumbnails go in a sibling
    '<folder>_thumb' folder instead of assuming a '/raw' suffix to swap,
    so there's no risk of a thumbnail colliding with (and overwriting)
    its own raw source file."""
    from PIL import Image, ImageOps
    raw_full_path = os.path.join(base_dir, UPLOAD_BASE, flat_subfolder, filename)
    thumb_subfolder = f"{flat_subfolder}_thumb"
    thumb_dir = os.path.join(base_dir, UPLOAD_BASE, thumb_subfolder)
    thumb_name = secure_filename(filename) + '.thumb.jpg'
    thumb_full_path = os.path.join(thumb_dir, thumb_name)
    temp_path = thumb_full_path + '.tmp'
    try:
        os.makedirs(thumb_dir, exist_ok=True)
        with Image.open(raw_full_path) as im:
            im = ImageOps.exif_transpose(im).convert('RGB')
            im.thumbnail((800, 800), Image.Resampling.LANCZOS)
            im.save(temp_path, 'JPEG', quality=90, optimize=True)
        os.replace(temp_path, thumb_full_path)
        stored = f"uploads/{thumb_subfolder}/{thumb_name}"
        get_storage().publish(stored, thumb_full_path)
        return stored
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return None


def _slug(text, fallback='unknown'):
    """Filesystem-safe folder name from a project/division/line/tower
    name — lowercase, spaces to underscores, anything else stripped.
    Never empty, so a folder path never ends up with a blank segment."""
    text = (text or '').strip().lower()
    text = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
    return text or fallback


def _save_tower_photo(file_storage, project, division, line, tower_label, allowed_exts, kind='raw'):
    """Save an uploaded tower photo under the project's own folder tree:
    uploads/tower_photos/<project>/<division>/<line>/<tower>/<kind>/<file>
    kind is 'raw' for the photo as uploaded, or 'defects' for the
    duplicate copy made when a defect gets marked on it (see
    _duplicate_photo_for_defects) — same tree, sibling folders, so a
    raw/ cleanup never touches anything under defects/.
    Returns (path, thumbnail_path), each relative to /static — or the
    same None/'' pair _save_upload uses for invalid type / no file.
    thumbnail_path is only ever generated for kind='raw' (nothing else
    needs its own thumbnail); it's None if generation failed or wasn't
    attempted — callers should treat that as "no thumbnail" and move on,
    not fail the whole upload over it."""
    if not file_storage or not file_storage.filename:
        return '', None
    if not _ext_ok(file_storage.filename, allowed_exts):
        return None, None
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    subfolder = os.path.join(
        'tower_photos', _slug(project.name if project else None),
        _slug(division.name if division else None), _slug(line.name if line else None),
        _slug(tower_label), kind,
    )
    folder_fs = os.path.join(base_dir, UPLOAD_BASE, subfolder)
    os.makedirs(folder_fs, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    name_root, name_ext = os.path.splitext(safe_name)
    final_name = safe_name
    i = 1
    while (os.path.exists(os.path.join(folder_fs, final_name)) or
           get_storage().exists(f"uploads/{subfolder.replace(os.sep, '/')}/{final_name}")):
        final_name = f"{name_root}_{i}{name_ext}"
        i += 1
    full_path = os.path.join(folder_fs, final_name)
    file_storage.save(full_path)
    stored_path = f"uploads/{subfolder}/{final_name}".replace('\\', '/')
    get_storage().publish(stored_path, full_path)

    thumb_path = None
    if kind == 'raw':
        thumb_path = _generate_thumbnail(base_dir, subfolder, final_name)
        if thumb_path:
            thumb_path = thumb_path.replace('\\', '/')

    return stored_path, thumb_path


def _duplicate_photo_for_defects(photo):
    """Copies a TowerPhoto's raw file into that same tower's defects/
    folder the first time a defect is marked on it — a real second copy
    on disk, not just a second DB reference to the same file. This is
    what lets raw/ get cleaned up later without losing the evidence a
    marked defect depends on. Idempotent: does nothing if already copied
    (photo.defect_copy_path already set) or if the raw file is missing.
    Returns True if a copy exists after this call (whether just made or
    already there), False if it couldn't be made."""
    if photo.defect_copy_path:
        return True
    if not photo.image_path:
        return False
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    src = _storage_working_path(photo.image_path)
    if not src:
        return False
    raw_dir, filename = os.path.split(photo.image_path)
    # .../<tower>/raw  ->  .../<tower>/defects
    defects_rel_dir = re.sub(r'/raw$', '/defects', raw_dir)
    if defects_rel_dir == raw_dir:
        # Old flat-path photos (uploaded before this folder structure
        # existed) have no /raw suffix to swap — give them their own
        # defects/ sibling next to wherever they actually live instead.
        defects_rel_dir = raw_dir + '_defects'
    dest_dir = os.path.join(base_dir, 'static', defects_rel_dir)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, filename)
    if not os.path.isfile(dest):
        shutil.copy2(src, dest)
    photo.defect_copy_path = f"{defects_rel_dir}/{filename}"
    get_storage().publish(photo.defect_copy_path, dest)
    return True


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Photos attached to a tower must be taken within this many meters of it —
# a generous buffer for normal GPS inaccuracy, not a tight survey
# tolerance. A photo with no GPS EXIF data at all is rejected too (see
# api_upload_tower_photo) — drone photos reliably carry it, so a photo
# without it can't be verified as belonging to this tower.
TOWER_PHOTO_GPS_BUFFER_M = 70


def _extract_gps_from_image(file_storage):
    """Best-effort read of (lat, lng) from an uploaded image's EXIF GPS
    tags. Returns None if the image has no GPS data, isn't a format Pillow
    can read EXIF from, or anything else goes wrong — this is advisory
    validation, not something that should ever crash the upload.

    Uses Image.getexif() + get_ifd(GPSInfo) — Pillow's current, documented
    way to read the GPS IFD. The older/legacy Image._getexif() approach
    (which just returns whatever's under the 'GPSInfo' key of its flat
    dict) doesn't reliably decode the nested GPS IFD across Pillow
    versions — it can come back as a bare IFD offset/reference rather than
    the actual decoded tag dict, silently failing to find real GPS data
    even in images (like standard drone photos) that do have it."""
    try:
        from PIL import Image, ExifTags
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        exif = img.getexif()
        file_storage.stream.seek(0)  # rewind so _save_upload can still read the full file
        if not exif:
            return None
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if not gps_ifd:
            return None
        gps = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

        def _to_degrees(value):
            d, m, s = value[0], value[1], value[2]
            return float(d) + float(m) / 60.0 + float(s) / 3600.0

        if 'GPSLatitude' not in gps or 'GPSLongitude' not in gps:
            return None
        lat = _to_degrees(gps['GPSLatitude'])
        if gps.get('GPSLatitudeRef') != 'N':
            lat = -lat
        lng = _to_degrees(gps['GPSLongitude'])
        if gps.get('GPSLongitudeRef') != 'E':
            lng = -lng
        return (lat, lng)
    except Exception:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return None


@projects_bp.route('/api/me', methods=['GET'])
def api_my_profile():
    """The logged-in user's own basic info — for a simple "my profile"
    view (e.g. SME's dashboard, which has no admin-style settings page
    to check this from). Never exposes anyone else's info; there's no
    id parameter, just whoever's actually signed in."""
    guard = _login_guard()
    if guard:
        return guard
    user = User.query.get(session.get('user_id'))
    if not user:
        return jsonify({'error': 'Not found.'}), 404
    return jsonify({
        'username': user.username,
        'email': user.email,
        'contact': user.contact or '',
        'role': session.get('role', ''),
        'member_since': user.created_at.strftime('%d %b %Y') if user.created_at else '',
        'last_login': user.last_login or 'Never',
    })


# ── Projects ────────────────────────────────────────────────────────────────

@projects_bp.route('/api/projects', methods=['GET'])
def api_list_projects():
    guard = _login_guard()
    if guard:
        return guard
    module = request.args.get('module', '')
    guard = _module_access_guard(module)
    if guard:
        return guard
    q = Project.query
    if module:
        q = q.filter_by(module=module)
    projects = q.order_by(Project.created_at.asc()).all()

    allowed_ids = _visible_project_ids(module or None)
    if allowed_ids is not None:
        projects = [p for p in projects if p.id in allowed_ids]

    return jsonify({'projects': [_visible_project_dict(p) for p in projects]})


@projects_bp.route('/api/projects', methods=['POST'])
def api_create_project():
    guard = _admin_guard()
    if guard:
        return guard

    form = request.form
    name = (form.get('name') or '').strip()
    module = (form.get('module') or '').strip()
    email = (form.get('email') or '').strip()

    if not name:
        return jsonify({'error': 'Project name is required.'}), 400
    if not module:
        return jsonify({'error': 'Module is required.'}), 400
    if email and '@' not in email:
        return jsonify({'error': 'Invalid email address.'}), 400

    # Multi-select — a project can do RGB only, Thermal only, or both.
    # Sent as repeated form fields (checkboxes); default to both if
    # nothing was actually picked, so this never silently produces a
    # project with no inspection type at all.
    raw_types = request.form.getlist('inspection_types')
    inspection_types = sorted({t for t in raw_types if t in ('rgb', 'thermal')}) or ['rgb', 'thermal']

    logo_path = ''
    logo_file = request.files.get('logo')
    if logo_file and logo_file.filename:
        saved = _save_upload(logo_file, 'logos', LOGO_EXTS)
        if saved is None:
            return jsonify({'error': 'Company logo must be a .jpg or .png file.'}), 400
        logo_path = saved

    def _int_or_none(raw):
        raw = (raw or '').strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    project = Project(
        module=module,
        name=name,
        contact_no=(form.get('contact_no') or '').strip(),
        email=email,
        country=(form.get('country') or '').strip(),
        state=(form.get('state') or '').strip(),
        logo_path=logo_path,
        client_name=(form.get('client_name') or '').strip(),
        planned_divisions=_int_or_none(form.get('planned_divisions')),
        planned_towers=_int_or_none(form.get('planned_towers')),
        timeline=(form.get('timeline') or '').strip(),
        inspection_types=json.dumps(inspection_types),
        created_by=session.get('user_id'),
    )
    db.session.add(project)
    db.session.commit()
    return jsonify(project.to_dict()), 201


@projects_bp.route('/api/projects/<int:project_id>', methods=['GET'])
def api_get_project(project_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _project_access_guard_by_id(project_id)
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    data = _visible_project_dict(project)
    data['divisions'] = [_visible_division_dict(d) for d in project.divisions if can_access_division(d)]
    return jsonify(data)


@projects_bp.route('/api/projects/<int:project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    guard = _admin_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    name, module = project.name, project.module
    stored_paths = collect_project_files(project_id)
    db.session.delete(project)
    ActivityLog.log(action='delete', entity_type='Project', entity_name=name,
                     module=module, performed_by=session.get('user_name', ''))
    db.session.commit()
    _cleanup_deleted_files(stored_paths, f'project {project_id}')
    return jsonify({'deleted': project_id})


# ── Project map page (dynamic Transmission Line view) ────────────────────────

@projects_bp.route('/projects/<int:project_id>/map')
def project_map(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard

    # The map is a division workspace, never a project-wide browser.  Older
    # links opened /projects/<id>/map without any scope, which made the UI
    # switch back to the legacy combined divisions + lines view.  Require a
    # division explicitly, or derive it from a trusted line deep-link (SME
    # assignments and notifications use ?line=<id>).
    selected_division = None
    line_id = request.args.get('line', type=int)
    division_id = request.args.get('division', type=int)
    if line_id:
        selected_line = Line.query.get(line_id)
        if (not selected_line or not selected_line.division
                or selected_line.division.project_id != project.id
                or not can_access_line(selected_line)):
            return redirect(url_for('projects_bp.project_divisions', project_id=project.id))
        selected_division = selected_line.division
    elif division_id:
        candidate = Division.query.get(division_id)
        if (not candidate or candidate.project_id != project.id
                or not can_access_division(candidate)):
            return redirect(url_for('projects_bp.project_divisions', project_id=project.id))
        selected_division = candidate
    else:
        return redirect(url_for('projects_bp.project_divisions', project_id=project.id))

    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'
    # SME gets the same inspection tools (marking, thermal measurement,
    # corridor viewing, report generation) as Admin, just never the
    # upload buttons — those stay gated on is_admin specifically. This is
    # a page-wide flag for simplicity; the actual write actions are still
    # checked per-line against that SME's real assignment server-side
    # (see _inspect_guard) — an SME who isn't assigned to a given line
    # will see the tools but have their save rejected, rather than the
    # tools being hidden per-line here too.
    can_inspect = is_admin or session.get('role') == 'SME'
    ActivityLog.log(action='enter_project', entity_type='Project', entity_name=project.name,
                     module=project.module, performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return render_template('project_map.html', project=project, user_name=session.get('user_name', ''),
                            back_endpoint=back_endpoint, is_admin=is_admin, can_inspect=can_inspect,
                            selected_division=selected_division)


@projects_bp.route('/api/thermal/health')
def api_thermal_health():
    """Admin-only diagnostic for 'thermal isn't working': reports whether
    the native DJI Thermal SDK loads on this server and the exact error if
    not. The stored photo originals are never recompressed, so a thermal
    failure is almost always either the SDK not loading here or a photo
    that isn't a genuine radiometric R-JPEG."""
    guard = _admin_guard()
    if guard:
        return guard
    try:
        from thermal_decode import sdk_health
        return jsonify(sdk_health())
    except Exception as e:  # noqa: BLE001
        return jsonify({'sdk_loaded': False, 'error': f'{type(e).__name__}: {e}'}), 500


@projects_bp.route('/api/thermal/probe/<int:photo_id>')
def api_thermal_probe(photo_id):
    """Admin-only per-photo diagnostic for a thermal decode failure (e.g.
    dirp_create_from_rjpeg code -7). Inspects the stored file and reports
    whether it's a genuine radiometric R-JPEG or a plain/stripped JPEG."""
    guard = _admin_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    try:
        from thermal_decode import probe_rjpeg
        path = _storage_working_path(photo.image_path)
        info = probe_rjpeg(path)
        info['filename'] = os.path.basename(photo.image_path or '')
        return jsonify(info)
    except Exception as e:  # noqa: BLE001
        return jsonify({'error': f'{type(e).__name__}: {e}'}), 500


@projects_bp.route('/projects/<int:project_id>/divisions')
def project_divisions(project_id):
    """Division-cards page that sits between the project card and the map:
    project card -> divisions -> (pick a division) -> map, where lines are
    added. Divisions are created here rather than inside the map."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'
    return render_template('project_divisions.html', project=project,
                            user_name=session.get('user_name', ''),
                            back_endpoint=back_endpoint, is_admin=is_admin)


def _build_project_defect_summary(project):
    """Every stat, chart-ready number, and grouped defect list needed for
    both the web Overview page and the PDF report — computed once here so
    the two can never drift out of sync with each other. Web-only
    presentation details (the CSS conic-gradient string, bar-chart
    percentages) are layered on top of this in project_overview() rather
    than baked in here, since the PDF report has no use for them."""
    all_divisions = project.divisions
    allowed_line_ids = visible_line_ids()
    lines = [
        line for division in all_divisions for line in division.lines
        if allowed_line_ids is None or line.id in allowed_line_ids
    ]
    visible_line_id_set = {line.id for line in lines}
    if session.get('role') in ('Pilot', 'SME'):
        divisions = [d for d in all_divisions if any(line.id in visible_line_id_set for line in d.lines)]
    else:
        divisions = all_divisions
    line_ids = [l.id for l in lines]

    # line_id -> (line, division), so each defect row can show which line
    # and division it came from without a query per row.
    line_lookup = {}
    for d in divisions:
        for l in d.lines:
            if l.id in visible_line_id_set:
                line_lookup[l.id] = (l, d)

    released_tower_keys = None
    if session.get('role') == 'Client User' and line_ids:
        released_tower_keys = {
            (status.line_id, status.tower_label)
            for status in TowerInspectionStatus.query.filter(
                TowerInspectionStatus.line_id.in_(line_ids),
                TowerInspectionStatus.inspection_done.is_(True),
            ).all()
        }

    defects = []
    if line_ids:
        defects = (TowerDefect.query
                   .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
                   .filter(TowerPhoto.line_id.in_(line_ids), TowerDefect.deleted_at.is_(None))
                   .order_by(TowerDefect.created_at.desc()).all())
        if released_tower_keys is not None:
            defects = [d for d in defects if (d.photo.line_id, d.photo.tower_label) in released_tower_keys]

    # Real inspection coverage — independent of whether a photo happens to
    # have a defect on it. A tower can be fully photographed and inspected
    # with nothing wrong found; the old computation below only counted a
    # tower as "photographed" if it also had a defect, silently missing
    # every clean tower in the number Admin/Client actually see.
    all_photos = TowerPhoto.query.filter(TowerPhoto.line_id.in_(line_ids)).all() if line_ids else []
    if released_tower_keys is not None:
        all_photos = [p for p in all_photos if (p.line_id, p.tower_label) in released_tower_keys]
    photos_uploaded = len(all_photos)
    photographed_tower_keys = {(p.line_id, p.tower_label) for p in all_photos}
    inspected_statuses = (TowerInspectionStatus.query
                          .filter(TowerInspectionStatus.line_id.in_(line_ids), TowerInspectionStatus.inspection_done.is_(True))
                          .all()) if line_ids else []
    towers_inspected = len(inspected_statuses)

    severity_counts = {'Critical': 0, 'Major': 0, 'Minor': 0}
    division_defect_counts = {}
    defect_type_counts = {}
    defect_rows = []
    for defect in defects:
        photo = defect.photo
        line, division = line_lookup.get(photo.line_id, (None, None))
        sev = defect.severity if defect.severity in severity_counts else 'Minor'
        severity_counts[sev] += 1
        div_name = division.name if division else '—'
        division_defect_counts[div_name] = division_defect_counts.get(div_name, 0) + 1
        dtype = defect.defect_type or 'Unspecified'
        defect_type_counts[dtype] = defect_type_counts.get(dtype, 0) + 1
        try:
            shape_coords = json.loads(defect.shape_coords) if defect.shape_coords else []
        except (TypeError, ValueError):
            shape_coords = []
        defect_rows.append({
            'id': defect.id,
            'component_name': defect.component_name,
            'defect_type': defect.defect_type,
            'severity': defect.severity,
            'location': defect.location,
            'status': defect.status,
            'resolution_status': defect.resolution_status or 'Open',
            'resolution_comment': defect.resolution_comment or '',
            'resolved_by': defect.resolved_by,
            'resolved_at': defect.resolved_at,
            'comments': defect.comments,
            'tower_label': photo.tower_label,
            'line_id': photo.line_id,
            'line_name': line.name if line else '—',
            'division_name': div_name,
            'created_at': defect.created_at,
            'created_by': defect.created_by,
            'image_path': photo.image_path or '',
            'image_url': stored_url(photo.image_path),
            'thumbnail_url': stored_url(photo.thumbnail_path or photo.display_image_path()),
            'shape_type': defect.shape_type,
            'shape_coords': shape_coords,
        })

    total_defects = len(defect_rows)

    # Per-division breakdown — lines, towers, and defects for each division
    # on its own, not just the project-wide totals. Also the same real
    # inspection-coverage numbers (KML towers / photos / photographed /
    # inspected), scoped to each division's own lines.
    division_stats = []
    for d in divisions:
        d_lines = [line for line in d.lines if line.id in visible_line_id_set]
        d_line_ids = [l.id for l in d_lines]
        d_towers = sum(l.tower_count or 0 for l in d_lines)
        d_photos = [p for p in all_photos if p.line_id in d_line_ids]
        d_photographed = len({(p.line_id, p.tower_label) for p in d_photos})
        d_inspected = sum(1 for s in inspected_statuses if s.line_id in d_line_ids)
        division_stats.append({
            'name': d.name,
            'line_count': len(d_lines),
            'tower_count': d_towers,
            'defect_count': division_defect_counts.get(d.name, 0),
            'photos_uploaded': len(d_photos),
            'towers_photographed': d_photographed,
            'towers_inspected': d_inspected,
        })

    # Defects grouped by tower (division -> line -> tower) — kept for
    # anything that still wants "everything found at this tower" framing.
    tower_groups_map = {}
    tower_group_order = []
    for row in defect_rows:
        key = (row['division_name'], row['line_name'], row['tower_label'])
        if key not in tower_groups_map:
            tower_groups_map[key] = []
            tower_group_order.append(key)
        tower_groups_map[key].append(row)
    tower_group_order.sort(key=lambda k: (k[0], k[1], k[2]))
    tower_groups = [
        {
            'division_name': key[0], 'line_name': key[1], 'tower_label': key[2],
            'defects': tower_groups_map[key],
        }
        for key in tower_group_order
    ]

    # Defects grouped by TYPE instead — "here's every Anti Climbing
    # Device defect, wherever it is" rather than "here's everything at
    # this tower". Each row keeps its own tower/division/line/location
    # since that's no longer implied by a shared group header. Ordered
    # by count (most-marked type first), matching the bar chart above it.
    type_groups_map = {}
    for row in defect_rows:
        key = row['defect_type'] or 'Unspecified'
        type_groups_map.setdefault(key, []).append(row)
    type_groups = [
        {'defect_type': key, 'defects': type_groups_map[key]}
        for key in sorted(type_groups_map.keys(), key=lambda k: -len(type_groups_map[k]))
    ]

    return {
        'division_count': len(divisions),
        'line_count': len(lines),
        'line_ids': line_ids,
        'tower_count': sum(l.tower_count or 0 for l in lines),
        'towers_photographed': len(photographed_tower_keys),
        'photos_uploaded': photos_uploaded,
        'towers_inspected': towers_inspected,
        'total_defects': total_defects,
        'severity_counts': severity_counts,
        'division_defect_counts': division_defect_counts,
        'defect_type_counts': defect_type_counts,
        'division_stats': division_stats,
        'defect_rows': defect_rows,
        'tower_groups': tower_groups,
        'type_groups': type_groups,
    }


def _build_project_chart_data(s):
    """Severity pie + division/type bar chart data, computed from a
    _build_project_defect_summary() result — shared by the Overview page
    and the client dashboard so both always show identical charts for
    the same project, never two slightly-different computations."""
    total_defects = s['total_defects']
    severity_counts = s['severity_counts']
    division_defect_counts = s['division_defect_counts']
    defect_type_counts = s['defect_type_counts']

    def _deg(n):
        return round((n / total_defects * 360), 2) if total_defects else 0
    crit_deg = _deg(severity_counts['Critical'])
    major_deg = _deg(severity_counts['Major'])
    if total_defects:
        severity_pie_gradient = (
            f"conic-gradient(var(--danger) 0deg {crit_deg}deg, "
            f"#e0a53a {crit_deg}deg {crit_deg + major_deg}deg, "
            f"var(--success) {crit_deg + major_deg}deg 360deg)"
        )
    else:
        severity_pie_gradient = 'conic-gradient(var(--border) 0deg 360deg)'

    max_div_count = max(division_defect_counts.values()) if division_defect_counts else 1
    division_bars = [
        {'name': name, 'count': count, 'pct': round(count / max_div_count * 100)}
        for name, count in sorted(division_defect_counts.items(), key=lambda kv: -kv[1])
    ]

    max_type_count = max(defect_type_counts.values()) if defect_type_counts else 1
    defect_type_bars = [
        {'name': name, 'count': count, 'pct': round(count / max_type_count * 100)}
        for name, count in sorted(defect_type_counts.items(), key=lambda kv: kv[0].lower())
    ]

    return {
        'severity_pie_gradient': severity_pie_gradient,
        'division_bars': division_bars,
        'defect_type_bars': defect_type_bars,
    }


def _build_project_activity_data(s, project_id, max_items=8):
    """Resolution progress + a 'Needs Attention' list (open Critical
    defects, oldest first — the ones that have been sitting the longest)
    + an aging breakdown for every open defect (not just Critical ones)
    + divisions ranked by defect density (defects per tower, not just
    raw count — a division with 40 towers and 5 defects is doing better
    than one with 5 towers and 5 defects, and a raw count alone hides
    that). All computed straight from defect_rows so it can never drift
    from what the charts above show."""
    from datetime import datetime as _dt

    rows = s['defect_rows']
    open_count = sum(1 for r in rows if (r.get('resolution_status') or 'Open') == 'Open')
    closed_count = sum(1 for r in rows if r.get('resolution_status') == 'Closed')
    total = open_count + closed_count
    resolution_pct = round(closed_count / total * 100) if total else 0

    # Not capped at max_items — the card scrolls internally now, so a
    # project with 40 open Critical defects shows all 40, oldest first,
    # rather than silently hiding anything past the first few.
    needs_attention = sorted(
        (r for r in rows if r['severity'] == 'Critical' and (r.get('resolution_status') or 'Open') == 'Open'),
        key=lambda r: r['created_at'] or _dt.min,
    )
    for r in needs_attention:
        r['type_url'] = f"/projects/{project_id}/defects/{r['defect_type'] or 'Unspecified'}"

    # How long has each still-open defect been sitting there? A defect
    # open 45 days is a very different story from one open 2 days, even
    # at the same severity — buckets make that visible at a glance.
    now = _dt.utcnow()
    aging = {'0-7': 0, '8-30': 0, '30+': 0}
    for r in rows:
        if (r.get('resolution_status') or 'Open') != 'Open' or not r['created_at']:
            continue
        days = (now - r['created_at']).days
        if days <= 7:
            aging['0-7'] += 1
        elif days <= 30:
            aging['8-30'] += 1
        else:
            aging['30+'] += 1

    # Density ranking — defects per tower actually covered so far, not
    # per tower planned, since a division still being photographed
    # shouldn't look artificially "clean" next to one that's finished.
    density_ranking = []
    for ds in s['division_stats']:
        covered = ds['towers_photographed']
        density = round(ds['defect_count'] / covered, 2) if covered else 0
        density_ranking.append({'name': ds['name'], 'defect_count': ds['defect_count'], 'towers_photographed': covered, 'density': density})
    density_ranking.sort(key=lambda d: -d['density'])

    return {
        'open_count': open_count,
        'closed_count': closed_count,
        'resolution_pct': resolution_pct,
        'needs_attention': needs_attention,
        'aging': aging,
        'density_ranking': density_ranking,
    }


@projects_bp.route('/projects/<int:project_id>/overview')
def project_overview(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'

    s = _build_project_defect_summary(project)
    total_defects = s['total_defects']
    severity_counts = s['severity_counts']

    chart_data = _build_project_chart_data(s)

    return render_template('project_overview.html',
        project=project, user_name=session.get('user_name', ''), is_admin=is_admin,
        back_endpoint=back_endpoint,
        division_count=s['division_count'], line_count=s['line_count'], tower_count=s['tower_count'],
        towers_photographed=s['towers_photographed'], photos_uploaded=s['photos_uploaded'],
        towers_inspected=s['towers_inspected'],
        total_defects=total_defects, severity_counts=severity_counts,
        severity_pie_gradient=chart_data['severity_pie_gradient'], division_bars=chart_data['division_bars'],
        defect_type_bars=chart_data['defect_type_bars'],
        division_stats=s['division_stats'], tower_groups=s['tower_groups'],
        type_groups=s['type_groups'],
        defect_rows=s['defect_rows'],
    )


@projects_bp.route('/projects/<int:project_id>/defects/<path:defect_type>')
def project_defects_by_type(project_id, defect_type):
    """The list a 'Defects by Type' bar links to — every defect of that
    one type, wherever it is, with its tower/position/severity/status.
    Reuses the same summary builder as the Overview page so the counts
    always agree with the bar the person clicked."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')

    s = _build_project_defect_summary(project)
    defects = [row for row in s['defect_rows'] if (row['defect_type'] or 'Unspecified') == defect_type]

    return render_template('defect_type_list.html',
        project=project, user_name=session.get('user_name', ''),
        back_endpoint=back_endpoint, defect_type=defect_type, defects=defects,
    )


@projects_bp.route('/projects/<int:project_id>/defects/by-tower')
def project_defects_by_tower(project_id):
    """Line dropdown -> tower list (with a defect count per tower) ->
    click a tower to see its full defect list. All the per-line/per-tower
    counts are computed once here and handed to the page as one JSON
    blob, so switching lines/towers in the browser is instant — no
    round-trip needed until a tower's defect list is actually opened."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')

    s = _build_project_defect_summary(project)

    # line_id -> {tower_label -> count}, built from the same defect_rows
    # the rest of the Overview page uses, so this always agrees with it.
    line_tower_counts = {}
    for row in s['defect_rows']:
        line_tower_counts.setdefault(row['line_id'], {}).setdefault(row['tower_label'], 0)
        line_tower_counts[row['line_id']][row['tower_label']] += 1

    lines_data = []
    summary_line_ids = set(s['line_ids'])
    for division in project.divisions:
        for line in division.lines:
            if line.id not in summary_line_ids:
                continue
            towers = line_tower_counts.get(line.id, {})
            lines_data.append({
                'id': line.id,
                'name': line.name,
                'division_name': division.name,
                'towers': [
                    {'label': label, 'count': count}
                    for label, count in sorted(towers.items(), key=lambda kv: kv[0])
                ],
            })

    return render_template('defects_by_tower.html',
        project=project, user_name=session.get('user_name', ''),
        back_endpoint=back_endpoint, lines_data=lines_data,
    )


@projects_bp.route('/projects/<int:project_id>/report')
def project_defect_report(project_id):
    """Generates and streams the defect report PDF for download/view.
    Open to both Admin and Client sessions, same as the chimney module's
    report — reuses _build_project_defect_summary() so the numbers in this
    PDF are always identical to what's shown on the Overview page."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    summary = _build_project_defect_summary(project)

    try:
        from trans_report import build_trans_report_pdf
        static_root = current_app.static_folder
        pdf_buf = build_trans_report_pdf(project, summary, static_root)
    except Exception as e:
        current_app.logger.exception('TRANS report generation failed for project %s', project_id)
        return jsonify({'error': f'Report generation failed: {e}'}), 500

    from flask import send_file
    safe_name = ''.join(c for c in project.name if c.isalnum() or c in ' _-').strip().replace(' ', '_')
    download_name = f'{safe_name or "transmission_line"}_inspection_report.pdf'
    return send_file(pdf_buf, as_attachment=False, download_name=download_name, mimetype='application/pdf')


@projects_bp.route('/projects/<int:project_id>/defects/export.csv')
def project_defects_export_csv(project_id):
    """Every defect on the project as a spreadsheet — for whoever wants
    to filter/pivot/share it outside the app rather than read it on
    screen. Same underlying data as everywhere else (defect_rows), so
    the export always matches what the dashboard and Overview show."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard

    s = _build_project_defect_summary(project)

    import csv
    import io as _io
    buf = _io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['Division', 'Line', 'Tower', 'Position', 'Component', 'Defect Type', 'Severity',
                      'Status', 'Resolution', 'Resolved By', 'Resolution Comment', 'Marked By', 'Marked At'])
    for r in s['defect_rows']:
        writer.writerow([
            r['division_name'], r['line_name'], r['tower_label'], r['location'] or '',
            r['component_name'] or '', r['defect_type'] or '', r['severity'],
            r['status'] or '', r.get('resolution_status') or 'Open', r.get('resolved_by') or '',
            r.get('resolution_comment') or '', r['created_by'] or '',
            r['created_at'].strftime('%Y-%m-%d %H:%M') if r['created_at'] else '',
        ])

    from flask import Response
    safe_name = ''.join(c for c in project.name if c.isalnum() or c in ' _-').strip().replace(' ', '_')
    filename = f'{safe_name or "project"}_defects.csv'
    return Response(
        buf.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


@projects_bp.route('/projects/<int:project_id>/info')
def project_info(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    return render_template('project_info.html', project=project, user_name=session.get('user_name', ''),
                           back_endpoint=MODULE_ROUTES.get(project.module, 'projects'))


# ── Divisions ──────────────────────────────────────────────────────────────

@projects_bp.route('/api/projects/<int:project_id>/divisions', methods=['GET'])
def api_list_divisions(project_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _project_access_guard_by_id(project_id)
    if guard:
        return guard
    Project.query.get_or_404(project_id)
    divisions = Division.query.filter_by(project_id=project_id).order_by(Division.created_at.asc()).all()
    if session.get('role') in ('Pilot', 'SME'):
        assigned_line_ids = visible_line_ids() or set()
        divisions = [d for d in divisions if any(line.id in assigned_line_ids for line in d.lines)]
    return jsonify({'divisions': [_visible_division_dict(d) for d in divisions]})


@projects_bp.route('/api/projects/<int:project_id>/divisions', methods=['POST'])
def api_create_division(project_id):
    guard = _admin_guard()
    if guard:
        return guard
    Project.query.get_or_404(project_id)

    data = request.get_json(force=True, silent=True) or request.form
    name = (data.get('name') or '').strip()
    try:
        lat = float(data.get('latitude'))
        lng = float(data.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Latitude and longitude must be valid numbers.'}), 400

    if not name:
        return jsonify({'error': 'Division name is required.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'Latitude/longitude out of range.'}), 400

    planned_towers_raw = (data.get('planned_towers') or '').strip() if isinstance(data.get('planned_towers'), str) else data.get('planned_towers')
    planned_towers = None
    if planned_towers_raw not in (None, ''):
        try:
            planned_towers = int(planned_towers_raw)
        except (TypeError, ValueError):
            planned_towers = None

    division = Division(
        project_id=project_id, name=name, latitude=lat, longitude=lng,
        client_name=(data.get('client_name') or '').strip() if isinstance(data.get('client_name'), str) else '',
        state=(data.get('state') or '').strip() if isinstance(data.get('state'), str) else '',
        planned_towers=planned_towers,
    )
    db.session.add(division)
    db.session.commit()
    return jsonify(division.to_dict()), 201


@projects_bp.route('/api/divisions/<int:division_id>', methods=['DELETE'])
def api_delete_division(division_id):
    """Cascades to every Line under it, and everything under those (photos,
    defects, thermal points) — just as destructive as deleting a whole
    project, so it's gated the same way: Admin only, delete password
    required. Was previously just _login_guard(), meaning any logged-in
    session — including a view-only Client User — could delete a whole
    division with no confirmation at all."""
    guard = _admin_guard()
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    name = division.name
    stored_paths = collect_division_files(division_id)
    db.session.delete(division)
    ActivityLog.log(action='delete', entity_type='Division', entity_name=name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    _cleanup_deleted_files(stored_paths, f'division {division_id}')
    return jsonify({'deleted': division_id})


# ── Lines ──────────────────────────────────────────────────────────────────

@projects_bp.route('/api/divisions/<int:division_id>/lines', methods=['GET'])
def api_list_lines(division_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _division_access_guard(division_id)
    if guard:
        return guard
    Division.query.get_or_404(division_id)
    lines = Line.query.filter_by(division_id=division_id).order_by(Line.created_at.asc()).all()
    if session.get('role') in ('Pilot', 'SME'):
        assigned_line_ids = visible_line_ids() or set()
        lines = [l for l in lines if l.id in assigned_line_ids]
    return jsonify({'lines': [l.to_dict() for l in lines]})


@projects_bp.route('/api/divisions/<int:division_id>/lines', methods=['POST'])
def api_create_line(division_id):
    guard = _admin_guard()
    if guard:
        return guard
    Division.query.get_or_404(division_id)

    form = request.form
    name = (form.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Line name is required.'}), 400

    try:
        start_lat = float(form.get('start_lat'))
        start_lng = float(form.get('start_lng'))
        end_lat   = float(form.get('end_lat'))
        end_lng   = float(form.get('end_lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Start/end position must be valid lat, long numbers.'}), 400

    length_raw = (form.get('length_km') or '').strip()
    if length_raw:
        try:
            length_km = float(length_raw)
        except ValueError:
            return jsonify({'error': 'Line length must be a number.'}), 400
    else:
        length_km = round(_haversine_km(start_lat, start_lng, end_lat, end_lng), 2)

    try:
        tower_count = int(form.get('tower_count') or 0)
    except ValueError:
        return jsonify({'error': 'Number of towers must be a whole number.'}), 400

    kml_path = ''
    kml_file = request.files.get('kml_file')
    if kml_file and kml_file.filename:
        saved = _save_upload(kml_file, 'kml', KML_EXTS)
        if saved is None:
            return jsonify({'error': 'KML file must have a .kml or .kmz extension.'}), 400
        kml_path = saved
        # The KML itself is authoritative once uploaded — auto-derive the
        # real tower count from its actual Point placemarks rather than
        # trusting whatever number was typed (or defaulted to 0).
        full_kml_path = _storage_working_path(kml_path)
        auto_count = _count_kml_tower_points(full_kml_path)
        if auto_count is not None:
            tower_count = auto_count

    line = Line(
        division_id=division_id,
        name=name,
        start_lat=start_lat, start_lng=start_lng,
        end_lat=end_lat, end_lng=end_lng,
        length_km=length_km,
        tower_count=tower_count,
        kml_path=kml_path,
    )
    db.session.add(line)
    db.session.commit()
    return jsonify(line.to_dict()), 201


@projects_bp.route('/api/lines/<int:line_id>', methods=['GET'])
def api_get_line(line_id):
    """A single line's own info plus its division/project ids — for
    deep-linking straight to a specific line on the map (e.g. from the
    SME dashboard's "Start Work" card), without the caller needing to
    already know which division it's under."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    division = line.division
    project = division.project if division else None
    return jsonify({
        'line': line.to_dict(),
        'division_id': line.division_id,
        'project_id': project.id if project else None,
    })


@projects_bp.route('/api/lines/<int:line_id>/kml-attributes', methods=['POST'])
def api_set_visible_kml_attrs(line_id):
    """Which raw KML ExtendedData keys Admin wants shown in the Tower
    Details panel — the line map is now served through the authenticated
    GeoJSON cache; this remembers the chosen subset so
    every tower's panel filters down to it instead of dumping every raw
    field (styleUrl, styleHash, etc.) every time."""
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    attrs = data.get('attrs')
    if not isinstance(attrs, list) or not all(isinstance(a, str) for a in attrs):
        return jsonify({'error': 'attrs must be a list of attribute name strings.'}), 400
    line.visible_kml_attrs = json.dumps(attrs)
    db.session.commit()
    return jsonify(line.to_dict())


_KML_TOWER_FIELD_CANDIDATES = ['tower_no', 'towerno', 'tower_number', 'towernum', 'tower', 'twr_no', 'twrno', 'point_no', 'pointno']


def _kml_local_tag(elem):
    tag = elem.tag
    return tag.split('}')[-1] if '}' in tag else tag


def _count_kml_tower_points(kml_full_path):
    """Counts Point Placemarks in an uploaded KML/KMZ file — used to set
    a line's tower_count automatically from what the KML actually
    contains, instead of relying on a manually typed number that can
    drift out of sync with it. Returns None (leave tower_count alone) if
    the file can't be read/parsed for any reason."""
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        if kml_full_path.lower().endswith('.kmz'):
            with zipfile.ZipFile(kml_full_path) as z:
                kml_name = next((n for n in z.namelist() if n.lower().endswith('.kml')), None)
                if not kml_name:
                    return None
                root = ET.fromstring(z.read(kml_name))
        else:
            root = ET.parse(kml_full_path).getroot()
    except Exception:
        return None

    count = 0
    for placemark in root.iter():
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        for child in placemark.iter():
            if _kml_local_tag(child) == 'Point':
                count += 1
                break
    return count


def _kml_placemark_props(placemark_elem):
    """{name: value} for a Placemark's ExtendedData/SimpleData/Data fields
    plus its own <name> — close enough to what the frontend's togeojson
    parse produces to reuse the exact same tower-label matching logic."""
    props = {}
    name_text = None
    for child in placemark_elem.iter():
        tag = _kml_local_tag(child)
        if tag == 'name' and name_text is None:
            name_text = (child.text or '').strip()
        elif tag == 'Data':
            key = child.get('name')
            value_text = None
            for sub in child:
                if _kml_local_tag(sub) == 'value':
                    value_text = (sub.text or '').strip()
                    break
            if key and value_text:
                props[key] = value_text
        elif tag == 'SimpleData':
            key = child.get('name')
            if key and child.text:
                props[key] = child.text.strip()
    if name_text:
        props.setdefault('name', name_text)
    return props


def _kml_pick_tower_label(props):
    """Mirrors the frontend's pickTowerLabel() exactly, so a point edited
    here matches the same point the Admin was looking at in the browser."""
    def norm(s):
        return ''.join(ch for ch in s.lower() if ch.isalnum())
    for cand in _KML_TOWER_FIELD_CANDIDATES:
        cand_norm = norm(cand)
        for k, v in props.items():
            if norm(k) == cand_norm and v:
                return str(v)
    if props.get('name'):
        return str(props['name'])
    return 'Point'


def _line_geojson(line):
    """Convert the authoritative KML/KMZ into a cacheable FeatureCollection."""
    import xml.etree.ElementTree as ET
    import zipfile
    if not line or not line.kml_path:
        return None
    source = _storage_working_path(line.kml_path)
    if not source:
        return None
    try:
        if source.lower().endswith('.kmz'):
            with zipfile.ZipFile(source) as archive:
                member = next((name for name in archive.namelist() if name.lower().endswith('.kml')), None)
                if not member:
                    return None
                root = ET.fromstring(archive.read(member))
        else:
            root = ET.parse(source).getroot()
    except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile):
        return None
    features = []
    for placemark in root.iter():
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        props = _kml_placemark_props(placemark)
        for geometry in placemark.iter():
            kind = _kml_local_tag(geometry)
            if kind not in {'Point', 'LineString'}:
                continue
            coords_el = next((child for child in geometry.iter() if _kml_local_tag(child) == 'coordinates'), None)
            if coords_el is None or not coords_el.text:
                continue
            coordinates = []
            for raw in coords_el.text.replace('\n', ' ').split():
                parts = raw.split(',')
                try:
                    coordinates.append([float(parts[0]), float(parts[1])])
                except (IndexError, ValueError):
                    continue
            if coordinates:
                features.append({'type': 'Feature', 'properties': props, 'geometry': {
                    'type': kind, 'coordinates': coordinates[0] if kind == 'Point' else coordinates}})
    return {'type': 'FeatureCollection', 'features': features} if features else None


def _ensure_line_geojson_cache(line):
    data = _line_geojson(line)
    if not data:
        return None
    folder = os.path.join(current_app.static_folder, 'uploads', 'kml_cache')
    os.makedirs(folder, exist_ok=True)
    destination = os.path.join(folder, f'line_{line.id}.geojson')
    temporary = destination + '.tmp'
    with open(temporary, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, separators=(',', ':'), ensure_ascii=False)
    os.replace(temporary, destination)
    line.geojson_path = f'uploads/kml_cache/line_{line.id}.geojson'
    from storage_service import get_storage
    get_storage().publish(line.geojson_path, destination)
    db.session.commit()
    return data


@projects_bp.route('/api/lines/<int:line_id>/geojson', methods=['GET'])
def api_line_geojson(line_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = None
    if line.geojson_path:
        cached = _storage_working_path(line.geojson_path)
        try:
            if cached and os.path.isfile(cached):
                with open(cached, encoding='utf-8') as handle:
                    data = json.load(handle)
        except (OSError, ValueError):
            data = None
    if data is None:
        data = _ensure_line_geojson_cache(line)
    if not data:
        return jsonify({'error': 'KML contains no usable point or line geometry.'}), 422
    response = jsonify(data)
    response.headers['Cache-Control'] = 'private, max-age=3600'
    return response


def _kml_tower_coordinates(line, tower_label):
    """Return trusted ``(lat, lng)`` for a tower from the line's KML/KMZ.

    Pilot capture requests must never supply the reference tower position
    themselves; otherwise both sides of the distance check are controlled by
    the browser. ``None`` means the line file or label could not be verified.
    """
    if not line or not line.kml_path or not tower_label:
        return None
    full_path = _storage_working_path(line.kml_path)
    if not full_path:
        return None

    import xml.etree.ElementTree as ET
    import zipfile
    try:
        if full_path.lower().endswith('.kmz'):
            with zipfile.ZipFile(full_path) as archive:
                kml_name = next((n for n in archive.namelist() if n.lower().endswith('.kml')), None)
                if not kml_name:
                    return None
                root = ET.fromstring(archive.read(kml_name))
        else:
            root = ET.parse(full_path).getroot()
    except (OSError, ET.ParseError, zipfile.BadZipFile):
        return None

    wanted = str(tower_label).strip().casefold()
    for placemark in root.iter():
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        label = _kml_pick_tower_label(_kml_placemark_props(placemark)).strip().casefold()
        if label != wanted:
            continue
        point = next((child for child in placemark.iter() if _kml_local_tag(child) == 'Point'), None)
        if point is None:
            return None
        coords = next((child for child in point.iter() if _kml_local_tag(child) == 'coordinates'), None)
        if coords is None or not coords.text:
            return None
        try:
            first = coords.text.strip().split()[0]
            lng, lat = (float(v) for v in first.split(',')[:2])
        except (IndexError, TypeError, ValueError):
            return None
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            return lat, lng
        return None
    return None


def _kml_remove_placemark(root, placemark):
    """xml.etree has no getparent() (that's an lxml-only feature) — has
    to find the direct parent by searching, then remove from there."""
    for parent in root.iter():
        if placemark in list(parent):
            parent.remove(placemark)
            return True
    return False



@projects_bp.route('/api/lines/<int:line_id>/kml-edit', methods=['POST'])
def api_edit_kml(line_id):
    """Rewrites the line's KML file to match the map editor's current
    state exactly: existing points move to their new coordinates, points
    no longer present get removed, points with no existing match get
    added as new Placemarks, and any newly-drawn lines are appended as
    additional LineString Placemarks. Everything else already in the file
    (styles, the original route line, unrelated Placemarks) is left
    untouched. Since Download/View just serves this same file, it
    automatically reflects the edit — no separate "edited version" to
    manage."""
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    if not line.kml_path:
        return jsonify({'error': 'This line has no KML file uploaded.'}), 400

    data = request.get_json(force=True, silent=True) or {}
    points = data.get('points')
    lines_in = data.get('lines') or []
    if not isinstance(points, list):
        return jsonify({'error': 'points must be a list.'}), 400
    if not isinstance(lines_in, list):
        return jsonify({'error': 'lines must be a list.'}), 400

    full_path = _storage_working_path(line.kml_path)
    if not full_path:
        return jsonify({'error': 'KML file was not found in active storage.'}), 404

    import xml.etree.ElementTree as ET
    KML_NS = 'http://www.opengis.net/kml/2.2'
    ET.register_namespace('', KML_NS)  # keep the default xmlns unprefixed on write
    try:
        tree = ET.parse(full_path)
    except ET.ParseError as e:
        return jsonify({'error': f'Could not parse the existing KML file: {e}'}), 500
    root = tree.getroot()

    # Validate + collect the desired point set first (label -> lat/lng).
    desired = {}
    for p in points:
        label = str(p.get('label', '')).strip()
        try:
            lat, lng = float(p['lat']), float(p['lng'])
        except (KeyError, TypeError, ValueError):
            continue
        if label and -90 <= lat <= 90 and -180 <= lng <= 180:
            desired[label] = (lat, lng)

    # Find the Document element (or root itself) — new Placemarks get
    # appended there, matching where existing ones already live.
    doc_el = root
    for child in root:
        if _kml_local_tag(child) == 'Document':
            doc_el = child
            break

    existing_labels = set()
    for placemark in list(root.iter()):
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        point_el, coords_el = None, None
        for child in placemark.iter():
            if _kml_local_tag(child) == 'Point':
                point_el = child
            elif point_el is not None and coords_el is None and _kml_local_tag(child) == 'coordinates':
                coords_el = child
        if point_el is None or coords_el is None:
            continue  # not a point Placemark (e.g. the route LineString) — leave entirely alone

        label = _kml_pick_tower_label(_kml_placemark_props(placemark))
        existing_labels.add(label)
        if label in desired:
            lat, lng = desired[label]
            existing_parts = (coords_el.text or '').strip().split(',')
            alt_suffix = f',{existing_parts[2]}' if len(existing_parts) >= 3 else ''
            coords_el.text = f'{lng},{lat}{alt_suffix}'
        else:
            # Present in the file but not in the map's current point set
            # anymore — the pilot/admin deleted it in the editor.
            _kml_remove_placemark(root, placemark)

    # Anything in `desired` that wasn't matched to an existing Placemark
    # is a brand-new point — add it.
    added_points = 0
    for label, (lat, lng) in desired.items():
        if label in existing_labels:
            continue
        placemark = ET.SubElement(doc_el, f'{{{KML_NS}}}Placemark')
        name_el = ET.SubElement(placemark, f'{{{KML_NS}}}name')
        name_el.text = label
        ext_data = ET.SubElement(placemark, f'{{{KML_NS}}}ExtendedData')
        data_el = ET.SubElement(ext_data, f'{{{KML_NS}}}Data', {'name': 'tower_no'})
        value_el = ET.SubElement(data_el, f'{{{KML_NS}}}value')
        value_el.text = label
        point_el = ET.SubElement(placemark, f'{{{KML_NS}}}Point')
        coords_el = ET.SubElement(point_el, f'{{{KML_NS}}}coordinates')
        coords_el.text = f'{lng},{lat},0'
        added_points += 1

    added_lines = 0
    for line_in in lines_in:
        coords = line_in.get('coords')
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            coord_pairs = [(float(c[1]), float(c[0])) for c in coords]  # (lng, lat) for KML
        except (TypeError, ValueError, IndexError):
            continue
        placemark = ET.SubElement(doc_el, f'{{{KML_NS}}}Placemark')
        name_el = ET.SubElement(placemark, f'{{{KML_NS}}}name')
        name_el.text = str(line_in.get('name') or 'New Line')
        ls_el = ET.SubElement(placemark, f'{{{KML_NS}}}LineString')
        coords_el = ET.SubElement(ls_el, f'{{{KML_NS}}}coordinates')
        coords_el.text = ' '.join(f'{lng},{lat},0' for lng, lat in coord_pairs)
        added_lines += 1

    lines_edit_in = data.get('lines_edit') or []
    if not isinstance(lines_edit_in, list):
        return jsonify({'error': 'lines_edit must be a list.'}), 400

    # Collect every existing LineString Placemark in document order, so
    # edits can be matched back by name (if the Placemark has one) or by
    # that same position — exactly how the editor identified them when
    # it first loaded the file.
    linestring_placemarks = []
    for placemark in root.iter():
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        ls_el, coords_el = None, None
        for child in placemark.iter():
            if _kml_local_tag(child) == 'LineString':
                ls_el = child
            elif ls_el is not None and coords_el is None and _kml_local_tag(child) == 'coordinates':
                coords_el = child
        if ls_el is not None and coords_el is not None:
            name_text = None
            for child in placemark:
                if _kml_local_tag(child) == 'name':
                    name_text = (child.text or '').strip() or None
                    break
            linestring_placemarks.append({'coords_el': coords_el, 'name': name_text})

    edited_lines = 0
    for edit in lines_edit_in:
        coords = edit.get('coords')
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            coord_pairs = [(float(c[1]), float(c[0])) for c in coords]  # (lng, lat) for KML
        except (TypeError, ValueError, IndexError):
            continue

        target = None
        edit_name = edit.get('name')
        if edit_name:
            target = next((p for p in linestring_placemarks if p['name'] == edit_name), None)
        if target is None:
            idx = edit.get('index')
            if isinstance(idx, int) and 0 <= idx < len(linestring_placemarks):
                target = linestring_placemarks[idx]
        if target is None:
            continue  # couldn't confidently match this one — leave the file alone rather than guess wrong

        target['coords_el'].text = ' '.join(f'{lng},{lat},0' for lng, lat in coord_pairs)
        edited_lines += 1

    tree.write(full_path, encoding='utf-8', xml_declaration=True)
    from storage_service import get_storage
    get_storage().publish(line.kml_path, full_path)

    # Points may have been added/removed in this edit — keep tower_count
    # in sync with what the KML now actually contains.
    auto_count = _count_kml_tower_points(full_path)
    if auto_count is not None:
        line.tower_count = auto_count
        db.session.commit()

    return jsonify({
        'updated_points': len(desired), 'added_points': added_points,
        'added_lines': added_lines, 'edited_lines': edited_lines,
    })


@projects_bp.route('/api/lines/<int:line_id>', methods=['DELETE'])
def api_delete_line(line_id):
    """Same fix as division delete above — this cascades to every photo,
    defect, and thermal point on the line, and was previously reachable
    by any logged-in session with no password and no role check."""
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    name = line.name
    stored_paths = collect_line_files(line_id)
    db.session.delete(line)
    ActivityLog.log(action='delete', entity_type='Line', entity_name=name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    _cleanup_deleted_files(stored_paths, f'line {line_id}')
    return jsonify({'deleted': line_id})


# ── Tower photos ─────────────────────────────────────────────────────────
# Tower points come from a Line's cached GeoJSON (not individual
# DB rows) — photos are matched to a specific point by line_id + the
# tower's label as it appears in the KML (e.g. "T12"), passed by the client
# exactly as shown in the tower details panel.

@projects_bp.route('/api/lines/<int:line_id>/tower-photos', methods=['GET'])
def api_list_tower_photos(line_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    try:
        per_page = min(200, max(20, int(request.args.get('per_page') or 120)))
        after_id = max(0, int(request.args.get('after_id') or 0))
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid pagination values.'}), 400
    query = (TowerPhoto.query
             .filter_by(line_id=line_id, tower_label=tower_label)
             .options(selectinload(TowerPhoto.defects), selectinload(TowerPhoto.thermal_points)))
    if after_id:
        query = query.filter(TowerPhoto.id > after_id)
    rows = query.order_by(TowerPhoto.id.asc()).limit(per_page + 1).all()
    has_more = len(rows) > per_page
    photos = rows[:per_page]
    # Client sessions only ever see a tower's photos once Admin has
    # explicitly marked that tower's inspection as done — NOT just
    # whether defects happen to be marked. A tower with zero defects is
    # either a genuinely good tower or one nobody's finished reviewing
    # yet; "inspection done" is how Admin distinguishes those, so a good
    # tower still needs that explicit sign-off before a client sees it,
    # same as a tower with real defects does. Enforced here, not just
    # hidden in the UI, so a Client session can't see everything by
    # calling this endpoint directly. Pilot is deliberately excluded from
    # this restriction — a pilot needs to see everything THEY'VE captured
    # regardless of whether Admin has finished reviewing it yet.
    if session.get('role') == 'Client User':
        status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
        if not status or not status.inspection_done:
            photos = []
    return jsonify({'photos': [p.to_dict() for p in photos], 'has_more': has_more,
                    'next_after_id': photos[-1].id if has_more and photos else None})


@projects_bp.route('/api/tower-photos/<int:photo_id>/image', methods=['GET'])
def api_tower_photo_image(photo_id):
    """Serve the best surviving viewable copy of a tower photo.

    Older application copies can contain a stale raw-image path while a
    defect-preservation copy or thumbnail still exists. Resolve that here
    instead of returning a broken URL to the lightbox.
    """
    guard = _login_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    if not can_access_photo(photo) or not client_can_access_tower(photo.line_id, photo.tower_label):
        return jsonify({'error': 'You do not have access to this photo.'}), 403

    storage = get_storage()
    candidates = [photo.image_path, photo.defect_copy_path, photo.thumbnail_path]
    for stored_path in candidates:
        normalised = (stored_path or '').replace('\\', '/').lstrip('/')
        if normalised.startswith('static/'):
            normalised = normalised[len('static/'):]
        if not normalised:
            continue
        try:
            if storage.exists(normalised):
                return storage.response(normalised, max_age=86400)
        except (OSError, ValueError, RuntimeError):
            continue
    return jsonify({'error': 'The image file was not found in the copied uploads folder.'}), 404


@projects_bp.route('/api/lines/<int:line_id>/tower-photos', methods=['POST'])
def api_upload_tower_photo(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    tower_label = (request.form.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    # A project only does RGB, only Thermal, or both — reject an upload
    # that doesn't match, rather than silently accepting photos the
    # project was never meant to have. Detected the same way thermal
    # photos are recognized everywhere else in the app: a "_T" (or
    # "_T_something") suffix on the filename.
    project = line.division.project if line.division else None
    allowed_types = project.get_inspection_types() if project else ['rgb', 'thermal']
    validation = _validate_uploaded_image(image_file, allowed_types)
    if not validation.get('valid'):
        return jsonify({'error': validation.get('error'), 'validation': validation}), 400

    # Reject an exact re-upload of a photo already on this tower — same
    # bytes, not just a similar filename (a fresh EXIF-preserving copy or
    # a re-run of the same folder upload should never silently create a
    # duplicate row + a duplicate file on disk).
    image_file.stream.seek(0)
    content_hash = hashlib.sha256(image_file.read()).hexdigest()
    image_file.stream.seek(0)
    if TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label, content_hash=content_hash).first():
        return jsonify({'error': f'"{image_file.filename}" is already uploaded for this tower — skipped as a duplicate.'}), 400

    # GPS proximity check: an uploaded photo should actually have been
    # taken at this tower, not some other one. Read the tower's own
    # coordinates from the form (the client has them from the KML point
    # that was clicked), read the photo's own EXIF GPS tag, and compare —
    # drone photos reliably carry accurate GPS in their EXIF, so this is a
    # straightforward accept/reject: within range, upload; too far away,
    # or no GPS data on the photo at all, reject.
    try:
        tower_lat = float(request.form.get('tower_lat'))
        tower_lng = float(request.form.get('tower_lng'))
    except (TypeError, ValueError):
        tower_lat = tower_lng = None

    if tower_lat is not None and tower_lng is not None:
        gps = _extract_gps_from_image(image_file)
        if gps is None:
            return jsonify({
                'error': "This photo has no location data in it, so it can't be verified against this tower. "
                         "Please upload a photo that has GPS data."
            }), 400
        dist_m = _haversine_km(gps[0], gps[1], tower_lat, tower_lng) * 1000
        if dist_m > TOWER_PHOTO_GPS_BUFFER_M:
            return jsonify({
                'error': f"This photo's location is about {round(dist_m)}m from this tower — photos must be "
                         f"taken within {TOWER_PHOTO_GPS_BUFFER_M}m. It looks like it may belong to a "
                         f"different tower."
            }), 400
    else:
        gps = None

    saved, thumb = _save_tower_photo(image_file, project, line.division, line, tower_label, IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = TowerPhoto(
        line_id=line_id,
        tower_label=tower_label,
        image_path=saved,
        thumbnail_path=thumb,
        uploaded_by=session.get('user_name', ''),
        gps_lat=gps[0] if gps else None,
        gps_lng=gps[1] if gps else None,
        content_hash=content_hash,
        media_type=validation['media_type'], image_width=validation['width'],
        image_height=validation['height'], captured_at=validation['captured_at'],
        validation_status=validation['status'],
        validation_warnings_json=json.dumps(validation.get('warnings') or []),
    )
    db.session.add(photo)
    db.session.commit()
    result = photo.to_dict()
    result['validation'] = validation
    return jsonify(result), 201


@projects_bp.route('/api/lines/<int:line_id>/validate-image', methods=['POST'])
def api_validate_tower_image(line_id):
    """Optional single-file preflight used by Admin troubleshooting.

    Bulk upload performs equivalent local preflight before confirmation and
    the real upload endpoint always repeats authoritative server validation.
    """
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400
    project = line.division.project if line.division else None
    allowed_types = project.get_inspection_types() if project else ['rgb', 'thermal']
    validation = _validate_uploaded_image(image_file, allowed_types)
    return jsonify(validation), 200 if validation.get('valid') else 400


@projects_bp.route('/api/lines/<int:line_id>/upload-batches', methods=['GET', 'POST'])
def api_line_upload_batches(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    if request.method == 'GET':
        batches = (UploadBatch.query.filter_by(line_id=line_id)
                   .order_by(UploadBatch.id.desc()).limit(20).all())
        tower_counts = dict(db.session.query(TowerPhoto.tower_label, db.func.count(TowerPhoto.id))
                            .filter(TowerPhoto.line_id == line_id)
                            .group_by(TowerPhoto.tower_label).all())
        return jsonify({'batches': [row.to_dict() for row in batches],
                        'tower_counts': [{'tower_label': key, 'images': value}
                                         for key, value in sorted(tower_counts.items())]})
    data = request.get_json(silent=True) or {}
    batch = UploadBatch(
        line_id=line_id, uploaded_by_user_id=session.get('user_id'),
        uploaded_by_name=session.get('user_name', ''),
        folder_name=(data.get('folder_name') or '')[:255],
        total_files=max(0, int(data.get('total_files') or 0)), status='Preparing')
    db.session.add(batch)
    db.session.commit()
    return jsonify(batch.to_dict()), 201


@projects_bp.route('/api/upload-batches/<int:batch_id>', methods=['GET', 'PATCH'])
def api_upload_batch(batch_id):
    guard = _admin_guard()
    if guard:
        return guard
    batch = UploadBatch.query.get_or_404(batch_id)
    if request.method == 'GET':
        return jsonify(batch.to_dict(include_items=True))
    data = request.get_json(silent=True) or {}
    previous_status = batch.status
    allowed_statuses = {'Preparing', 'Uploading', 'Completed', 'Completed with errors', 'Cancelled'}
    if data.get('status') in allowed_statuses:
        batch.status = data['status']
        if batch.status in {'Completed', 'Completed with errors', 'Cancelled'}:
            batch.finished_at = datetime.utcnow()
    items = data.get('items') or []
    for item in items:
        status = item.get('status')
        if status not in {'Completed', 'Duplicate', 'Failed', 'Invalid', 'Wrong Type', 'No GPS', 'Unmatched', 'Cancelled'}:
            continue
        db.session.add(UploadBatchItem(
            batch_id=batch.id, filename=(item.get('filename') or 'unknown')[:500],
            tower_label=(item.get('tower_label') or '')[:150], status=status,
            error_message=(item.get('error') or '')[:2000],
            file_size=max(0, int(item.get('file_size') or 0)),
            attempts=max(0, int(item.get('attempts') or 0))))
    db.session.flush()
    counts = dict(db.session.query(UploadBatchItem.status, db.func.count(UploadBatchItem.id))
                  .filter(UploadBatchItem.batch_id == batch.id)
                  .group_by(UploadBatchItem.status).all())
    batch.completed_files = counts.get('Completed', 0)
    batch.duplicate_files = counts.get('Duplicate', 0)
    batch.failed_files = counts.get('Failed', 0) + counts.get('Invalid', 0) + counts.get('Wrong Type', 0)
    batch.no_gps_files = counts.get('No GPS', 0)
    batch.unmatched_files = counts.get('Unmatched', 0)
    batch.cancelled_files = counts.get('Cancelled', 0)
    batch.matched_files = batch.completed_files + batch.duplicate_files + batch.failed_files
    if previous_status not in {'Completed', 'Completed with errors', 'Cancelled'} and batch.status in {'Completed', 'Completed with errors'}:
        assignment = SmeAssignment.query.filter_by(line_id=batch.line_id).first()
        if assignment:
            line = batch.line
            project = line.division.project if line and line.division else None
            notify_user(assignment.sme_user_id, f'Images ready for review — {line.name}',
                        f'{batch.completed_files} uploaded, {batch.failed_files + batch.no_gps_files + batch.unmatched_files} need attention.',
                        'upload', f'/projects/{project.id}/map?line={line.id}' if project else '')
    db.session.commit()
    return jsonify(batch.to_dict(include_items=True))


@projects_bp.route('/api/upload-batches/<int:batch_id>/errors.csv')
def api_upload_batch_errors(batch_id):
    guard = _admin_guard()
    if guard:
        return guard
    batch = UploadBatch.query.get_or_404(batch_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['File', 'Tower', 'Status', 'Reason', 'Attempts', 'Size bytes'])
    for item in sorted(batch.items, key=lambda row: row.id):
        if item.status not in {'Completed', 'Duplicate'}:
            writer.writerow([item.filename, item.tower_label, item.status,
                             item.error_message, item.attempts, item.file_size])
    return Response(output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': f'attachment; filename=upload_batch_{batch.id}_errors.csv'})


@projects_bp.route('/api/lines/<int:line_id>/tower-defects-flat', methods=['GET'])
def api_tower_defects_flat(line_id):
    """One entry PER DEFECT (not per photo) for a tower, each carrying its
    parent photo's image_url. Used for the client-facing gallery: marking
    2 defects on the same uploaded photo should show as 2 separate image
    entries there — one per defect, each highlighting just that one
    marking — rather than one image with both defects combined."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    guard = _client_tower_release_guard(line_id, tower_label)
    if guard:
        return guard
    defects = (TowerDefect.query
               .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
               .filter(TowerPhoto.line_id == line_id, TowerPhoto.tower_label == tower_label,
                       TowerDefect.deleted_at.is_(None))
               .order_by(TowerPhoto.id.asc(), TowerDefect.created_at.asc()).all())
    out = []
    for d in defects:
        entry = d.to_dict()
        entry['image_url'] = stored_url(d.photo.image_path)
        entry['thumbnail_url'] = stored_url(d.photo.thumbnail_path) if d.photo.thumbnail_path else entry['image_url']
        out.append(entry)
    return jsonify({'defects': out})


@projects_bp.route('/api/lines/<int:line_id>/tower-thermal-points-flat', methods=['GET'])
def api_tower_thermal_points_flat(line_id):
    """Which thermal photos on this tower actually have a measured point/
    rect/line on them — mirrors api_tower_defects_flat's shape (one entry
    per measurement, each carrying its parent photo's image_url), used
    the same way client-side: a thermal photo with zero measurements on
    it shouldn't show in the Client gallery any more than an RGB photo
    with zero marked defects does."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    guard = _client_tower_release_guard(line_id, tower_label)
    if guard:
        return guard
    points = (ThermalPoint.query
              .join(TowerPhoto, ThermalPoint.tower_photo_id == TowerPhoto.id)
              .filter(TowerPhoto.line_id == line_id, TowerPhoto.tower_label == tower_label)
              .order_by(TowerPhoto.id.asc(), ThermalPoint.created_at.asc()).all())
    out = []
    for pt in points:
        entry = pt.to_dict()
        entry['image_url'] = stored_url(pt.photo.image_path)
        out.append(entry)
    return jsonify({'points': out})


@projects_bp.route('/api/divisions/<int:division_id>/photo-coverage', methods=['GET'])
def api_division_photo_coverage(division_id):
    """For every line in this division: how many of its towers have at
    least one photo, out of its total tower_count. One request covers
    the whole division's sidebar at once, rather than one request per
    line — avoids an N+1 pattern when a division has many lines."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _division_access_guard(division_id)
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)
    lines = [line for line in division.lines if can_access_line(line)]
    line_ids = [l.id for l in lines]

    photographed_counts = {}
    if line_ids:
        rows = (db.session.query(TowerPhoto.line_id, TowerPhoto.tower_label)
                .filter(TowerPhoto.line_id.in_(line_ids)).distinct().all())
        released_keys = None
        if session.get('role') == 'Client User':
            released_keys = {
                (s.line_id, s.tower_label)
                for s in TowerInspectionStatus.query.filter(
                    TowerInspectionStatus.line_id.in_(line_ids),
                    TowerInspectionStatus.inspection_done.is_(True),
                ).all()
            }
        for line_id, label in rows:
            if released_keys is not None and (line_id, label) not in released_keys:
                continue
            photographed_counts[line_id] = photographed_counts.get(line_id, 0) + 1

    return jsonify({
        'coverage': {
            l.id: {'photographed': photographed_counts.get(l.id, 0), 'total': l.tower_count or 0}
            for l in lines
        }
    })


@projects_bp.route('/api/lines/<int:line_id>/tower-photo-labels', methods=['GET'])
def api_tower_photo_labels(line_id):
    """Which tower labels on this line have at least one photo — used for
    the map's photo-indicator dots and the Tower Photo Checklist. Admin
    and Pilot both see every uploaded label regardless of inspection
    status — Admin needs to catch what's missing, Pilot needs to see
    everything they've captured regardless of whether Admin's reviewed
    it yet. A Client session only ever sees labels for towers whose
    inspection has been explicitly marked done, matching
    api_list_tower_photos — otherwise a Client's map dot would point at a
    tower whose photos, once opened, turn out to be empty for them."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    q = db.session.query(TowerPhoto.tower_label).filter_by(line_id=line_id)
    if session.get('role') == 'Client User':
        done_labels = {s.tower_label for s in TowerInspectionStatus.query.filter_by(line_id=line_id, inspection_done=True).all()}
        q = q.filter(TowerPhoto.tower_label.in_(done_labels)) if done_labels else q.filter(db.false())
    rows = q.distinct().all()
    return jsonify({'labels': [r[0] for r in rows]})


@projects_bp.route('/api/lines/<int:line_id>/tower-photo-points', methods=['GET'])
def api_tower_photo_points(line_id):
    """Every individual photo's OWN gps_lat/gps_lng on this line — one
    entry per photo, not deduped to one per tower — for a real per-image
    dot on the map instead of a single dot marking "this tower has
    photos". Same visibility rule as tower-photo-labels above (Client
    only sees labels from towers marked inspection-done). Photos with no
    stored GPS (uploaded before this was tracked, or where EXIF/capture
    GPS wasn't available) are skipped — there's nowhere to put their dot."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    q = (TowerPhoto.query.filter_by(line_id=line_id)
         .filter(TowerPhoto.gps_lat.isnot(None), TowerPhoto.gps_lng.isnot(None)))
    if session.get('role') == 'Client User':
        done_labels = {s.tower_label for s in TowerInspectionStatus.query.filter_by(line_id=line_id, inspection_done=True).all()}
        q = q.filter(TowerPhoto.tower_label.in_(done_labels)) if done_labels else q.filter(db.false())
    photos = q.all()
    return jsonify({'points': [
        {'id': p.id, 'tower_label': p.tower_label, 'lat': p.gps_lat, 'lng': p.gps_lng}
        for p in photos
    ]})


@projects_bp.route('/api/lines/<int:line_id>/tower-summary', methods=['GET'])
def api_tower_summary(line_id):
    """Inspection-done status and defect count for every tower on this
    line, in one call — used by the tower list sidebar so it doesn't
    need a separate request per tower. Two grouped queries (defect
    counts, inspection statuses) rather than N+1 per-tower lookups."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)

    defect_counts = dict(
        db.session.query(TowerPhoto.tower_label, db.func.count(TowerDefect.id))
        .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
        .filter(TowerPhoto.line_id == line_id, TowerDefect.deleted_at.is_(None))
        .group_by(TowerPhoto.tower_label)
        .all()
    )
    open_counts = dict(
        db.session.query(TowerPhoto.tower_label, db.func.count(TowerDefect.id))
        .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
        .filter(TowerPhoto.line_id == line_id, TowerDefect.deleted_at.is_(None), TowerDefect.resolution_status == 'Open')
        .group_by(TowerPhoto.tower_label).all()
    )
    critical_counts = dict(
        db.session.query(TowerPhoto.tower_label, db.func.count(TowerDefect.id))
        .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
        .filter(TowerPhoto.line_id == line_id, TowerDefect.deleted_at.is_(None),
                TowerDefect.resolution_status == 'Open', TowerDefect.severity == 'Critical')
        .group_by(TowerPhoto.tower_label).all()
    )
    photo_counts = dict(db.session.query(TowerPhoto.tower_label, db.func.count(TowerPhoto.id))
                        .filter(TowerPhoto.line_id == line_id, TowerPhoto.raw_deleted.is_(False))
                        .group_by(TowerPhoto.tower_label).all())
    inspection_rows = TowerInspectionStatus.query.filter_by(line_id=line_id).all()
    inspection_done = {r.tower_label: bool(r.inspection_done) for r in inspection_rows}

    labels = set(defect_counts.keys()) | set(inspection_done.keys())
    if session.get('role') == 'Client User':
        labels = {label for label in labels if inspection_done.get(label, False)}
    return jsonify({'towers': {
        label: {'defect_count': defect_counts.get(label, 0), 'open_defect_count': open_counts.get(label, 0),
                'critical_defect_count': critical_counts.get(label, 0), 'photo_count': photo_counts.get(label, 0),
                'inspection_done': inspection_done.get(label, False)}
        for label in labels
    }})


@projects_bp.route('/api/lines/<int:line_id>/tower-zones', methods=['GET'])
def api_get_tower_zones(line_id):
    """All zone classifications set so far for this line's towers, plus
    which ones the Pilot has already submitted — one call, used to
    populate the pilot's tower list and map markers without a separate
    request per tower."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    rows = TowerInspectionStatus.query.filter_by(line_id=line_id).filter(TowerInspectionStatus.zone != '').all()
    submitted_rows = TowerInspectionStatus.query.filter_by(line_id=line_id, pilot_submitted=True).all()
    if session.get('role') == 'Client User':
        rows = [r for r in rows if r.inspection_done]
        submitted_rows = [r for r in submitted_rows if r.inspection_done]
    return jsonify({
        'zones': {r.tower_label: r.zone for r in rows},
        'submitted_labels': [r.tower_label for r in submitted_rows],
    })


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/inspection-status', methods=['GET'])
def api_get_inspection_status(line_id, tower_label):
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if session.get('role') == 'Client User' and (not status or not status.inspection_done):
        return jsonify({
            'line_id': line_id,
            'tower_label': tower_label,
            'inspection_done': False,
            'marked_by': '',
            'marked_at': '',
            'zone': '',
            'zone_set_by': '',
            'zone_set_at': '',
            'pilot_submitted': False,
            'pilot_submitted_by': '',
            'pilot_submitted_at': '',
        })
    if status:
        return jsonify(status.to_dict())
    return jsonify({'line_id': line_id, 'tower_label': tower_label, 'inspection_done': False, 'marked_by': '', 'marked_at': '',
                     'zone': '', 'zone_set_by': '', 'zone_set_at': ''})


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/inspection-status', methods=['POST'])
def api_set_inspection_status(line_id, tower_label):
    guard = _inspect_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    data = request.get_json(force=True, silent=True) or {}
    done = bool(data.get('inspection_done'))
    # Completion is a tower-level SME decision. Clean images require no
    # per-image mark; the SME browses them, records findings only where
    # present, then marks the tower Inspection Done.
    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    was_done = bool(status and status.inspection_done)

    if not status:
        status = TowerInspectionStatus(line_id=line_id, tower_label=tower_label)
        db.session.add(status)
    status.inspection_done = done
    status.marked_by = session.get('user_name', '') if done else ''
    status.marked_at = datetime.utcnow() if done else None
    if done and not was_done:
        line = Line.query.get(line_id)
        project = line.division.project if line and line.division else None
        notify_users([user.id for user in (project.allowed_users if project else [])],
                     f'Tower {tower_label} is ready',
                     f'{line.name if line else "Assigned line"} inspection is now visible to you.',
                     'inspection', f'/projects/{project.id}/map?line={line_id}' if project else '')
        notify_users([uid for uid in admin_user_ids() if uid != session.get('user_id')],
                     f'Inspection completed — Tower {tower_label}',
                     f'{session.get("user_name", "SME")} completed the inspection.',
                     'inspection', f'/projects/{project.id}/map?line={line_id}' if project else '')
    db.session.commit()
    return jsonify(status.to_dict())


@projects_bp.route('/api/tower-photos/<int:photo_id>/review', methods=['PUT'])
def api_set_photo_review(photo_id):
    guard = _inspect_guard_for_photo(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    data = request.get_json(force=True, silent=True) or {}
    outcome = (data.get('outcome') or '').strip()
    if outcome not in {'Pending', 'Reviewed - No Defect'}:
        return jsonify({'error': 'outcome must be Pending or Reviewed - No Defect.'}), 400
    photo.review_outcome = outcome
    if outcome == 'Pending':
        photo.reviewed_by_user_id = None
        photo.reviewed_by_name = ''
        photo.reviewed_at = None
    else:
        photo.reviewed_by_user_id = session.get('user_id')
        photo.reviewed_by_name = session.get('user_name', '')
        photo.reviewed_at = datetime.utcnow()
    db.session.commit()
    return jsonify(photo.to_dict())


VALID_ZONES = {'red', 'yellow', 'green'}


def _pilot_or_admin_guard(line_id):
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') == 'Admin':
        return None
    if session.get('role') != 'Pilot':
        return jsonify({'error': 'Only Pilot and Admin accounts can set a tower\'s zone.'}), 403
    line = Line.query.get(line_id)
    if not line:
        return jsonify({'error': 'Line not found.'}), 404
    if not can_access_line(line):
        return jsonify({'error': "This line isn't assigned to you."}), 403
    return None


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/zone', methods=['POST'])
def api_set_tower_zone(line_id, tower_label):
    """Pilot's required risk-zone classification for a tower — set before
    capturing a photo there. Admin can also set/correct it, but Client
    never can (view-only, same as everything else client-facing)."""
    guard = _pilot_or_admin_guard(line_id)
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    if _kml_tower_coordinates(line, tower_label) is None:
        return jsonify({'error': 'Tower was not found in this line\'s KML.'}), 400
    data = request.get_json(force=True, silent=True) or {}
    zone = (data.get('zone') or '').strip().lower()
    if zone not in VALID_ZONES:
        return jsonify({'error': "Zone must be 'red', 'yellow', or 'green'."}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status:
        status = TowerInspectionStatus(line_id=line_id, tower_label=tower_label)
        db.session.add(status)
    status.zone = zone
    status.zone_set_by = session.get('user_name', '')
    status.zone_set_at = datetime.utcnow()
    db.session.commit()
    return jsonify(status.to_dict())


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/pilot-submit', methods=['POST'])
def api_pilot_submit_tower(line_id, tower_label):
    """Pilot's "done here" for a tower — zone must already be set; photos
    themselves come from the actual drone, not through this app, so
    there's nothing to upload here. This just closes out the visit."""
    guard = _pilot_or_admin_guard(line_id)
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    if _kml_tower_coordinates(line, tower_label) is None:
        return jsonify({'error': 'Tower was not found in this line\'s KML.'}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.zone:
        return jsonify({'error': 'Set a zone (red/yellow/green) for this tower before submitting.'}), 400
    status.pilot_submitted = True
    status.pilot_submitted_by = session.get('user_name', '')
    status.pilot_submitted_at = datetime.utcnow()
    db.session.commit()
    return jsonify(status.to_dict())


@projects_bp.route('/api/pilot/location', methods=['POST'])
def api_ping_pilot_location():
    """The Pilot's own device reports its current position here,
    periodically, while their portal tab is open — with their consent
    (browser geolocation permission). Overwrites their previous ping;
    this isn't a location history, just "where are they right now"."""
    guard = _pilot_guard()
    if guard:
        return guard
    data = request.get_json(force=True, silent=True) or {}
    try:
        lat = float(data.get('lat'))
        lng = float(data.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'lat and lng are required numbers.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'lat/lng out of range.'}), 400

    loc = PilotLocation.query.get(session.get('user_id'))
    if not loc:
        loc = PilotLocation(pilot_user_id=session.get('user_id'), lat=lat, lng=lng)
        db.session.add(loc)
    else:
        loc.lat, loc.lng, loc.updated_at = lat, lng, datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/pilot-locations', methods=['GET'])
def api_list_pilot_locations():
    """Every Pilot's last-known position — for Admin's live map. Only
    pings from the last 30 minutes count as "current"; anything older
    means that pilot's tab has likely been closed a while, so they're
    left off rather than showing a stale dot."""
    guard = _admin_guard()
    if guard:
        return guard
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    locs = PilotLocation.query.filter(PilotLocation.updated_at >= cutoff).all()
    return jsonify({'locations': [l.to_dict() for l in locs]})


@projects_bp.route('/api/pilots', methods=['GET'])
def api_list_pilots():
    """Users with the Pilot role, for the "Assign Pilot" dropdown when
    uploading/editing a Line's KML."""
    guard = _admin_guard()
    if guard:
        return guard
    from models import Role
    pilot_role = Role.query.filter_by(name='Pilot').first()
    pilots = pilot_role.users if pilot_role else []
    return jsonify({'pilots': [{'id': p.id, 'username': p.username, 'email': p.email} for p in pilots]})


@projects_bp.route('/api/lines/<int:line_id>/assign-pilot', methods=['GET'])
def api_get_line_assignment(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    assignment = PilotAssignment.query.filter_by(line_id=line_id).first()
    return jsonify({'assignment': assignment.to_dict() if assignment else None})


@projects_bp.route('/api/lines/<int:line_id>/assign-pilot', methods=['POST'])
def api_assign_pilot(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    pilot_user_id = data.get('pilot_user_id')

    if not pilot_user_id:
        # Explicit unassign — clears whoever's currently on this line.
        PilotAssignment.query.filter_by(line_id=line_id).delete()
        db.session.commit()
        return jsonify({'ok': True, 'assignment': None})

    pilot = User.query.get(int(pilot_user_id))
    if not pilot or 'Pilot' not in pilot.role_names():
        return jsonify({'error': 'That user does not have the Pilot role.'}), 400

    # One pilot per line at a time — reassigning replaces rather than adds.
    PilotAssignment.query.filter_by(line_id=line_id).delete()
    assignment = PilotAssignment(
        line_id=line_id, pilot_user_id=pilot.id,
        assigned_by=session.get('user_name', ''), seen_by_pilot=False,
    )
    db.session.add(assignment)
    ActivityLog.log(action='assign_pilot', entity_type='Line', entity_name=line.name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=f'Assigned to pilot {pilot.username}')
    db.session.commit()
    return jsonify({'ok': True, 'assignment': assignment.to_dict()})


@projects_bp.route('/api/smes', methods=['GET'])
def api_list_smes():
    """Users with the SME role, for the "Assign SME" dropdown in a Line's
    Details modal."""
    guard = _admin_guard()
    if guard:
        return guard
    from models import Role
    sme_role = Role.query.filter_by(name='SME').first()
    smes = sme_role.users if sme_role else []
    return jsonify({'smes': [{'id': u.id, 'username': u.username, 'email': u.email} for u in smes]})


@projects_bp.route('/api/lines/<int:line_id>/assign-sme', methods=['GET'])
def api_get_line_sme_assignment(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    assignment = SmeAssignment.query.filter_by(line_id=line_id).first()
    return jsonify({'assignment': assignment.to_dict() if assignment else None})


@projects_bp.route('/api/smes/<int:sme_user_id>/assignments', methods=['GET'])
def api_sme_all_assignments(sme_user_id):
    """Every line a specific SME is currently assigned to, across the
    whole system — not just the one line a Details modal happens to be
    open for. Exists so Admin can see (and clean up) a case like "this
    SME got assigned to 6 lines over several testing attempts, only 1
    of which is actually correct" instead of having to check line by
    line."""
    guard = _admin_guard()
    if guard:
        return guard
    rows = SmeAssignment.query.filter_by(sme_user_id=sme_user_id).order_by(SmeAssignment.assigned_at.desc()).all()
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        out.append({
            'assignment_id': a.id, 'line_id': a.line_id,
            'line_name': line.name if line else '—',
            'division_name': division.name if division else '—',
            'assigned_at': a.assigned_at.strftime('%d %b %Y, %H:%M') if a.assigned_at else '',
        })
    return jsonify({'assignments': out})


@projects_bp.route('/api/sme-assignments/<int:assignment_id>', methods=['DELETE'])
def api_delete_sme_assignment(assignment_id):
    """Unassign one specific SME-line pairing directly, by assignment id —
    for cleaning up extras from the all-assignments list above, without
    needing to open that particular line's own Details modal."""
    guard = _admin_guard()
    if guard:
        return guard
    assignment = SmeAssignment.query.get_or_404(assignment_id)
    db.session.delete(assignment)
    db.session.commit()
    return jsonify({'deleted': assignment_id})


@projects_bp.route('/api/lines/<int:line_id>/assign-sme', methods=['POST'])
def api_assign_sme(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    sme_user_id = data.get('sme_user_id')

    if not sme_user_id:
        # Explicit unassign — clears whoever's currently on this line.
        SmeAssignment.query.filter_by(line_id=line_id).delete()
        db.session.commit()
        return jsonify({'ok': True, 'assignment': None})

    sme = User.query.get(int(sme_user_id))
    if not sme or 'SME' not in sme.role_names():
        return jsonify({'error': 'That user does not have the SME role.'}), 400

    # One SME per line at a time — reassigning replaces rather than adds.
    SmeAssignment.query.filter_by(line_id=line_id).delete()
    assignment = SmeAssignment(
        line_id=line_id, sme_user_id=sme.id,
        assigned_by=session.get('user_name', ''), seen_by_sme=False,
    )
    db.session.add(assignment)
    project = line.division.project if line.division else None
    notify_user(sme.id, f'New line assigned — {line.name}',
                f'{project.name if project else "Project"} · assigned by {session.get("user_name", "Admin")}.',
                'assignment', f'/projects/{project.id}/map?line={line.id}' if project else '')
    ActivityLog.log(action='assign_sme', entity_type='Line', entity_name=line.name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=f'Assigned to SME {sme.username}')
    db.session.commit()
    return jsonify({'ok': True, 'assignment': assignment.to_dict()})


@projects_bp.route('/api/pilot/assignments', methods=['GET'])
def api_pilot_assignments():
    """The current Pilot's own assigned work — what their dashboard's
    "Assigned Work" list and new-assignment popup are built from."""
    guard = _pilot_guard()
    if guard:
        return guard
    rows = (PilotAssignment.query
            .filter_by(pilot_user_id=session.get('user_id'))
            .order_by(PilotAssignment.assigned_at.desc()).all())
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        project = division.project if division else None
        entry = a.to_dict()
        entry['line_name'] = line.name if line else '—'
        entry['division_name'] = division.name if division else '—'
        entry['project_name'] = project.name if project else '—'
        entry['project_module'] = project.module if project else ''
        entry['kml_url'] = stored_url(line.kml_path) if line else ''
        out.append(entry)
    return jsonify({'assignments': out})


@projects_bp.route('/api/pilot/assignments/<int:assignment_id>/acknowledge', methods=['POST'])
def api_acknowledge_assignment(assignment_id):
    guard = _pilot_guard()
    if guard:
        return guard
    assignment = PilotAssignment.query.get_or_404(assignment_id)
    if assignment.pilot_user_id != session.get('user_id'):
        return jsonify({'error': 'Not your assignment.'}), 403
    assignment.seen_by_pilot = True
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/sme/assignments', methods=['GET'])
def api_sme_assignments():
    """The current SME's own assigned work — what their dashboard's card
    grid and new-assignment popup are built from. Each entry also carries
    a towers-done/total tally and how many days it's been since Admin
    assigned it, since the dashboard shows both as a completion
    indicator."""
    guard = _sme_guard()
    if guard:
        return guard
    rows = (SmeAssignment.query
            .filter_by(sme_user_id=session.get('user_id'))
            .order_by(SmeAssignment.assigned_at.desc()).all())
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        project = division.project if division else None
        entry = a.to_dict()
        entry['line_name'] = line.name if line else '—'
        entry['division_name'] = division.name if division else '—'
        entry['project_name'] = project.name if project else '—'
        entry['project_id'] = project.id if project else None
        entry['project_module'] = project.module if project else ''
        entry['tower_count'] = line.tower_count if line else 0
        photos = TowerPhoto.query.filter_by(line_id=a.line_id).order_by(TowerPhoto.id.asc()).all() if line else []
        uploaded_labels = sorted(
            {p.tower_label for p in photos if p.tower_label},
            key=lambda value: [(0, int(part)) if part.isdigit() else (1, part.casefold()) for part in re.split(r'(\d+)', value)],
        )
        done_labels = {
            row.tower_label for row in
            TowerInspectionStatus.query.filter_by(line_id=a.line_id, inspection_done=True).all()
        } if line else set()
        pending_labels = [label for label in uploaded_labels if label not in done_labels]
        thermal_photos = [photo for photo in photos if photo.is_thermal_image()]
        rgb_photos = [photo for photo in photos if not photo.is_thermal_image()]
        entry['uploaded_towers'] = len(uploaded_labels)
        entry['towers_done'] = len(done_labels)
        entry['review_pending'] = len(pending_labels)
        entry['pending_tower_labels'] = pending_labels
        entry['next_pending_tower'] = pending_labels[0] if pending_labels else ''
        entry['rgb_images'] = len(rgb_photos)
        entry['thermal_images'] = len(thermal_photos)
        entry['client_visible_towers'] = len(done_labels)
        entry['days_since_assigned'] = (datetime.utcnow() - a.assigned_at).days if a.assigned_at else 0
        out.append(entry)
    return jsonify({'assignments': out})


@projects_bp.route('/api/sme/assignments/<int:assignment_id>/acknowledge', methods=['POST'])
def api_acknowledge_sme_assignment(assignment_id):
    guard = _sme_guard()
    if guard:
        return guard
    assignment = SmeAssignment.query.get_or_404(assignment_id)
    if assignment.sme_user_id != session.get('user_id'):
        return jsonify({'error': 'Not your assignment.'}), 403
    assignment.seen_by_sme = True
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/lines/<int:line_id>/tower-photos/pilot-capture', methods=['POST'])
def api_pilot_capture_photo(line_id):
    """Upload path for the Pilot's in-app live camera capture.

    Unlike api_upload_tower_photo above (which reads GPS from the
    photo's own EXIF), a canvas-captured frame from getUserMedia has NO
    EXIF data at all — there's nothing embedded to check. So this
    validates against capture_lat/capture_lng instead: the device's live
    GPS reading taken via navigator.geolocation at the moment the pilot
    pressed the shutter, sent alongside the image. Same 70m buffer and
    same rejection behavior as the EXIF-based path, just a different
    source of truth for "where was this actually taken.\""""
    guard = _pilot_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    if not can_access_line(line):
        return jsonify({'error': "This line isn't assigned to you."}), 403

    tower_label = (request.form.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.zone:
        return jsonify({'error': 'Set this tower\'s zone (Red/Yellow/Green) before capturing a photo.'}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    tower_coords = _kml_tower_coordinates(line, tower_label)
    if tower_coords is None:
        return jsonify({'error': 'Tower was not found in this line\'s KML.'}), 400
    tower_lat, tower_lng = tower_coords

    try:
        capture_lat = float(request.form.get('capture_lat'))
        capture_lng = float(request.form.get('capture_lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Missing location data — GPS must be available to capture.'}), 400
    if not (-90 <= capture_lat <= 90) or not (-180 <= capture_lng <= 180):
        return jsonify({'error': 'Capture GPS coordinates are out of range.'}), 400

    dist_m = _haversine_km(capture_lat, capture_lng, tower_lat, tower_lng) * 1000
    if dist_m > TOWER_PHOTO_GPS_BUFFER_M:
        return jsonify({
            'error': f"You're about {round(dist_m)}m from this tower — you need to be within "
                     f"{TOWER_PHOTO_GPS_BUFFER_M}m to capture it. Please move to the correct location."
        }), 400

    project = line.division.project if line.division else None
    saved, thumb = _save_tower_photo(image_file, project, line.division, line, tower_label, IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = TowerPhoto(
        line_id=line_id, tower_label=tower_label, image_path=saved, thumbnail_path=thumb,
        uploaded_by=session.get('user_name', ''),
        gps_lat=capture_lat, gps_lng=capture_lng,
    )
    db.session.add(photo)
    ActivityLog.log(action='capture_photo', entity_type='TowerPhoto',
                     entity_name=f"Tower {tower_label} — {line.name}",
                     module='TRANS', performed_by=session.get('user_name', ''), role='Pilot',
                     details=f'Captured within {round(dist_m)}m of tower location')
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/tower-photos/<int:photo_id>', methods=['DELETE'])
def api_delete_tower_photo(photo_id):
    """Deletes the DB row and its actual file(s) on disk — both the raw
    copy and the defects/ duplicate if one was ever made. Previously
    only removed the database row, silently leaving the real file(s)
    behind on every single-photo delete."""
    guard = _admin_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)

    stored_paths = {photo.image_path, photo.thumbnail_path, photo.defect_copy_path}
    stored_paths.update(
        event.evidence_image_path
        for defect in photo.defects for event in defect.resolution_events
        if event.evidence_image_path
    )
    db.session.delete(photo)
    db.session.commit()
    _cleanup_deleted_files(stored_paths, f'tower photo {photo_id}')
    return jsonify({'deleted': photo_id})


# ── Corridor photos — anywhere along the line, not tied to a tower ──────
# Same idea as tower photos (GPS-verified upload), but with no 70m-of-a-
# tower requirement and no per-tower grouping — one flat gallery per line,
# each photo optionally carrying a short observation note. Viewing is
# open to any logged-in role; uploading/editing/deleting is Admin-only.

@projects_bp.route('/api/lines/<int:line_id>/corridor-photos', methods=['GET'])
def api_list_corridor_photos(line_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    photos = CorridorPhoto.query.filter_by(line_id=line_id).order_by(CorridorPhoto.created_at.desc()).all()
    return jsonify({'photos': [p.to_dict() for p in photos]})


@projects_bp.route('/api/lines/<int:line_id>/corridor-photos', methods=['POST'])
def api_upload_corridor_photo(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    image_file.stream.seek(0)
    content_hash = hashlib.sha256(image_file.read()).hexdigest()
    image_file.stream.seek(0)
    if CorridorPhoto.query.filter_by(line_id=line_id, content_hash=content_hash).first():
        return jsonify({'error': f'"{image_file.filename}" is already uploaded for this line — skipped as a duplicate.'}), 400

    # No tower/distance check here (that's the whole point of a corridor
    # photo — it can be anywhere) but GPS itself is still required, since
    # there'd be nowhere to put its map dot otherwise.
    gps = _extract_gps_from_image(image_file)
    if gps is None:
        return jsonify({
            'error': "This photo has no location data in it, so it can't be placed on the map. "
                     "Please upload a photo that has GPS data."
        }), 400

    saved = _save_upload(image_file, 'corridor_photos', IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    thumb_path = _generate_flat_thumbnail(
        current_app.root_path if hasattr(current_app, 'root_path') else '.',
        'corridor_photos', os.path.basename(saved),
    )

    photo = CorridorPhoto(
        line_id=line_id, image_path=saved, gps_lat=gps[0], gps_lng=gps[1],
        uploaded_by=session.get('user_name', ''), thumbnail_path=thumb_path,
        content_hash=content_hash,
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/corridor-photos/<int:photo_id>/observation', methods=['PUT'])
def api_update_corridor_photo_observation(photo_id):
    photo = CorridorPhoto.query.get_or_404(photo_id)
    guard = _inspect_guard(photo.line_id)
    if guard:
        return guard
    data = request.get_json(force=True, silent=True) or {}
    photo.observation = (data.get('observation') or '').strip()
    db.session.commit()
    return jsonify(photo.to_dict())


@projects_bp.route('/api/corridor-photos/<int:photo_id>', methods=['DELETE'])
def api_delete_corridor_photo(photo_id):
    guard = _admin_guard()
    if guard:
        return guard
    photo = CorridorPhoto.query.get_or_404(photo_id)
    stored_paths = {photo.image_path, photo.thumbnail_path}
    db.session.delete(photo)
    db.session.commit()
    _cleanup_deleted_files(stored_paths, f'corridor photo {photo_id}')
    return jsonify({'deleted': photo_id})


# ── Tower defects (marked directly on a tower photo) ────────────────────────
# The 2D equivalent of the chimney module's 3D defect markings — a
# polygon/rectangle/circle drawn over the photo, plus a short observation
# form. Viewing is open to both Admin and Client sessions; marking/editing/
# deleting is Admin-only (Client sessions see the marked-up photos but
# can't add to them).

VALID_SHAPE_TYPES = {'polygon', 'rect', 'circle'}
VALID_LOCATIONS = {'Top', 'Middle', 'Bottom', 'Left', 'Right'}
VALID_SEVERITIES = {'Minor', 'Major', 'Critical'}
SYSTEM_DEFECT_TYPES = {'Grading Ring'}
VALID_DEFECT_STATUSES = {
    # Common physical/component conditions.
    'OK', 'Missing', 'Damaged', 'Loose', 'Broken', 'Cracked', 'Corroded',
    'Bent', 'Displaced', 'Deformed', 'Burnt', 'Contaminated',
    'Not Connected', 'Needs Attention', 'Not Applicable',
    'Partially Missing', 'Rusted', 'Cut', 'Disconnected', 'Leaning',
    'Exposed', 'Dirty', 'Overheated', 'Oil Leakage', 'Flashover Marks',
    'Obstructed', 'Needs Repair', 'Needs Replacement', 'Serviceable',
    'Unserviceable', 'Unable to Verify',
    # Defect-specific inspection results retained by the UI.
    'No', 'Yes', 'Issue Found', 'Not Required', 'Required', 'Done',
    'Good', 'Fair', 'Poor', 'Within Limit', 'Not Within Limit',
    'Connected', 'None Missing', 'Parts Missing',
}


def _defect_taxonomy_error(component_name, defect_type):
    """Validate a component/type pair without blocking built-in universal types."""
    configured_component = InspectionComponent.query.filter_by(
        name=component_name, active=True,
    ).first()
    if InspectionComponent.query.filter_by(active=True).first() and not configured_component:
        return 'Select an active component from the controlled inspection taxonomy.'
    if configured_component and defect_type not in SYSTEM_DEFECT_TYPES:
        taxonomy_type = (InspectionDefectType.query
                         .filter_by(component_id=configured_component.id,
                                    name=defect_type, active=True).first())
        if not taxonomy_type:
            return 'The selected defect type is not active for this component.'
    return ''


def _defect_snapshot(defect):
    return {
        'shape_type': defect.shape_type, 'shape_coords': json.loads(defect.shape_coords or '[]'),
        'component_name': defect.component_name or '', 'location': defect.location or '',
        'defect_type': defect.defect_type or '', 'severity': defect.severity or 'Minor',
        'status': defect.status or 'OK', 'comments': defect.comments or '',
        'resolution_status': defect.resolution_status or 'Open', 'version': defect.version or 1,
    }


def _annotation_event(defect, action, before=None, after=None, reason=''):
    db.session.add(DefectAnnotationEvent(
        defect=defect, action=action,
        before_json=json.dumps(before, separators=(',', ':')) if before is not None else '',
        after_json=json.dumps(after, separators=(',', ':')) if after is not None else '',
        reason=(reason or '')[:255], changed_by_user_id=session.get('user_id'),
        changed_by_name=session.get('user_name', ''), changed_by_role=session.get('role', ''),
    ))


@projects_bp.route('/api/tower-photos/<int:photo_id>/defects', methods=['GET'])
def api_list_tower_defects(photo_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _photo_access_guard(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    guard = _client_photo_release_guard(photo)
    if guard:
        return guard
    defects = (TowerDefect.query.filter_by(tower_photo_id=photo_id, deleted_at=None)
               .order_by(TowerDefect.created_at.asc()).all())
    return jsonify({'defects': [d.to_dict() for d in defects]})


@projects_bp.route('/api/tower-photos/<int:photo_id>/defects', methods=['POST'])
def api_create_tower_defect(photo_id):
    guard = _inspect_guard_for_photo(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)

    data = request.get_json(force=True, silent=True) or {}
    shape_type = (data.get('shape_type') or '').strip()
    if shape_type not in VALID_SHAPE_TYPES:
        return jsonify({'error': 'shape_type must be one of: polygon, rect, circle.'}), 400

    shape_coords = data.get('shape_coords')
    if not isinstance(shape_coords, list) or len(shape_coords) < 2:
        return jsonify({'error': 'shape_coords must be a list of at least 2 {x, y} points.'}), 400
    try:
        clean_coords = []
        for pt in shape_coords:
            x, y = float(pt['x']), float(pt['y'])
            if not (0 <= x <= 100) or not (0 <= y <= 100):
                return jsonify({'error': 'shape_coords must be percentages between 0 and 100.'}), 400
            clean_coords.append({'x': x, 'y': y})
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'Each shape_coords point needs numeric x and y.'}), 400

    location = (data.get('location') or '').strip()
    if location and location not in VALID_LOCATIONS:
        return jsonify({'error': 'location must be one of: Top, Middle, Bottom, Left, Right.'}), 400

    severity = (data.get('severity') or 'Minor').strip()
    if severity not in VALID_SEVERITIES:
        severity = 'Minor'

    # Status keeps both the defect-specific inspection vocabulary and a
    # shared condition vocabulary. Validate it server-side so direct API
    # requests cannot save an unexpected status that filters and reports
    # do not understand.
    status = (data.get('status') or 'OK').strip()
    if status not in VALID_DEFECT_STATUSES:
        return jsonify({'error': 'Select a valid defect condition status.'}), 400

    component_name = (data.get('component_name') or '').strip()
    defect_type = (data.get('defect_type') or '').strip()
    if not component_name:
        return jsonify({'error': 'Select a component before saving the annotation.'}), 400
    if not defect_type:
        return jsonify({'error': 'Select a defect type before saving the annotation.'}), 400

    taxonomy_error = _defect_taxonomy_error(component_name, defect_type)
    if taxonomy_error:
        return jsonify({'error': taxonomy_error}), 400

    defect = TowerDefect(
        tower_photo_id=photo_id,
        shape_type=shape_type,
        shape_coords=json.dumps(clean_coords),
        component_name=component_name,
        location=location,
        defect_type=defect_type,
        severity=severity,
        status=status,
        comments=(data.get('comments') or '').strip(),
        created_by=session.get('user_name', ''),
    )
    db.session.add(defect)
    db.session.flush()
    _annotation_event(defect, 'create', after=_defect_snapshot(defect))
    if severity == 'Critical':
        line = Line.query.get(photo.line_id)
        project = line.division.project if line and line.division else None
        notify_users([uid for uid in admin_user_ids() if uid != session.get('user_id')],
                     f'Critical defect — Tower {photo.tower_label}',
                     defect.component_name or defect.defect_type or 'A critical defect was marked.',
                     'critical', f'/projects/{project.id}/map?line={photo.line_id}' if project else '')
    # First defect ever marked on this photo — make its permanent copy
    # under .../defects/ now, so the raw/ copy can be safely deleted
    # later without losing what this marking depends on.
    _duplicate_photo_for_defects(photo)
    ActivityLog.log(action='mark_defect', entity_type='TowerDefect',
                     entity_name=f"{defect.component_name or 'Defect'} — Tower {photo.tower_label}",
                     performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify(defect.to_dict()), 201


@projects_bp.route('/api/tower-defects/<int:defect_id>/resolve', methods=['POST'])
def api_resolve_tower_defect(defect_id):
    """Marks a defect Closed (rectified in the field) or reopens it.
    Available to both Admin and Client sessions — unlike marking or
    deleting a defect, closing one is a real-world fact the client
    often confirms just as much as Drogo does, so both can record it."""
    guard = _admin_or_client_guard()
    if guard:
        return guard
    defect = TowerDefect.query.get_or_404(defect_id)
    if defect.deleted_at:
        return jsonify({'error': 'This annotation has been deleted.'}), 409
    guard = _photo_access_guard(defect.tower_photo_id)
    if guard:
        return guard
    guard = _client_photo_release_guard(defect.photo)
    if guard:
        return guard

    data = request.form if request.content_type and request.content_type.startswith('multipart/form-data') else (request.get_json(force=True, silent=True) or {})
    action = (data.get('action') or 'close').strip().lower()
    if action not in ('close', 'reopen'):
        return jsonify({'error': "action must be 'close' or 'reopen'."}), 400

    comment = (data.get('comment') or '').strip()
    if not comment:
        message = 'A comment explaining what was fixed is required to close a defect.' if action == 'close' else 'A reason is required to reopen a defect.'
        return jsonify({'error': message}), 400

    evidence_path = ''
    evidence = request.files.get('evidence_image')
    if evidence and evidence.filename:
        if action != 'close':
            return jsonify({'error': 'Rectification evidence can be uploaded only when closing a defect.'}), 400
        evidence_path = _save_upload(evidence, 'defect_rectifications', IMAGE_EXTS)
        if evidence_path is None:
            return jsonify({'error': 'Rectification evidence must be a .jpg, .png, or .webp image.'}), 400

    from_status = defect.resolution_status or 'Open'
    if action == 'close':
        defect.resolution_status = 'Closed'
        defect.resolved_by = session.get('user_name', '')
        defect.resolved_at = datetime.utcnow()
        defect.resolution_comment = comment
    else:
        defect.resolution_status = 'Open'
        defect.resolved_by = ''
        defect.resolved_at = None
        defect.resolution_comment = ''

    event = DefectResolutionEvent(
        defect=defect,
        action=action,
        from_status=from_status,
        to_status=defect.resolution_status,
        comment=comment,
        evidence_image_path=evidence_path or '',
        changed_by_user_id=session.get('user_id'),
        changed_by_name=session.get('user_name', ''),
        changed_by_role=session.get('role', ''),
    )
    db.session.add(event)
    photo = defect.photo
    line = Line.query.get(photo.line_id)
    project = line.division.project if line and line.division else None
    title_action = 'closed' if action == 'close' else 'reopened'
    recipients = set(admin_user_ids())
    if project:
        recipients.update(user.id for user in project.allowed_users)
    recipients.discard(session.get('user_id'))
    notify_users(recipients, f'Defect {title_action} — Tower {photo.tower_label}',
                 f'{defect.component_name or defect.defect_type or "Defect"} was {title_action} by {session.get("user_name", "User")}.',
                 'defect', f'/projects/{project.id}/map?line={photo.line_id}' if project else '')

    ActivityLog.log(action=f'{action}_defect', entity_type='TowerDefect',
                     entity_name=f"{defect.component_name or 'Defect'} — Tower {defect.photo.tower_label}",
                     performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify(defect.to_dict())


@projects_bp.route('/api/tower-defects/<int:defect_id>', methods=['DELETE'])
def api_delete_tower_defect(defect_id):
    defect = TowerDefect.query.get_or_404(defect_id)
    guard = _inspect_guard_for_photo(defect.tower_photo_id)
    if guard:
        return guard
    if defect.deleted_at:
        return jsonify({'error': 'This annotation is already deleted.'}), 409
    data = request.get_json(silent=True) or {}
    reason = (data.get('reason') or '').strip()
    before = _defect_snapshot(defect)
    defect.deleted_at = datetime.utcnow()
    defect.deleted_by_user_id = session.get('user_id')
    defect.deleted_by_name = session.get('user_name', '')
    defect.deletion_reason = reason[:255]
    defect.version = (defect.version or 1) + 1
    _annotation_event(defect, 'delete', before=before, reason=reason)
    ActivityLog.log(action='delete_annotation', entity_type='TowerDefect',
                    entity_name=f"{defect.component_name or 'Defect'} — Tower {defect.photo.tower_label}",
                    performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify({'deleted': defect_id})


@projects_bp.route('/api/tower-defects/<int:defect_id>', methods=['PATCH'])
def api_update_tower_defect(defect_id):
    defect = TowerDefect.query.get_or_404(defect_id)
    guard = _inspect_guard_for_photo(defect.tower_photo_id)
    if guard:
        return guard
    if defect.deleted_at:
        return jsonify({'error': 'Restore this annotation before editing it.'}), 409
    data = request.get_json(force=True, silent=True) or {}
    expected_version = data.get('version')
    try:
        version_mismatch = (expected_version is not None and
                            int(expected_version) != (defect.version or 1))
    except (TypeError, ValueError):
        return jsonify({'error': 'A valid annotation version is required.'}), 400
    if version_mismatch:
        return jsonify({'error': 'This annotation was changed by another user. Reload it before saving.',
                        'current': defect.to_dict()}), 409

    component_name = (data.get('component_name', defect.component_name) or '').strip()[:150]
    defect_type = (data.get('defect_type', defect.defect_type) or '').strip()[:100]
    location = (data.get('location', defect.location) or '').strip()[:20]
    status = (data.get('status', defect.status or 'OK') or '').strip()[:20]
    severity = (data.get('severity', defect.severity or 'Minor') or '').strip()
    if not component_name or not defect_type:
        return jsonify({'error': 'Component and defect type are required.'}), 400
    if location and location not in VALID_LOCATIONS:
        return jsonify({'error': 'location must be one of: Top, Middle, Bottom, Left, Right.'}), 400
    if status not in VALID_DEFECT_STATUSES:
        return jsonify({'error': 'Select a valid defect condition status.'}), 400
    if severity not in VALID_SEVERITIES:
        return jsonify({'error': 'Severity must be Minor, Major or Critical.'}), 400
    taxonomy_error = _defect_taxonomy_error(component_name, defect_type)
    if taxonomy_error:
        return jsonify({'error': taxonomy_error}), 400

    before = _defect_snapshot(defect)
    defect.component_name = component_name
    defect.defect_type = defect_type
    defect.location = location
    defect.status = status
    defect.severity = severity
    if 'comments' in data:
        defect.comments = (data.get('comments') or '').strip()[:2000]
    if 'observation' in data:
        defect.observation = (data.get('observation') or '').strip()[:255]
    defect.version = (defect.version or 1) + 1
    after = _defect_snapshot(defect)
    _annotation_event(defect, 'update', before=before, after=after)
    db.session.commit()
    return jsonify(defect.to_dict())


@projects_bp.route('/api/tower-defects/<int:defect_id>/restore', methods=['POST'])
def api_restore_tower_defect(defect_id):
    guard = _admin_guard()
    if guard:
        return guard
    defect = TowerDefect.query.get_or_404(defect_id)
    if not defect.deleted_at:
        return jsonify({'error': 'This annotation is already active.'}), 409
    defect.deleted_at = None
    defect.deleted_by_user_id = None
    defect.deleted_by_name = ''
    defect.deletion_reason = ''
    defect.version = (defect.version or 1) + 1
    _annotation_event(defect, 'restore', after=_defect_snapshot(defect))
    db.session.commit()
    return jsonify(defect.to_dict())


@projects_bp.route('/api/lines/<int:line_id>/delete-raw-images', methods=['POST'])
def api_delete_raw_images(line_id):
    """Safely remove only redundant RGB originals.

    Radiometric thermal originals are never deleted because the embedded DJI
    data is required for future measurements. RGB originals are removed only
    when a verified full-quality defect/evidence copy already exists. Photos
    without a surviving copy are reported as skipped and remain untouched.
    """
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    photos = TowerPhoto.query.filter_by(line_id=line_id).all()
    storage = get_storage()

    deleted_count = 0
    freed_bytes = 0
    skipped_thermal = 0
    skipped_no_copy = 0
    for photo in photos:
        if photo.raw_deleted or not photo.image_path:
            continue
        if photo.is_thermal_image():
            skipped_thermal += 1
            continue
        # A photo with defects but no duplicate yet (shouldn't normally
        # happen — duplication happens at defect-creation time — but
        # never delete evidence on the strength of an assumption) gets
        # one made right now before its raw copy is touched.
        if photo.defects and not photo.defect_copy_path:
            _duplicate_photo_for_defects(photo)

        if not photo.defect_copy_path or not storage.exists(photo.defect_copy_path):
            skipped_no_copy += 1
            continue
        try:
            freed_bytes += storage.size(photo.image_path)
            if storage.delete(photo.image_path):
                deleted_count += 1
        except (OSError, RuntimeError, ValueError):
            continue
        if not storage.exists(photo.image_path):
            photo.raw_deleted = True

    db.session.commit()
    ActivityLog.log(action='delete_raw_images', entity_type='Line', entity_name=line.name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=(f'Deleted {deleted_count} redundant RGB original(s), freed {freed_bytes} bytes; '
                              f'skipped {skipped_thermal} thermal and {skipped_no_copy} unprotected photo(s)'))
    db.session.commit()
    return jsonify({
        'deleted_count': deleted_count,
        'freed_bytes': freed_bytes,
        'skipped_thermal': skipped_thermal,
        'skipped_no_preserved_copy': skipped_no_copy,
    })


# ── Thermal point-temperature measurements ──────────────────────────────
# Click a spot on a thermal photo, get back the actual radiometric
# temperature at that pixel (decoded from the R-JPEG via thermal_decode.py
# / the DJI Thermal SDK) — same idea as DJI Thermal Analysis Tool 3's point
# measurement. Viewing is open to Admin + Client (same as defects); adding/
# deleting a point is Admin-only.

@projects_bp.route('/api/tower-photos/<int:photo_id>/thermal-points', methods=['GET'])
def api_list_thermal_points(photo_id):
    guard = _login_guard()
    if guard:
        return guard
    guard = _photo_access_guard(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    guard = _client_photo_release_guard(photo)
    if guard:
        return guard
    points = (ThermalPoint.query.filter_by(tower_photo_id=photo_id)
              .order_by(ThermalPoint.created_at.asc()).all())

    # Whole-frame min/max — for the color-scale bar next to the image, not
    # tied to any specific measurement. Computed with the current default
    # params (distance/humidity/etc); a per-point override only affects
    # that one point's own reading, not this frame-wide range.
    frame_min_c = frame_max_c = None
    image_abs_path = _storage_working_path(photo.image_path)
    if image_abs_path and os.path.exists(image_abs_path):
        try:
            matrix = thermal_decode.get_temperature_matrix(image_abs_path)
            frame_min_c = float(np.nanmin(matrix))
            frame_max_c = float(np.nanmax(matrix))
        except Exception:  # noqa: BLE001 — the color bar is a nice-to-have, not worth failing the whole list over
            pass

    return jsonify({
        'points': [p.to_dict() for p in points],
        'frame_min_c': frame_min_c,
        'frame_max_c': frame_max_c,
        'default_params': thermal_decode.DEFAULT_PARAMS_DICT,
    })


def _validate_and_measure_thermal(photo, data):
    """Shared by both the persisting (Admin) and ephemeral (any logged-in
    role) thermal measurement endpoints — validates the request body and
    runs the actual decode, without touching the database either way.
    Returns (shape_type, clean_coords, avg_c, min_c, max_c, raw_avg,
    raw_min, raw_max, error) on success, or (None, error_response,
    status_code) on a validation failure the caller should return as-is."""
    shape_type = (data.get('shape_type') or 'point').strip()
    if shape_type not in ('point', 'rect', 'line'):
        return None, jsonify({'error': "shape_type must be one of: point, rect, line."}), 400

    coords_in = data.get('shape_coords')
    min_needed = 1 if shape_type == 'point' else 2
    if not isinstance(coords_in, list) or len(coords_in) < min_needed:
        return None, jsonify({'error': f'{shape_type} needs at least {min_needed} coordinate(s).'}), 400
    try:
        clean_coords = []
        for pt in coords_in:
            x, y = float(pt['x']), float(pt['y'])
            if not (0 <= x <= 100) or not (0 <= y <= 100):
                return None, jsonify({'error': 'Coordinates must be percentages between 0 and 100.'}), 400
            clean_coords.append({'x': x, 'y': y})
    except (KeyError, TypeError, ValueError):
        return None, jsonify({'error': 'Each coordinate needs numeric x and y.'}), 400

    # Optional per-measurement override of distance/humidity/emissivity/
    # reflection/ambient_temp (the "Parameters" panel) — any key not sent
    # falls back to thermal_decode.DEFAULT_PARAMS_DICT.
    params_in = data.get('params') or {}
    params_dict = {}
    if isinstance(params_in, dict):
        for key in ('distance', 'humidity', 'emissivity', 'reflection', 'ambient_temp'):
            if key in params_in and params_in[key] not in (None, ''):
                try:
                    params_dict[key] = float(params_in[key])
                except (TypeError, ValueError):
                    return None, jsonify({'error': f'params.{key} must be a number.'}), 400

    # Whether a photo is "thermal" is decided by what's actually IN the
    # file (does it have an embedded radiometric data block?), not by its
    # filename — filename conventions vary/get mangled by uploads, and a
    # naming mismatch shouldn't block a photo that genuinely has real
    # thermal data. Extraction is attempted first; the filename is only
    # used afterward, to word the error usefully if it fails.
    image_abs_path = _storage_working_path(photo.image_path)
    avg_c = min_c = max_c = raw_avg = raw_min = raw_max = None
    error = 'Photo has no saved image file.'
    if image_abs_path and os.path.exists(image_abs_path):
        coords_pct = [(c['x'], c['y']) for c in clean_coords]
        avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error = thermal_decode.get_shape_stats(image_abs_path, shape_type, coords_pct, params_dict)
        if error and isinstance(error, str) and 'No APP3 segments found' in error:
            filename = os.path.basename(photo.image_path or '')
            stem = re.sub(r'\.[^.]+$', '', filename)
            if not re.search(r'_T(_\S+)?$', stem, re.IGNORECASE):
                error = f"This isn't a thermal photo ({filename}) — temperature measurement only works on thermal photos."
    # error column is capped (VARCHAR(255) on some DBs) — the SDK's own
    # error strings can run longer than that (e.g. "file not found at
    # <full path>"), which was crashing the save outright instead of
    # storing a shorter version of the same message.
    if error and len(error) > 255:
        error = error[:252] + '...'

    return (shape_type, clean_coords, avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error), None, None


@projects_bp.route('/api/tower-photos/<int:photo_id>/thermal-points', methods=['POST'])
def api_create_thermal_point(photo_id):
    guard = _inspect_guard_for_photo(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    data = request.get_json(force=True, silent=True) or {}

    result, err_response, err_status = _validate_and_measure_thermal(photo, data)
    if result is None:
        return err_response, err_status
    shape_type, clean_coords, avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error = result

    point = ThermalPoint(
        tower_photo_id=photo_id,
        x_pct=clean_coords[0]['x'],
        y_pct=clean_coords[0]['y'],
        shape_type=shape_type,
        shape_coords=json.dumps(clean_coords),
        temperature_c=avg_c,
        min_c=min_c,
        max_c=max_c,
        avg_c=avg_c,
        raw_avg=raw_avg,
        raw_min=raw_min,
        raw_max=raw_max,
        error=error,
        label=(data.get('label') or '').strip(),
        created_by=session.get('user_name', ''),
    )
    db.session.add(point)
    db.session.commit()
    return jsonify(point.to_dict()), 201


@projects_bp.route('/api/tower-photos/<int:photo_id>/thermal-measure', methods=['POST'])
def api_measure_thermal_ephemeral(photo_id):
    """Same decode as thermal-points, but nothing is written to the
    database — for Client, who can measure temperature on a thermal photo
    to explore it themselves, but can't leave a permanent marker. Open to
    any logged-in role (Admin included, though Admin normally uses the
    persisting endpoint above); the response shape matches
    ThermalPoint.to_dict() so the frontend can render it identically."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _photo_access_guard(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    guard = _client_photo_release_guard(photo)
    if guard:
        return guard
    data = request.get_json(force=True, silent=True) or {}

    result, err_response, err_status = _validate_and_measure_thermal(photo, data)
    if result is None:
        return err_response, err_status
    shape_type, clean_coords, avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error = result

    return jsonify({
        'id': None,
        'tower_photo_id': photo_id,
        'x_pct': clean_coords[0]['x'],
        'y_pct': clean_coords[0]['y'],
        'shape_type': shape_type,
        'shape_coords': clean_coords,
        'temperature_c': avg_c,
        'min_c': min_c,
        'max_c': max_c,
        'avg_c': avg_c,
        'raw_avg': raw_avg,
        'raw_min': raw_min,
        'raw_max': raw_max,
        'label': '',
        'error': error,
        'created_by': session.get('user_name', ''),
        'created_at': '',
        'ephemeral': True,
    }), 200


@projects_bp.route('/api/thermal-points/<int:point_id>', methods=['DELETE'])
def api_delete_thermal_point(point_id):
    point = ThermalPoint.query.get_or_404(point_id)
    guard = _inspect_guard_for_photo(point.tower_photo_id)
    if guard:
        return guard
    db.session.delete(point)
    db.session.commit()
    return jsonify({'deleted': point_id})


# ── Source-backed AI inspection summary ─────────────────────────────────

def _tower_summary_sources(line_id, tower_label):
    photos = TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label).all()
    photo_by_id = {p.id: p for p in photos}
    photo_ids = list(photo_by_id)
    if not photo_ids:
        return []
    sources = []
    defects = (TowerDefect.query.filter(TowerDefect.tower_photo_id.in_(photo_ids), TowerDefect.deleted_at.is_(None))
               .order_by(TowerDefect.id.asc()).limit(200).all())
    for defect in defects:
        photo = photo_by_id[defect.tower_photo_id]
        sources.append({
            'id': f'D{defect.id}', 'kind': 'defect', 'record_id': defect.id,
            'photo_id': photo.id, 'url': stored_url(photo.display_image_path()),
            'title': f'{defect.severity or "Minor"} {defect.defect_type or "defect"}',
            'fact': (f'Component: {defect.component_name or "unspecified"}; location: {defect.location or "unspecified"}; '
                     f'defect: {defect.defect_type or "unspecified"}; severity: {defect.severity or "Minor"}; '
                     f'condition: {defect.status or "OK"}; resolution: {defect.resolution_status or "Open"}; '
                     f'observation: {defect.observation or "none"}.')
        })
    points = (ThermalPoint.query.filter(ThermalPoint.tower_photo_id.in_(photo_ids))
              .order_by(ThermalPoint.id.asc()).limit(200).all())
    for point in points:
        photo = photo_by_id[point.tower_photo_id]
        temp = 'unavailable' if point.avg_c is None else f'{point.avg_c:.2f} C average'
        span = '' if point.min_c is None or point.max_c is None else f', {point.min_c:.2f}-{point.max_c:.2f} C range'
        sources.append({
            'id': f'T{point.id}', 'kind': 'thermal', 'record_id': point.id,
            'photo_id': photo.id, 'url': stored_url(photo.display_image_path()),
            'title': point.label or f'Thermal measurement {point.id}',
            'fact': f'Label: {point.label or "unlabelled"}; shape: {point.shape_type or "point"}; temperature: {temp}{span}; error: {point.error or "none"}.'
        })
    return sources


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/ai-summary', methods=['GET'])
def api_get_ai_inspection_summary(line_id, tower_label):
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    tower_label = tower_label.strip()
    if session.get('role') == 'Client User':
        guard = _client_tower_release_guard(line_id, tower_label)
        if guard:
            return guard
    summary = AiInspectionSummary.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if summary and session.get('role') == 'Client User' and not summary.is_shared:
        summary = None
    return jsonify({'summary': summary.to_dict() if summary else None})


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/ai-summary', methods=['POST'])
def api_generate_ai_inspection_summary(line_id, tower_label):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.inspection_done:
        return jsonify({'error': 'Mark this tower Inspection Done before generating its AI summary.'}), 400
    api_key = os.environ.get('GEMINI_API_KEY_OVERRIDE') or os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return jsonify({'error': 'GEMINI_API_KEY is not configured.'}), 503
    sources = _tower_summary_sources(line_id, tower_label)
    if not sources:
        return jsonify({'error': 'This tower has no recorded defect or thermal findings to summarize.'}), 400
    allowed_ids = {source['id'] for source in sources}
    facts = '\n'.join(f'[{source["id"]}] {source["fact"]}' for source in sources)
    prompt = f'''Create a concise engineering inspection summary for tower {tower_label} on line {line.name}.
Use ONLY the source facts below. Do not infer causes, urgency, safety, or repairs that are not explicitly recorded.
Return strict JSON only: {{"findings":[{{"text":"one factual conclusion","source_ids":["D1"]}}]}}.
Every finding must contain at least one exact source ID. Prefer 3-8 useful findings, grouping related records when accurate.

SOURCE FACTS
{facts}'''
    try:
        from google import genai
        from google.genai import types
        # Keep a strong reference to the SDK client until the response is
        # complete.  Chaining ``genai.Client(...).models.generate_content``
        # lets newer SDK versions dispose the temporary Client while its HTTP
        # transport is still in use, producing "client has been closed".
        client = genai.Client(api_key=api_key)
        try:
            response = client.models.generate_content(
                model='gemini-3.1-flash-lite', contents=prompt,
                config=types.GenerateContentConfig(response_mime_type='application/json', temperature=0.1),
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
        parsed = json.loads(response.text or '{}')
        findings = parsed.get('findings') if isinstance(parsed, dict) else None
        if not isinstance(findings, list) or not findings:
            raise ValueError('The AI returned no usable findings.')
        validated = []
        for finding in findings[:12]:
            text_value = str(finding.get('text') or '').strip()[:800]
            source_ids = [str(value) for value in (finding.get('source_ids') or [])]
            source_ids = list(dict.fromkeys(value for value in source_ids if value in allowed_ids))
            if not text_value or not source_ids:
                raise ValueError('The AI returned a finding without a valid source reference.')
            validated.append({'text': text_value, 'source_ids': source_ids})
    except Exception as exc:
        current_app.logger.exception('AI summary generation failed for line %s tower %s', line_id, tower_label)
        return jsonify({'error': f'AI summary could not be generated safely: {exc}'}), 502

    summary = AiInspectionSummary.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not summary:
        summary = AiInspectionSummary(line_id=line_id, tower_label=tower_label)
        db.session.add(summary)
    summary.findings_json = json.dumps(validated)
    summary.sources_json = json.dumps([{k: v for k, v in source.items() if k != 'fact'} for source in sources])
    summary.is_shared = False  # regeneration always requires an explicit new Client release
    summary.generated_by_user_id = session.get('user_id')
    summary.generated_by_name = session.get('user_name', '')
    summary.generated_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'summary': summary.to_dict()})


@projects_bp.route('/api/ai-inspection-summaries/<int:summary_id>/sharing', methods=['PATCH'])
def api_share_ai_inspection_summary(summary_id):
    guard = _admin_guard()
    if guard:
        return guard
    summary = AiInspectionSummary.query.get_or_404(summary_id)
    data = request.get_json(force=True, silent=True) or {}
    summary.is_shared = bool(data.get('is_shared'))
    db.session.commit()
    return jsonify({'summary': summary.to_dict()})


# ── Per-tower RGB Visual Inspection Report ──────────────────────────────
# Generation is Admin-only; once generated, the PDF is stored on disk with
# a TowerReport row pointing at it, so a Client session can download the
# already-generated report without being able to trigger generation
# itself. Re-generating replaces the existing file/row for that tower
# rather than accumulating duplicates.

@projects_bp.route('/api/lines/<int:line_id>/tower-report', methods=['GET'])
def api_get_tower_report(line_id):
    """Does a report already exist for this tower? Used by the panel to
    decide whether to show a "Download Report" link."""
    guard = _login_guard()
    if guard:
        return guard
    guard = _line_access_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    guard = _client_tower_release_guard(line_id, tower_label)
    if guard:
        return guard
    report = (TowerReport.query
              .filter_by(line_id=line_id, tower_label=tower_label)
              .order_by(TowerReport.generated_at.desc()).first())
    return jsonify({'report': report.to_dict() if report else None})


@projects_bp.route('/api/lines/<int:line_id>/tower-report', methods=['POST'])
def api_generate_tower_report(line_id):
    guard = _inspect_guard(line_id)
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    data = request.get_json(force=True, silent=True) or {}
    tower_label = (data.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.inspection_done:
        return jsonify({'error': 'This tower has not been marked "Inspection Done" yet — mark it done before generating a report.'}), 400

    try:
        tower_lat = float(data.get('tower_lat'))
        tower_lng = float(data.get('tower_lng'))
        coordinates = f'{tower_lat:.5f}, {tower_lng:.5f}'
    except (TypeError, ValueError):
        coordinates = '—'

    photos = TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label).all()
    photo_ids = [p.id for p in photos]
    defects = []
    if photo_ids:
        defects = (TowerDefect.query
                   .filter(TowerDefect.tower_photo_id.in_(photo_ids), TowerDefect.deleted_at.is_(None))
                   .order_by(TowerDefect.created_at.asc()).all())

    photo_by_id = {p.id: p for p in photos}
    defect_dicts = []
    for d in defects:
        photo = photo_by_id.get(d.tower_photo_id)
        entry = d.to_dict()
        entry['image_path'] = photo.display_image_path() if photo else ''
        defect_dicts.append(entry)

    # Thermal — RGB always comes first in the report, thermal appended
    # after (see build_tower_report_pdf); one entry per thermal photo
    # that has at least one measurement on it, each carrying its own
    # SP1/SP2/… points list.
    thermal_points = []
    if photo_ids:
        thermal_points = (ThermalPoint.query
                          .filter(ThermalPoint.tower_photo_id.in_(photo_ids))
                          .order_by(ThermalPoint.tower_photo_id.asc(), ThermalPoint.created_at.asc()).all())
    thermal_by_photo = {}
    for pt in thermal_points:
        thermal_by_photo.setdefault(pt.tower_photo_id, []).append(pt.to_dict())
    thermal_photo_dicts = [
        {'image_path': photo_by_id[photo_id].display_image_path() if photo_by_id.get(photo_id) else '', 'points': pts}
        for photo_id, pts in thermal_by_photo.items()
    ]

    info = {
        'line_name': line.name,
        'tower_id': tower_label,
        'voltage_level': line.voltage_level or '',
        'coordinates': coordinates,
        'survey_date': line.survey_date.strftime('%d %b %Y') if line.survey_date else '',
        'pilot_name': line.pilot_name or '',
        'inspection_name': line.inspection_name or '',
        'report_date': datetime.utcnow().strftime('%d %b %Y'),
    }

    try:
        from tower_report import build_tower_report_pdf
        static_root = os.path.join(current_app.root_path, 'static')
        project = line.division.project if line.division else None
        client_logo_path = project.logo_path if project else ''
        # The PDF renderer expects filesystem paths. Object-backed assets are
        # hydrated into the protected local working cache before rendering.
        for item in defect_dicts:
            _storage_working_path(item.get('image_path'))
        for item in thermal_photo_dicts:
            _storage_working_path(item.get('image_path'))
        _storage_working_path(client_logo_path)
        inspection_types = project.get_inspection_types() if project else ['rgb', 'thermal']
        pdf_buf = build_tower_report_pdf(info, defect_dicts, static_root, client_logo_path, thermal_photo_dicts,
                                          inspection_types=inspection_types)
    except Exception as e:
        current_app.logger.exception('Tower report generation failed for line %s tower %s', line_id, tower_label)
        return jsonify({'error': f'Report generation failed: {e}'}), 500

    # Save to disk under static/uploads/tower_reports/, same pattern as
    # _save_upload() but for a PDF we built ourselves rather than an
    # uploaded file.
    folder_fs = os.path.join(current_app.static_folder, 'uploads', 'tower_reports')
    os.makedirs(folder_fs, exist_ok=True)
    safe_tower = secure_filename(tower_label) or 'tower'
    filename = f'line{line_id}_{safe_tower}_report.pdf'
    full_path = os.path.join(folder_fs, filename)
    with open(full_path, 'wb') as f:
        f.write(pdf_buf.getvalue())
    report_path = f'uploads/tower_reports/{filename}'
    get_storage().publish(report_path, full_path)

    # Replace any existing report for this exact tower rather than
    # accumulating duplicate rows/files each time it's regenerated.
    existing = TowerReport.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if existing:
        existing.report_path = report_path
        existing.generated_by = session.get('user_name', '')
        existing.generated_at = datetime.utcnow()
        report = existing
    else:
        report = TowerReport(
            line_id=line_id, tower_label=tower_label, report_path=report_path,
            generated_by=session.get('user_name', ''),
        )
        db.session.add(report)
    db.session.commit()
    return jsonify(report.to_dict()), 201


@projects_bp.route('/api/tower-reports/<int:report_id>/download', methods=['GET'])
def api_download_tower_report(report_id):
    guard = _login_guard()
    if guard:
        return guard
    report = TowerReport.query.get_or_404(report_id)
    guard = _line_access_guard(report.line_id)
    if guard:
        return guard
    guard = _client_tower_release_guard(report.line_id, report.tower_label)
    if guard:
        return guard
    storage = get_storage()
    if not storage.exists(report.report_path):
        return jsonify({'error': 'Report file not found — try generating it again.'}), 404
    download_name = f'Tower_{secure_filename(report.tower_label)}_Inspection_Report.pdf'
    return storage.response(report.report_path, download_name=download_name)
