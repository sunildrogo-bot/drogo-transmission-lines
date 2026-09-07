# Updates 8–14: production foundation

These changes preserve the existing interface, colours, roles and inspection workflow.

## 8. PostgreSQL-aware backups

Application backups now use SQLite's online backup API locally and `pg_dump` custom format on PostgreSQL. Install the PostgreSQL client tools in the worker container/server. A backup still includes tracked uploaded files and excludes `.env` secrets.

## 9. Unified image storage

`STORAGE_BACKEND=local` preserves the existing laptop behaviour. `STORAGE_BACKEND=s3` supports S3-compatible object storage through private object keys and signed download URLs. Uploaded application media is addressed through `/uploads/...`, where the existing access checks still run before delivery.

## 10. Legacy media metadata repair

Settings can queue a persistent background job that reads older images, records dimensions/capture date, classifies RGB versus thermal using the existing naming convention, and records warnings without changing annotations.

## 11. Database indexes and photo pagination

Migration `20260831_0013` adds indexes for the largest inspection queries. The tower-photo API accepts `per_page` and `after_id` and returns `has_more` and `next_after_id`, while remaining backward-compatible when those parameters are omitted.

## 12. Optional offline maps

The existing OpenStreetMap mode remains the default. To serve a region from the VPS without internet tile requests, place a raster PMTiles archive at `static/maps/region.pmtiles` and set:

```text
MAP_TILE_MODE=pmtiles
PMTILES_URL=/static/maps/region.pmtiles
```

PMTiles archives are runtime deployment data and are intentionally excluded from release ZIPs.

## 13. Shared service health

The worker writes its heartbeat to the shared database. Admin health checks report database, storage, worker and background-job state. `/healthz` is a lightweight liveness endpoint and `/readyz` checks database and storage readiness for the reverse proxy/orchestrator.

## 14. Production HTTP and session security

The application now sets HttpOnly/SameSite session cookies, configurable secure cookies and session lifetime, trusted-proxy handling, security headers, and HSTS on HTTPS. For a normal Nginx-to-Gunicorn deployment, configure the exact proxy count and HTTPS cookie mode in `.env`:

```text
SESSION_COOKIE_SECURE=true
SESSION_LIFETIME_MINUTES=480
TRUST_PROXY_COUNT=1
ENABLE_HSTS=true
```

Do not enable secure cookies on the local `http://127.0.0.1:5000` test address.

## Required upgrade commands

After safely preserving `.env`, `.venv`, `instance` and `static/uploads`:

```powershell
python -m flask --app app:app db upgrade
```

Run the web application and persistent worker together. On Windows, `start_windows.ps1` does this. On the VPS, configure separate Gunicorn web and `python job_worker.py` services.
