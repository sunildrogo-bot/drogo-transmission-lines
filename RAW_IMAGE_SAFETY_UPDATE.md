# Raw-image and thermal-file safety update

- Radiometric thermal originals are never removed by line raw cleanup.
- An RGB original is removed only when a verified full-quality evidence copy
  exists on disk.
- Missing copies and failed deletions leave the database record unchanged.
- Galleries, reports and the assistant resolve the same surviving image path.
- Cleanup responses report deleted, thermal-skipped and unprotected-skipped
  counts separately.

This update requires no database migration and does not alter `.env`.
