# Fixes 9, 10, 11 and 13

## 9 — Disabled users and stale sessions

- Added `users.session_version`.
- Every authenticated application request loads the current user from the
  database and rejects deleted, inactive, role-invalid, or version-stale
  sessions.
- Account edits and password resets increment the version, revoking older
  session cookies immediately.
- The current Admin session is synchronized safely after a self-edit.

Existing sessions will sign in once after this database upgrade because old
cookies do not contain a session version.

## 10 — Help-ticket ownership

- Added `help_tickets.submitted_by_user_id` with a foreign key to `users.id`.
- New tickets record both immutable ownership and the reporter-name snapshot.
- Non-Admin list and update routes are scoped by user ID.
- The PATCH endpoint rejects access to another user's ticket before applying
  any requested mutation.
- Legacy tickets are backfilled only when the old reporter name maps to exactly
  one user. Ambiguous/unmatched tickets remain Admin-only.

## 11 — Open/closed defect calculations

- Dashboard and assistant searches now use `resolution_status` for Open/Closed.
- Assistant results expose `status` as the workflow state and
  `component_status` as OK/Missing.
- The migration backfills missing workflow state to Open and repairs the old
  invalid `status='Open'` value to `status='OK'`.

## 13 — Database migrations

- Removed schema creation and `ALTER TABLE` operations from web-server startup.
- Added versioned Flask-Migrate/Alembic revisions under `migrations/`.
- Updated `seed_db.py` so it seeds data only; migrations create the schema.
- Kept `migrate_add_defect_columns.py` only as a clearly deprecated legacy
  utility and corrected its defect-status backfill.

Before starting this release, back up the database and run:

```bash
flask --app app db upgrade
```

See `MIGRATION_GUIDE.md` for the full local PostgreSQL upgrade sequence.
