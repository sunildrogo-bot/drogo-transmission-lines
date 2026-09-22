# Update 25 — Storage control centre and TRANS cleanup

- Reorganized Settings → Uploads & Storage into a responsive operational layout.
- Preserved project browsing/deletion, storage summary, thumbnail repair,
  media metadata, object-storage migration, backup/recovery, compatibility and
  deployment-health functions.
- Replaced the Land Survey fallback on Project and Division cards with the
  bundled Transmission inspection image.
- Removed retired Chimney source scripts, placeholder image and manual cleanup
  utilities from the release.
- The Windows upgrader removes only those exact retired source files. It does
  not remove database records, migrations, `.env`, `.venv`, `instance`, or any
  directory below `static/uploads`.
