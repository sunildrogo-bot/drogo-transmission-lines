# Fixes 8, 14, 15, 16 and 17

## 8 — Login and password-reset throttling

- Added account and IP limits for failed Jinja and React login attempts.
- Added account and IP limits for password-reset requests.
- Limits are stored as keyed hashes in the database so raw emails and IPs are
  not retained in the throttle table.
- Successful login clears that account's failure counter.

## 14 — Safe password-reset links and logs

- External security links now use the trusted `PUBLIC_BASE_URL` setting instead
  of the incoming request Host header.
- Password-reset email failures no longer print live reset links or tokens.
- Reset and account-setup tokens are stored only as SHA-256 digests.

For local development, the default origin is `http://127.0.0.1:5000`. Set
`PUBLIC_BASE_URL` to the final HTTPS origin before deployment.

## 15 — File cleanup after deletion

- Project, division, and line deletion now collects their tracked KML, tower,
  corridor, thumbnail, defect-copy, logo, and tower-report paths before the
  database cascade, then removes those files after the database commit.
- Single tower-photo, corridor-photo, and Settings tower deletion now remove
  all tracked copies, including thumbnails.
- Replacing or deleting a user profile photograph removes the old file.
- Cleanup rejects paths outside `static/uploads` and prunes only empty upload
  directories.

## 16 — Safe deletion of project creators

- `projects.created_by` now uses `ON DELETE SET NULL`.
- User deletion also clears this field before deleting the account, so project
  history survives even before the migration is applied.

## 17 — One-time password setup

- New accounts receive a 24-hour one-time link to set their own password.
- No reusable temporary password is generated, emailed, or returned to the
  browser.
- If mail delivery fails, only the signed-in Admin receives the one-time setup
  link as a manual fallback.
- Setting or resetting a password revokes older signed-in sessions.

Before starting this release, back up the database and run:

```powershell
python -m flask --app app:app db upgrade
```
