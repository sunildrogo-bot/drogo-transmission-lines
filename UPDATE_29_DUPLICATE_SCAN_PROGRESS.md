# Update 29 — Visible duplicate-scan progress

- Replaced the zero-only duplicate scan state with a live Admin progress panel.
- Shows the running operation and current phase: preparing inventory,
  fingerprinting originals, analysing groups, removing records, or clearing
  unreferenced stored files.
- Shows percentage, completed images, total images, remaining images, elapsed
  time, processing speed, estimated remaining time, and worker activity.
- Clearly distinguishes a queued task waiting for the background worker from a
  task that is actively processing.
- Warns when a processing worker has stopped reporting progress for more than
  90 seconds.
- Publishes scan progress every five photos while retaining the conservative
  exact-byte duplicate rules introduced in Update 28.

No database migration is required. Existing jobs, images and hashes remain
compatible.
