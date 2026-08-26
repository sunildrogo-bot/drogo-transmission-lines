# Database migrations

The application never creates or changes database tables during web-server
startup. Apply revisions explicitly before starting a new release:

```bash
flask --app app db current
flask --app app db upgrade
```

Revision `20260825_0001` is an adoption baseline. It creates the complete
schema for a fresh database and leaves tables from an existing installation
in place. Revision `20260825_0002` adds session revocation, immutable help-
ticket ownership, and correct defect workflow defaults/backfills.

Always back up a production database before upgrading. The deprecated
`migrate_add_defect_columns.py` script must not be run alongside this history.
