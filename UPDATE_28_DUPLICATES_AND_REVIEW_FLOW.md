# Update 28 — Duplicate images and review flow

Application version: `1.28.0`

## Included changes

- Exact duplicate uploads are rejected across the same transmission line.
- Same-name legacy rows without a fingerprint are checked during upload.
- Settings → Uploads & Storage includes a background duplicate scan.
- The scan backfills SHA-256 fingerprints for legacy images without deleting data.
- Password-protected cleanup removes only exact, safe redundant copies.
- Copies carrying review, defect, thermal, rectification, or audit evidence are protected.
- Identical files assigned to different towers are reported for manual review and never auto-deleted.
- The image viewer no longer closes by clicking the black backdrop; use Close or Escape.
- Previous/Next navigation stops at the first and last image instead of wrapping.
- Reaching the final image shows a completion notification.
- Defect-detail inputs opt out of browser/password-manager credential autofill.

## Data and deployment

- No database migration is required.
- Run `python job_worker.py` while using the duplicate scan or cleanup.
- Existing database, uploads, `.env`, `.venv`, migration history, and reports are preserved.
- Test locally before deploying this cumulative release to the public VPS.
