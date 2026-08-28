# Inspection Quality and Tower-Level Completion

This update adds an Admin-only **Inspection Quality** workspace and aligns the
inspection flow with the operational decision approved for the application.

## Workflow

1. The SME opens a tower and browses the available RGB and thermal images.
2. A normal image can be skipped without a separate review action.
3. If a visible defect is identified, the SME records the annotation.
4. Thermal measurements are recorded when required.
5. **Inspection Done** is the tower-level completion and Client-release action.

There is no **Mark Reviewed** button and Inspection Done is not blocked by
legacy per-image review fields. The legacy database columns and endpoint remain
available for backward compatibility, but the current UI does not use them.

## Inspection Quality

The Admin page reports:

- Total towers
- Inspection Done / Client-visible towers
- Inspection Not Done towers
- Open and critical defects
- Missing evidence files
- Missing or regenerable thumbnails
- Missing component or defect-type classifications
- RGB and thermal image counts
- Direct links to affected project lines

The quality dashboard itself reads existing inspection records. This bundled
release still requires migration `20260827_0011` for its annotation audit and
admin-managed taxonomy features.
