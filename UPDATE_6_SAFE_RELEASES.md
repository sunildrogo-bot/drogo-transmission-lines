# Update 6 — secret-safe, in-place releases

This update changes release and upgrade handling only. It does not alter the
application interface, roles, inspection workflow, database records, uploads,
or existing configuration.

## Protected locations

Every source release excludes:

- `.env` and environment-specific credentials;
- `.venv` and other machine-specific Python environments;
- `instance`, SQLite databases, backups and generated exports;
- `static/uploads`, reports and inspection imagery;
- caches, logs, temporary files and older ZIP packages.

`.env.example` documents the required keys with empty values.

## Release validation

`scripts/build_release.py` creates a source-only ZIP with a SHA-256 manifest.
It immediately reopens the archive and verifies paths, file sizes, hashes, CRC,
excluded locations and private-key markers. The command fails closed when a
credential or runtime-data risk is detected.

Example for a release maintainer:

```powershell
python scripts\build_release.py `
  --output "C:\safe-output\drogo_trans_pilot_release.zip"
```

Verify an existing package without extracting it:

```powershell
python scripts\build_release.py --verify "C:\safe-output\drogo_trans_pilot_release.zip"
```

## Windows in-place upgrade

Stop the application first. From the permanent application folder, run:

```powershell
.\.venv\Scripts\Activate.ps1
.\scripts\upgrade_windows.ps1 `
  -ReleaseZip "C:\path\to\verified_release.zip" `
  -ApplicationPath "C:\TRANSMISSION APPLICATION\drogo_trans_pilot_inspection_quality_updated_extracted\drogo_trans_pilot"
```

The script verifies the incoming ZIP, saves a source rollback package, copies
the local SQLite database to a timestamped pre-upgrade backup, preserves `.env`,
`.venv`, `instance` and `static\uploads`, installs dependencies, applies the
versioned migration and displays the final database revision.

PostgreSQL must still be backed up using the configured database-server backup
procedure before an application migration.
