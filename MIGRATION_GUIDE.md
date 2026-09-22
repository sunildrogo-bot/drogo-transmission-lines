# Upgrade instructions

This release replaces schema mutation during Flask startup with explicit,
versioned Flask-Migrate/Alembic revisions.

## Existing local PostgreSQL database

1. Stop the Flask application.
2. Back up the database.
3. Keep `DATABASE_URL` pointed at that database.
4. From this application's root directory, run:

   ```bash
   flask --app app db current
   flask --app app db upgrade
   flask --app app db current
   ```

5. Start the application normally.

The first revision adopts existing tables without dropping them. The second
revision adds and backfills the session, ticket, and defect-integrity fields.
The third revision adds authentication throttling and changes the informational
project-creator foreign key to `ON DELETE SET NULL`. Existing browser sessions
do not carry the new database session version, so users will sign in once after
this upgrade.

On Windows PowerShell, use the Python module form if the `flask` command is not
available in PATH:

```powershell
python -m flask --app app:app db upgrade
```

## Fresh database

Run `flask --app app db upgrade` first, then optionally run `python seed_db.py`
to load the local demo data.

Do not run `migrate_add_defect_columns.py` after using this migration history.
