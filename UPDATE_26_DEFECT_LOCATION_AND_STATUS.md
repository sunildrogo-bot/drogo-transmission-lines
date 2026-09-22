# Update 26 — Expanded defect location and condition status

- Added `Left` and `Right` to the existing `Top`, `Middle` and `Bottom`
  annotation locations.
- Added a complete shared component-condition list for every defect while
  retaining defect-specific choices such as `Good/Fair/Poor`,
  `Within Limit/Not Within Limit` and `Required/Done`.
- Added server-side validation for both the expanded location values and every
  supported condition status.
- Existing annotations, reports, filters and exports remain compatible because
  both database columns already store text. No database migration is required.
