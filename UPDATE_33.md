# Update 33 — Completed tower storage management

Version: 1.33.0. Cumulative from Update 32.

## Tower storage browser

Settings → Uploads & Storage → Project → Browse → Division → Line now shows every tower with stored inspection data or status, including:

- tower number and Inspection Done status;
- RGB and thermal image counts;
- RGB defect and thermal-measurement counts;
- total, raw and preserved-finding storage sizes;
- estimated recoverable storage.

## Delete Raw Images

This password-confirmed action is enabled only for Inspection Done towers. Before deleting a raw file, the server verifies a separate full-quality evidence copy for every RGB defect image and every measured thermal image. Thermal files are copied byte-for-byte so DJI radiometric information remains available. Unmarked RGB/thermal originals and their unnecessary thumbnails are removed. Database markings, temperatures, audit history, reports, AI summaries and the tower's Inspection Done status remain intact.

If evidence cannot be verified, that source raw image is kept and the response reports a warning. The operation is repeatable and already-removed files are handled safely.

## Delete Findings & Markings

This separate password-confirmed action permanently removes preserved RGB/thermal evidence, RGB defect markings, thermal measurements, rectification evidence, generated tower reports and the tower AI summary. It does not silently run as part of raw cleanup.

## Verification

Update 33 adds workflow tests that prove unfinished towers are blocked, marked RGB and measured thermal evidence remain viewable after raw cleanup, clean raw media is removed, and the separate findings purge invalidates generated outputs.
