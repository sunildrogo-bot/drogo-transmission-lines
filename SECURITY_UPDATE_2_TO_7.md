# Security Update: Critical Items 2–7

This build includes the requested fixes for the six critical findings below.

## 2. Project and module access

- Client Users can access only projects explicitly checked in User Management.
- No checked projects now means no project access.
- A Client must have both the project and its module assigned.
- Pilot and SME visibility is derived only from their line assignments.
- Page, API, assistant, and uploaded-file checks use the same centralized rules.

## 3. Pilot assignment enforcement

- Zone, tower submission, and live-capture writes verify the Pilot's exact line assignment.
- Tower labels and reference coordinates are resolved from the server-side KML/KMZ.
- Browser-supplied tower coordinates are no longer trusted for Pilot distance validation.

## 4. Client inspection release

- Unreleased tower photos, defects, thermal points, summaries, reports, exports, assistant results, and direct uploaded-image paths are hidden from Client Users.
- A tower becomes Client-visible only after `inspection_done` is true.

## 5. Private uploaded files

- `/static/uploads/...` and `/uploads/...` now require authentication and resource authorization.
- Project logos, KML, tower/corridor photos, reports, profile images, and announcements use purpose-specific checks.
- Uploaded responses use private browser caching instead of public immutable caching.

## 6. CSRF protection

- Every POST, PUT, PATCH, and DELETE request requires a per-session CSRF token.
- Existing Jinja fetch/XHR calls receive the token automatically.
- React requests fetch and attach the token automatically.
- Login, forgot-password, reset-password, logout, and role-switch forms are protected.
- Logout and role switching now use POST rather than GET.

## 7. Tower deletion authorization

- Complete tower deletion now requires an active Admin session in addition to the configured delete password.

## Verification

- Python syntax and AST parsing pass.
- JavaScript syntax for the updated React API client passes.
- Dependency-free security regression checks are in `tests/test_security_regressions.py`.
