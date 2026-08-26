# Complete-line bulk upload reliability update

- Admin still selects the complete line folder once, regardless of its total
  size.
- Files are transferred individually so the complete multi-gigabyte line is
  never placed into one HTTP request.
- Each file receives an adaptive two-to-fifteen-minute timeout based on size.
- Network, timeout and server failures are retried automatically up to three
  times; validation failures are not retried.
- Admin can cancel the remaining queue. Successfully uploaded files are kept.
- Existing duplicate-file detection makes a repeated folder selection safe:
  completed files are skipped and only missing files are added.
- Corridor images now receive the same content-hash duplicate protection as
  tower images.

This update does not compress originals or change `.env`. Run the database
migration before starting the updated application.
