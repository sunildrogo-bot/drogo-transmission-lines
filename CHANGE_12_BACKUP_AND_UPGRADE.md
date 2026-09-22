# Change 12 — Data Safety, Backup & Upgrade Manager

The existing DROGO colours, page structure, roles and inspection workflow are
unchanged. Admin now has a Backup & Upgrade section under Settings → Uploads &
Storage.

## Included

- Database/upload consistency health check.
- Missing and untracked file counts.
- Record counts, migration revision and free-disk-space visibility.
- Background manual and pre-upgrade backups.
- Consistent SQLite snapshot using the SQLite backup API.
- Original uploads and non-secret taxonomy configuration.
- Manifest, database SHA-256 and ZIP CRC integrity verification.
- Persistent backup history with progress, download and delete.
- ZIP64 and stored compression for multi-gigabyte image collections.

The `.env` file is intentionally excluded because it contains credentials.
External databases such as SQL Server require their database-native backup tool;
the application refuses to label an uploads-only package as a complete backup.

No database migration is required for Change 12.
