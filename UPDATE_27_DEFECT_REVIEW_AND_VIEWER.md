# Update 27 — Defect review and image viewer controls

- Added `Grading Ring` as a built-in defect type. It remains available even
  when an Admin-managed inspection taxonomy is active.
- Added a defect-type search field that narrows the available list without
  changing or deleting configured taxonomy records.
- Added an Edit action to every active defect chip for authorized inspection
  users. Component, location, defect type, condition status, severity and
  comments can be corrected through the existing versioned audit workflow.
- Expanded the common condition-status vocabulary with operational choices
  including `Partially Missing`, `Rusted`, `Disconnected`, `Overheated`,
  `Needs Repair`, `Needs Replacement`, `Serviceable` and `Unable to Verify`.
- Excluded soft-deleted annotations consistently from photo badges, tower
  summaries, Settings counts and AI/search results. A deleted marking is kept
  in audit history but is no longer presented as an active marked defect.
- Scoped mouse-wheel zoom to the photo only, added bounded pointer/middle-mouse
  panning, preserved normal scrolling in panels, and suppressed native image
  drag/text selection on double-click.

No database migration is required. Existing defect text values and annotation
history remain compatible.
