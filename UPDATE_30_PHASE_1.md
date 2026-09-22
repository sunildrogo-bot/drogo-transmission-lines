# Update 30 — Phase 1 Production Reliability

Version: `1.30.1`

Patch 1 closes SQLite handles explicitly for Windows/Python 3.14 backup,
restore-verification, and temporary-database cleanup compatibility.

This cumulative release preserves the existing `.env`, virtual environment,
`instance/nova.db`, and `static/uploads` data when installed with the supplied
upgrade script.

## Completed work

- Repaired the clean SQLite migration path at revision `20260825_0007`.
- Enabled SQLite foreign keys on every connection.
- Enabled WAL mode and a configurable busy timeout.
- Added a dry-run integrity audit and backup-first orphan repair command.
- Fixed custom static/upload roots in the real inspection workflow.
- Added production configuration validation for secret, public HTTPS URL and
  secure cookies.
- Removed default debug-mode startup.
- Added Gunicorn, Docker and Procfile production definitions.
- Added a container supervisor that runs and monitors the web service and job
  worker, performs migrations before boot, and handles shutdown safely.
- Improved the SQLite worker lease so a reused PID is not accepted as the old
  worker on Linux.
- Added streaming S3-compatible offsite checkpoints. Only the small database
  snapshot is temporary on local disk; uploads stream one-by-one.
- Added automatic database restore verification, per-object size verification,
  configurable recurring backups and checkpoint retention.
- Added one-command Windows/Linux verification and eleven Phase 1 runtime tests.

## Local verification

Activate the existing application environment and run:

```text
python scripts/verify_phase1.py
```

The command compiles the source, runs `pip check`, creates and migrates an
isolated fresh database, and runs the complete cumulative test suite. It never
uses the configured production database.

Audit the installed SQLite database without changing it:

```text
python scripts/check_data_integrity.py
```

Exit code `0` means no violations. Exit code `2` means violations were found in
dry-run mode. Only after reviewing the output, run this to create a verified
database backup and repair safe `CASCADE`/`SET NULL` orphan references:

```text
python scripts/check_data_integrity.py --repair
```

Rows governed by `RESTRICT` or `NO ACTION` are reported for manual review and
are never removed automatically.

## Offsite backups

Offsite backup is disabled until a separate S3-compatible bucket is configured.
The credentials are read by boto3 from standard environment variables and are
never stored in the database, manifest or release package.

Important settings:

```text
BACKUP_OFFSITE_ENABLED=true
BACKUP_OFFSITE_BUCKET=your-separate-backup-bucket
BACKUP_OFFSITE_PREFIX=drogo-backups
BACKUP_OFFSITE_ENDPOINT_URL=
BACKUP_OFFSITE_REGION=
BACKUP_OFFSITE_RETENTION_COUNT=3
BACKUP_OFFSITE_INTERVAL_HOURS=24
BACKUP_OFFSITE_VERIFY_EVERY_FILE=true
BACKUP_OFFSITE_SSE=AES256
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
```

The destination must not be the same VPS disk. When configured, the persistent
worker automatically queues a checkpoint at the requested interval. Admin can
also start one under **Settings → Uploads & Storage → Backup & recovery**.

Every completed checkpoint includes the database, all files visible to the
active storage backend and `backup_manifest.json`. Completion is reported only
after the database has been downloaded to a temporary path and passed SQLite
`quick_check` (or the PostgreSQL dump has been downloaded and verified nonempty).

## Production safeguards

When `DEPLOYMENT_ENV=production`, startup is blocked unless:

- `SECRET_KEY` is unique and at least 32 characters.
- `PUBLIC_BASE_URL` is a valid public `https://` origin.
- `SESSION_COOKIE_SECURE=true`.

See `DEPLOYMENT_DOKPLOY.md` before deploying this release.
