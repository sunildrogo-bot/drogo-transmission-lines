# Dokploy Deployment — v1.32.0

Do not deploy until the laptop verification passes. Keep the existing Dokploy
mounts at `/app/instance` and `/app/static/uploads`; never recreate or delete
those volumes.

## Required production environment

Keep all existing values and add or confirm:

```text
DEPLOYMENT_ENV=production
PUBLIC_BASE_URL=https://YOUR-REAL-PORTAL-DOMAIN
SESSION_COOKIE_SECURE=true
TRUST_PROXY_COUNT=1
SQLITE_JOURNAL_MODE=WAL
SQLITE_BUSY_TIMEOUT_MS=30000
RUN_MIGRATIONS_ON_START=true
WEB_CONCURRENCY=2
WEB_THREADS=4
WEB_TIMEOUT_SECONDS=300
```

Generate a secret once and store the output as `SECRET_KEY` in Dokploy:

```text
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Changing `SECRET_KEY` signs users out but does not change passwords or data.
Never paste the generated value into GitHub or chat.

## Container command

The Dockerfile default is:

```text
python production_start.py
```

It runs migrations first, starts Gunicorn on port `8000`, starts the persistent
job worker, forwards termination signals, and restarts a worker that exits
unexpectedly. Logs go to Dokploy container logs rather than an unlimited file.

## Pre-deployment checks in the current container

Create and verify a database-only rollback checkpoint using the established
SQLite backup command. Also confirm the existing mounts and free space. The
database checkpoint does not replace offsite upload protection.

## Post-deployment verification

Run in the new container:

```text
cd /app
grep -n "^APPLICATION_VERSION" feature_registry.py
python -m flask --app app:app db current
python scripts/check_data_integrity.py
python -m unittest tests.test_update_30_phase1 -v
```

Then verify database pragmas:

```text
python -c "from app import app; from models import db; from sqlalchemy import text; c=app.app_context(); c.push(); print('foreign_keys',db.session.execute(text('PRAGMA foreign_keys')).scalar()); print('journal_mode',db.session.execute(text('PRAGMA journal_mode')).scalar()); print('busy_timeout',db.session.execute(text('PRAGMA busy_timeout')).scalar())"
```

Expected:

```text
APPLICATION_VERSION = '1.32.0'
20260901_0014 (head)
Foreign-key violations: 0
foreign_keys 1
journal_mode wal
busy_timeout 30000
```

Check web, storage and worker status:

```text
python -c "from urllib.request import urlopen; [(lambda r: print(e,r.status,r.read().decode()))(urlopen('http://127.0.0.1:8000/'+e,timeout=10)) for e in ('healthz','readyz')]"
python -c "from app import app; from models import ServiceHeartbeat; c=app.app_context(); c.push(); print([(r.service_id,r.status,r.last_seen) for r in ServiceHeartbeat.query.filter_by(service_type='worker').order_by(ServiceHeartbeat.id.desc()).limit(3)])"
```

The newest worker row must show `Running` and a recent `last_seen` time.

## Offsite backup activation

Offsite backup requires a separate S3-compatible bucket and credentials. Leave
`BACKUP_OFFSITE_ENABLED=false` until those values are available. Enabling it
without a valid bucket will not damage local data, but scheduled jobs will fail.

After configuration, restart the application, open **Settings → Uploads &
Storage → Backup & recovery**, and create the first offsite checkpoint. Do not
consider uploads protected until the latest job says `Completed` and `restore
verified`.
