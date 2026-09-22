# Updates 1–5 deployment notes

These changes are additive. They do not delete or rename existing projects,
lines, photos, annotations, users, uploads, or inspection records.

## Before deployment

1. Back up PostgreSQL with the database-server backup procedure.
2. Confirm the existing object-storage or mounted-upload backup is current.
3. Deploy the application code while preserving the production `.env`.
4. Run the database migration:

   ```bash
   flask db upgrade
   ```

The migration adds upload metadata, a cached GeoJSON path, and the persistent
background-jobs table. Existing photo rows remain valid and are shown with the
`Legacy` validation status until they are processed again.

## Persistent worker

Run one worker separately from Gunicorn:

```bash
python job_worker.py --poll-seconds 2
```

For production, supervise this command with the same process manager used for
Gunicorn (for example, systemd). The worker must use the same application
directory, Python environment, `DATABASE_URL`, and storage mount/configuration
as the web application. Do not run it from the Flask development server.

Queued work and progress are stored in PostgreSQL. A replacement worker can
recover an interrupted job after its heartbeat becomes stale. Multiple workers
use PostgreSQL row locking to avoid claiming the same queued job.

## Post-deployment smoke checks

```bash
flask db current
python job_worker.py --once
```

Then check these in the browser:

1. Upload one valid RGB image and confirm its preview summary.
2. Try one damaged/non-image file and confirm it is rejected before storage.
3. Open Settings, then Uploads & Storage, and run Thumbnail Repair on a small
   test selection if missing thumbnails exist.
4. Open a tower with many images, switch towers quickly, and open adjacent
   images in the lightbox.
5. Open the same line map as Admin and Pilot and confirm both load tower points.

## Rollback

Rolling back application code does not require removing the additive columns.
Prefer leaving migration `20260829_0012` applied. Only run an Alembic downgrade
after a verified PostgreSQL backup and an explicit rollback decision.
