# Updates 15–17

## 15 — Release compatibility protection

- A required-feature registry verifies the complete cumulative source before a release ZIP can be built.
- The release build is blocked if a protected page, route or workflow marker is missing.
- Settings → Uploads & Storage displays application version, database revision, feature count and worker state.
- This phase introduced version `1.17.0`; the cumulative version may be newer.

## 16 — Complete object-storage operation

- KML/KMZ parsing and editing, GeoJSON map cache generation, thermal SDK decoding, training exports and tower reports hydrate private objects into a protected local working cache when native libraries require a filesystem path.
- Generated GeoJSON caches, edited KML, central reports, tower reports, thumbnails and new uploads are published through the configured storage backend.
- URL generation for client defect thumbnails now uses the private storage route.
- Filename collision checks include objects already present remotely.
- Settings includes a non-destructive tracked-file migration job. It copies missing database-linked local uploads to object storage and never deletes local files.

## 17 — Reliable worker and shared monitoring

- Request metrics are buffered and written to per-process database minute buckets at most once every ten seconds.
- Dashboard load figures combine all Gunicorn web workers instead of reporting one process.
- Worker heartbeats are stored per process and surfaced through the compatibility status.
- SQLite permits one background worker through a stale-safe local lease; PostgreSQL continues to support concurrent workers with row locking.
- Job progress, retries and interruption recovery remain database-backed.

## Upgrade requirements

1. Preserve `.env`, `.venv`, `instance`, and existing uploads.
2. Install requirements.
3. Run `python -m flask --app app:app db upgrade`.
4. Confirm revision `20260901_0014`.
5. Start Gunicorn/Flask and the separate `python job_worker.py` service.
6. Open Settings → Uploads & Storage and confirm all protected features pass.
7. When object storage is enabled, run **Copy Missing Files** once and wait for completion.
