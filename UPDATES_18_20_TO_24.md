# Updates 18, 20, 21, 22, 23 and 24

Update 19 (tower PDF redesign) is intentionally excluded and the existing report design remains in place.

## 18 — Complete gallery loading

- Tower-photo API uses cursor pagination with 120 records by default and 200 maximum per response.
- The browser follows `next_after_id` until every image is available.
- Only 48 thumbnails are rendered at once, preserving fast tower opening and complete lightbox navigation.

## 20 — Activity Log pagination

- Settings initially loads 20 audit records.
- Server-side filters cover uploads, reviews, defects, users and deletions.
- Search matches affected item, user, module and details.
- Load More retrieves the next page without replacing visible rows.

## 21 — Resilient local assets and maps

- Application fonts use local operating-system fonts; default cover images use bundled application images.
- Leaflet, Leaflet Draw and PMTiles libraries remain locally hosted.
- `MAP_TILE_MODE` supports `osm`, `pmtiles`, custom URL use, and `none`.
- A regional raster PMTiles archive can make the transmission map independent of internet tile availability.
- KML/tower overlays remain visible when a remote basemap is unavailable.

## 22 — Production health

- Admin Settings checks the web server process, trusted proxy configuration, HTTPS origin, database, storage, background worker, persistent jobs and map source.
- Production warnings are explicit and never claim that Nginx or Gunicorn is healthy merely because Flask is reachable.
- Use `DEPLOYMENT_ENV=production` on the VPS to enable production-specific warnings.

## 23 — Data integrity repair centre

- Missing database-linked files are grouped by upload category.
- A complete issue CSV is downloadable.
- Existing missing-thumbnail regeneration remains available.
- A relink is offered only when one missing path has exactly one untracked candidate with the same filename.
- Relinking changes database references only; it does not rename, move or delete files, and every change is audited.

## 24 — Workflow regression

- An isolated temporary-database test covers SME defect marking, Inspection Done release, report generation, Client visibility and closure, and training-dataset preview.
- The test never reads or writes the configured laptop or VPS database.
- Run it with the application virtual environment:

```powershell
python -m unittest tests.test_workflow_end_to_end -v
```

## Deployment configuration

```text
DEPLOYMENT_ENV=production
PUBLIC_BASE_URL=https://your-domain.example
TRUST_PROXY_COUNT=1
MAP_TILE_MODE=osm
```

For a self-contained regional basemap:

```text
MAP_TILE_MODE=pmtiles
PMTILES_URL=/static/maps/region.pmtiles
```
