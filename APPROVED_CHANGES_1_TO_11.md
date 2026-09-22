# Approved Changes 1–11

This cumulative build preserves the existing DROGO colours, cards, page styles,
roles and inspection release workflow.

## Included

1. Shared, stable Admin sidebar icons.
2. Settings large-screen compatibility.
3. Safe screen, modal, image and overflow behaviour.
4. Shared navigation/icon foundations.
5. Breadcrumbs on deep project/inspection pages.
6. Project-map position, zoom and tower context preserved during the session.
7. Canonical workflow status helpers without rewriting existing records.
8. Tower sidebar filters, open/critical counts and previous/next navigation.
9. Explicit image review and server-enforced Inspection Done validation.
10. Recoverable annotation deletion, audit snapshots and optimistic edit versions.
11. Admin-managed Component & Defect Taxonomy under Settings → Inspection.

## Required database upgrade

After copying the previous `.env`, `instance` data and `static/uploads` into
this build, run this once before starting the application:

```powershell
python -m flask --app app db upgrade
python app.py
```

Migration `20260826_0010` preserves compatibility with legacy installations.
Migration `20260827_0011` adds annotation audit and taxonomy tables without
deleting or renaming existing inspection records.

## First taxonomy review

Open **Settings → Inspection**. The manager initially groups component and
defect names already observed in the database. Review those names and click
**Save Taxonomy**. Controlled component-specific defect choices then apply to
new annotations; historical observations remain unchanged.
