# DJI's own Thermal SDK (vendored)

DJI's real Thermal SDK (DTSDK) — decodes real point temperatures out of a
DJI thermal photo's embedded radiometric data, matching DJI Thermal
Analysis Tool 3's readings exactly (same underlying calibration). Bundled
for BOTH Linux (the production server) and Windows (local dev machines).

- `lib/linux/release_x64/` — Linux x86-64 build (`libdirp.so` + deps,
  including `libv_hirp.so`/MicroTA/MicroIA/MicroJPEG/libexif — needed for
  newer camera models like the M30-series/H20-series, not just the
  original DJI R-JPEG format).
- `lib/windows/release_x64/` — the equivalent Windows build.
- `LICENSE.txt` — DJI's MIT license terms for this SDK (same terms DJI
  ships it under on their own developer download page).

`thermal_decode.py` picks the right platform's copy automatically.
`THERMAL_SDK_DIR` in `.env` overrides it — e.g. point it at your own local
SDK install if you'd rather use that copy instead.
