# Update 31 — Image Review and Map Reliability

Version: **1.31.0**. Cumulative release based on Update 30 Phase 1, v1.30.1.

## Changes

1. **Correct image details during navigation.** Defect shapes, chips, thermal readings and measurement defaults clear when the photo changes. Delayed responses from another photo, a closed viewer or an older refresh are ignored. A measurement/save finishing after navigation cannot put its result on the new photo. Tower responses are also scoped to the selected tower. Gallery refreshes preserve the selected photo by identity.
2. **KML edits refresh the map.** Saving writes a new storage key and rebuilds GeoJSON with a new cache key. Browser requests bypass cached geometry. Active map layers reload after saving; an open details panel for that line closes so the next marker selection supplies the updated coordinates. KMZ edits retain the archive's other assets. Earlier source files are retained; this update does not delete map history.
3. **New images require a fresh SME release.** A successful tower-photo upload resets that tower to Inspection not completed in the same database transaction. Client access to the tower is blocked until SME/Admin marks Inspection Done again. Existing findings, closure history and files remain. Failed/rejected uploads do not reopen a tower. Previously shared AI summaries become unshared and must be reviewed/shared again. Regenerate existing PDF reports after reviewing the new evidence; saved PDFs remain dated snapshots.
4. **Preserved originals remain usable.** Project defect summaries, Client defect/thermal galleries, report image hydration and thermal image access use the surviving full-quality image path after raw cleanup. The existing image endpoint retains its thumbnail fallback for viewing; report source images remain full-quality originals. This does not recover files already missing from both raw and preserved storage.

Existing colors, PDF layout and role workflow remain. SME can release directly to Client; Client can close/reopen defects. No per-image clean/no-defect marking is required.

## Upgrade an existing installation

This ZIP contains application source, not your live database, uploads or credentials. Update 31 adds no database migration; the migration head remains `20260901_0014`.

1. Make your normal application/database backup. Stop the web process and background worker before replacing source.
2. On Windows, from the existing application folder, run the included upgrade script with the downloaded ZIP's actual path:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\scripts\upgrade_windows.ps1 -ReleaseZip "C:\Downloads\drogo_trans_pilot_cumulative_updates_1_to_31_v1.31.0.zip" -ApplicationPath (Get-Location).Path
   ```

   The existing upgrade script verifies the ZIP, creates a source rollback package, backs up local SQLite and preserves `.env`, `.venv`, `instance` and `static/uploads`. For PostgreSQL, use your server backup procedure first.

3. For Linux/container deployment, deploy this source through your existing process while retaining environment settings and mounted runtime data. Use the existing dependency installation and `python -m flask --app app db upgrade` steps. See `DEPLOYMENT_DOKPLOY.md` for that deployment's instructions.
4. Restart both web and worker processes. Reload open browser tabs to load the new viewer code. Settings should show **1.31.0**.
5. Confirm that a moved map marker appears at its saved location. On a test tower, mark Inspection Done, add a new image, and confirm it returns to pending review. Review the images, regenerate its report if needed, and mark Inspection Done again.

## Verification

The release was checked with the existing regression suite plus new tests covering delayed RGB/thermal response order, closed-viewer responses, upload release reset, failed upload preservation, repeated KML/KMZ edits, preserved-original summary/gallery access and report generation. JavaScript syntax and the source release manifest are checked separately. These are local isolated checks; this package has not been deployed to your running server.

Run the suite from the application directory with dependencies installed:

```text
python -m unittest discover -s tests -v
```

Node.js is used only by the viewer response-order regression test; it is not an application runtime requirement.
