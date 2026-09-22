# v1.32.0 deployment and rollback checklist

Release branch: `release/v1.32.0-client-workspace`.

This is the cumulative Update 32 release. It has not been deployed merely by pushing this branch. Do not merge into an automatically deployed branch until the live backup gate below is complete.

## Identify the existing application

Confirm the transmission application's Dokploy application ID, deployed Git branch and commit, container image digest, domain, port 8000, and actual host/volume sources mounted at `/app/instance` and `/app/static/uploads`. Keep other VPS applications untouched. Confirm the actual database URL and any external storage paths; SQLite at `/app/instance/nova.db` is only the historical configuration.

## Back up before changing the running release

1. Record the current image digest, deployment configuration, environment and volume mappings in a restricted backup location outside the application volumes. Environment backups contain secrets and must not be committed to Git.
2. Enter maintenance mode and stop writes from web and worker processes for this application. Keep writes paused until a consistent backup of both the database and uploads is complete.
3. Back up the entire persistent instance and uploads directories, including hidden files and any SQLite WAL/SHM files. Back up any configured project-map directory or other external storage too. If the live database is PostgreSQL, use its native consistent database dump instead of assuming SQLite.
4. Preserve ownership and permissions. Generate SHA-256 checksums, copy the backup to a separate off-server destination, and verify checksums at that destination. Check available disk space first.
5. Restore the backup to a temporary directory (never over production), run SQLite `PRAGMA integrity_check` and `PRAGMA foreign_key_check` against the restored database where applicable, and verify uploaded files are present and readable. Test representative project/photo records against those restored files. Archive listings or file-size checks alone are insufficient.
6. Record the verified backup location, timestamp, previous release, restore results and retention policy. Keep the pre-release backup until the new release is accepted and a later tested backup exists.

## Deployment gate

Preserve existing secrets, SMTP settings, volumes and database configuration. Review `DEPLOYMENT_DOKPLOY.md` and `.env.example`; do not overwrite live configuration with example values. The new Dockerfile starts `production_start.py`, which runs migrations and supervises web/worker processes. Production startup requires a strong secret, HTTPS `PUBLIC_BASE_URL`, secure cookies and non-debug configuration. Migration head is `20260901_0014`; Update 32 adds no migration beyond the earlier cumulative updates.

Configure the identified Dokploy application to deploy the reviewed release commit, then deploy. Keep the previous image available. Never delete or recreate data volumes during redeployment.

## Verify before ending maintenance

Check `/healthz` and `/readyz`, running web and worker processes, migration head, logs and database integrity. Test login, the client dashboard chart scrolling, defect details to the left of the image, and independently scrolling tower lists. Check the admin email configuration status. A real onboarding email test needs a controlled recipient and working SMTP; the automated suite uses mocked SMTP and does not prove delivery.

## Rollback

Stop application writes and preserve the failed release's logs and data for diagnosis. If no schema or data changes occurred, redeploy the recorded previous image/configuration using the same volumes. Otherwise restore the verified pre-release database and uploads together with the matching previous image/configuration while the application remains stopped. Do not restore only the database after uploads have changed. Check integrity and representative records before reopening access. Restoring the pre-release backup discards changes after its timestamp, so reconcile any such writes before rollback.

## Current status

The Git release is prepared and its automated tests passed. Live backup, restore testing and VPS deployment require access to the identified Dokploy/VPS application; none is asserted by this document.
