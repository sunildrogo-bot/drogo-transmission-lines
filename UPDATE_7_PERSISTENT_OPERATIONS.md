# Update 7 — persistent operations

The PostgreSQL/SQLite-backed worker introduced with the performance foundation
now runs application backups and training dataset exports as well as thumbnail
repair. The existing Settings and Dataset Export screens, progress indicators,
history records, downloads and delete actions remain unchanged.

The web process only validates and queues work. A separate worker claims it,
updates the existing progress record, records a heartbeat and completes it.
Queued work survives a web/Gunicorn restart. An interrupted worker job is
re-queued up to its configured attempt limit.

## Windows local start

From the permanent application folder, run:

```powershell
.\start_windows.ps1
```

This starts the persistent worker and Flask together using the existing
`.venv`, then stops both when `Ctrl+C` ends the local application.

## VPS

Run this as a separately supervised service using the same release, virtual
environment, `.env`, database and storage configuration as Gunicorn:

```bash
python job_worker.py --poll-seconds 2
```

Do not start the worker inside a Gunicorn web worker. PostgreSQL row locking
allows more than one dedicated job worker when production volume requires it.

Application backups still support automatic database snapshots only for local
SQLite. PostgreSQL must be backed up by the database-server backup procedure;
moving the queue to the persistent worker does not change that safety rule.
