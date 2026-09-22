# Client direct defect closure with history

An authorized Client can close or reopen a released defect directly. No Admin
or SME approval is required.

- Closing requires a comment describing the rectification.
- A JPEG, PNG or WebP rectification image can be attached optionally.
- Reopening requires a reason.
- Every action creates an immutable history entry with old/new status, comment,
  user, role, time and evidence link.
- Evidence is protected by the same project, line and Inspection Done access
  rules as the original tower image.
- Evidence files are cleaned up when their defect, tower photo, line, division
  or project is deleted.

This update requires migration `20260825_0005`. It does not change `.env`.
