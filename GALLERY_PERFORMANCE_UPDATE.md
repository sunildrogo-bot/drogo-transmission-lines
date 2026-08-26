# Gallery performance update

- Tower and corridor grids use separate 800px JPEG thumbnails at quality 90.
- Originals are never overwritten or recompressed.
- Thumbnail paths are derived safely on Windows, macOS and Linux.
- Unique `<original filename>.thumb.jpg` names prevent case-insensitive
  collisions with DJI originals.
- EXIF orientation is applied to thumbnails only; original EXIF/GPS remains
  untouched.
- Gallery images remain lazy-loaded and appear in windows of 48.
- Clicking a thumbnail opens the original full-quality image.
- Defect marking, thermal decoding, reports and downloads continue using the
  original.

New uploads receive thumbnails automatically. After starting the application,
run `python generate_missing_thumbnails.py` once to backfill existing tower and
corridor images. The command is safe to run again and skips completed items.
