# DROGO Trans+Pilot — Full Project Documentation

**Project:** Transmission Line + TRANS modules, with Pilot role (drone inspection field app)
**Stack:** Python (Flask) backend, server-rendered Jinja2 templates + vanilla JavaScript for the main app, plus a partial React SPA migration in progress
**Database:** MSSQL (SQL Server) by default in this build, PostgreSQL supported as a drop-in alternative (see `.env`)

This document catalogs every file in the project: what it contains, what it's for, and what language it's written in. Files confirmed to be unused leftovers are marked clearly rather than omitted, so nothing here is guessed — every entry was checked against the actual code.

---

## 1. Languages Used, at a Glance

| Language | Where | What it's doing |
|---|---|---|
| **Python** | All `.py` files (18 files, ~9,600 lines) | Flask web server, routes, database models (SQLAlchemy ORM), PDF report generation (ReportLab), business logic, auth, migrations |
| **HTML + Jinja2** | All `.html` files in `templates/` (17 files) | Page structure and server-side templating — Jinja2 is Flask's templating language, embedded directly in the HTML (`{{ }}` for variables, `{% %}` for logic) |
| **JavaScript** | `.js` files in `static/js/`, plus inline `<script>` blocks inside most templates | Client-side interactivity: map rendering (Leaflet), the 3D chimney viewer (CesiumJS — mostly unused leftover in this project, see §6), form handling, live camera capture, AJAX calls to the Flask API |
| **CSS** | Inline `<style>` blocks inside templates (this project has no separate `.css` files outside the React frontend) | Page styling, using CSS custom properties (`--accent`, `--bg-elevated`, etc.) defined once in `base.html` and reused everywhere |
| **SQL** | 2 `.sql` files | Ad-hoc one-off database scripts (not part of the running app — see §6) |
| **JSX (React)** | `.jsx`/`.js` files in `frontend/src/` | A **separate, partial** React single-page app — login + dashboard only, meant to eventually replace the Jinja templates incrementally (see §5) |
| **JSON** | `.json` files | npm package manifests (`frontend/package.json`, `package-lock.json`), plus one static data file |

**Why this mix:** the core app is a traditional server-rendered Flask app (Python + Jinja2 + vanilla JS) — the most direct path for a small team to ship features fast without a separate frontend build pipeline. The React frontend in `frontend/` is a newer, incremental migration layer that currently only covers login and the dashboard; every other page still redirects to the existing Flask-rendered pages until they're migrated too (see the comment in `frontend/src/moduleRoutes.js`).

---

## 2. Directory Structure

```
drogo_trans_pilot/
├── app.py                          # Flask app factory — creates and configures the app
├── models.py                       # All database tables (SQLAlchemy ORM)
├── projects_routes.py              # Project/Division/Line/Tower CRUD + KML/photo/defect/report API
├── assistant_api.py                # AI chat assistant (Gemini-powered)
├── users.py / users_routes.py      # User accounts — CRUD logic and HTTP routes (split apart)
├── settings.py / settings_routes.py# App settings + admin dashboard data
├── auth_api.py                     # JSON auth endpoints for the React frontend
├── announcement_routes.py          # Admin announcements (bell icon popup)
├── help_routes.py                  # Support ticket system
├── load_monitor.py                 # In-memory server load tracking (admin dashboard chart)
├── mailer.py                       # Gmail SMTP — welcome emails
├── migrations/                     # Versioned Flask-Migrate/Alembic database revisions
├── migrate_add_defect_columns.py   # Deprecated legacy compatibility utility
├── seed_db.py                      # One-time demo data seeding script
├── tower_report.py                 # Per-tower PDF report builder
├── trans_report.py                 # Whole-project PDF report builder (TRANS module)
├── central_report.py               # Cross-project combined PDF report builder
│
├── templates/                      # Jinja2 HTML templates (17 files)
│   ├── base.html                   # Shared layout: navbar, CSS variables, AI chat widget
│   ├── login.html                  # Login page (Client/Admin/Pilot role toggle)
│   ├── home.html                   # Public landing page
│   ├── admin.html                  # Admin dashboard
│   ├── client_dashboard.html       # Client's dashboard
│   ├── pilot_dashboard.html        # Pilot's "assigned work" list
│   ├── pilot_line_work.html        # Pilot's map + camera capture page
│   ├── project_map.html            # The main KML/tower/defect map (Admin + Client)
│   ├── project_info.html           # Simple project detail page
│   ├── projects.html               # Project listing page (Transmission Line / TRANS)
│   ├── users.html                  # User Management page
│   ├── settings.html               # Settings page (delete password, activity log, etc.)
│   ├── _add_project_modal.html     # Reusable "add project" modal partial
│   ├── _add_trans_project_modal.html # ⚠️ orphaned, see §6
│   ├── land_survey_sites.html      # ⚠️ orphaned, see §6
│   ├── project_overview.html       # ⚠️ orphaned, see §6
│   └── USE drogo_aerospace;.sql    # ⚠️ misplaced file, see §6
│
├── static/
│   ├── js/                         # Vanilla JS (8 files)
│   ├── images/                     # Logos, cover photos
│   ├── data/towers.json            # ⚠️ orphaned, see §6
│   └── uploads/                    # User-uploaded files (photos, logos, KML, tilesets)
│
├── frontend/                       # Partial React SPA (separate migration effort, see §5)
│
├── requirements.txt                # Python package dependencies
├── .env                            # Environment variables (database URL, mail, API keys)
└── README.md
```

---

## 3. Backend — Python Files

| File | Purpose | Lines |
|---|---|---|
| **`app.py`** | The Flask **application factory**. Creates the Flask app, configures the database connection, registers every blueprint (the other route files), defines the core page routes (`/login`, `/logout`, `/admin`, `/dashboard`, `/pilot`, `/pilot/lines/<id>`, `/projects`, `/trans`), and validates authenticated sessions against current database state. | 290 |
| **`models.py`** | Every database table, defined as a SQLAlchemy class: `User`, `Role`, `Module`, `Project`, `Division`, `Line`, `TowerPhoto`, `TowerDefect`, `TowerReport`, `TowerInspectionStatus` (inspection-done flag + pilot's zone classification), `PilotAssignment`, `Announcement`, `HelpTicket`, `ActivityLog`, `AppSetting`. Each class has a `to_dict()` method used to serialize it to JSON for the API. | 590 |
| **`projects_routes.py`** | The biggest file — the core API for the Transmission Line / TRANS modules. Covers: Project/Division/Line CRUD, KML upload, tower photo upload (both admin's regular upload and the pilot's live-camera capture path), tower defect marking, the "Inspection Done" and "Zone" per-tower status system, pilot assignment, and PDF report generation triggers. | 1,289 |
| **`assistant_api.py`** | The AI chat widget's backend. A tool-calling agent using Google's Gemini API — lets a user ask natural-language questions ("how many critical defects on Line 2?") and the AI calls real database-query functions to answer, respecting the same permission rules as the rest of the app. | 446 |
| **`users.py`** | User account business logic — create/update/authenticate, password hashing, role/module assignment, project-wise access restrictions. Pure logic, no Flask routes. | ~340 |
| **`users_routes.py`** | The `/users/*` HTTP routes (User Management page) — thin wrapper around `users.py`'s logic, handles file uploads (profile photos) and JSON request/response. | ~260 |
| **`settings_routes.py`** | Powers the Settings page and admin dashboard: shared delete-password, activity log, dashboard summary statistics (project/user counts, defect severity breakdown, 7-day activity chart), live server-load monitoring endpoint, and the admin's personal notes widget. | ~340 |
| **`auth_api.py`** | A parallel, JSON-only version of the login/logout endpoints, built specifically for the React frontend (which can't use Flask's server-rendered redirect-based login flow). Doesn't replace the existing `/login` route — both exist side by side. | ~110 |
| **`announcement_routes.py`** | Admin-authored announcements shown as a bell-icon popup to every logged-in user. One-way (Admin → everyone), unlike the two-way help ticket system. | ~90 |
| **`help_routes.py`** | Support ticket system — any user can raise a ticket, Admin moves it through Open → Checking → Resolved, and the original reporter gets notified of status changes. | ~150 |
| **`load_monitor.py`** | Tracks request timing and active-user counts **in memory**, purely for the admin dashboard's live load chart. Explicitly documented as single-process-only — won't be accurate if the app ever runs with multiple worker processes. | ~120 |
| **`mailer.py`** | Sends the "welcome, here's your password" email to newly created users via Gmail SMTP, using an App Password (not the account's real password). | ~60 |
| **`migrations/`** | Ordered Flask-Migrate/Alembic revisions. Run `flask --app app db upgrade` before starting a new release; the app does not mutate the schema during startup. | — |
| **`migrate_add_defect_columns.py`** | Deprecated compatibility utility for pre-Alembic installations. It is retained for reference but is no longer imported at startup and must not be mixed with the versioned history. | ~400 |
| **`seed_db.py`** | An optional demo-data script (`python seed_db.py`) to run after `flask --app app db upgrade` on a fresh database. | ~180 |
| **`settings.py`** | Small helper module — just the shared delete-password get/set/verify logic, factored out of `settings_routes.py`. | ~30 |
| **`tower_report.py`** | Builds the **per-tower PDF report** ("RGB Visual Inspection Report") — one tower's defects, 2 per page, full-page format, corporate navy/gold styling. | ~330 |
| **`trans_report.py`** | Builds the **whole-project PDF report** for Transmission Line/TRANS — every tower's defects in one document, grouped by division and line. | ~250 |
| **`central_report.py`** | Builds a **combined PDF across every project** the requesting user can see, for a single high-level report spanning the whole portfolio. | ~140 |

---

## 4. Frontend — Templates, JavaScript, and Styling

### 4a. Templates (`templates/*.html`) — HTML + Jinja2 + inline JS/CSS

Every template extends `base.html` (Jinja2's `{% extends %}`), which provides the shared navbar, CSS custom properties, and the floating AI chat widget. Most page-specific logic lives in a `<script>` block at the bottom of each template rather than separate `.js` files — this was a deliberate choice through most of this project's development to keep each page self-contained.

| Template | Purpose | Audience |
|---|---|---|
| `base.html` | Shared layout every other template builds on: navbar, CSS variables (`--accent`, `--bg-elevated`, etc.), the announcement bell, the floating AI assistant chat widget (hideable per-page via a `hide_ai_chat` flag). | Everyone |
| `login.html` | Login form with a Client/Admin/Pilot role toggle (three-way animated switch). | Everyone |
| `home.html` | Public landing page before login. | Public |
| `admin.html` | Admin's dashboard — stats cards, module management grid, recent activity feed, calendar+notes, live server load chart. | Admin |
| `client_dashboard.html` | Client's dashboard — their own stats (total projects, open defects, users), calendar+notes, activity trend, help ticket list. | Client |
| `pilot_dashboard.html` | Pilot's landing page — list of lines assigned to them by Admin, with a "new assignment" popup notification. | Pilot |
| `pilot_line_work.html` | The pilot's actual field-work page: a live Leaflet map of the assigned line's KML (line path + tower markers), a slide-out tower list, inline Red/Yellow/Green zone selection per tower, and the GPS-gated live camera capture flow. | Pilot |
| `project_map.html` | The core working page for Admin and Client — the full interactive KML map: tower markers, photo upload, defect marking (point and shape-based), tower inspection status, pilot assignment, PDF report generation, bulk photo folder upload. The single largest template (~2,000 lines). | Admin, Client |
| `project_info.html` | A simple, low-detail project metadata page. | Admin, Client |
| `projects.html` | The project listing/grid page for Transmission Line and TRANS modules — where "+ Add Project" lives. | Admin, Client |
| `users.html` | User Management — create/edit/delete accounts, assign roles and modules, project-wise access restriction. | Admin |
| `settings.html` | Delete password management, activity log viewer, project management table, help ticket inbox. | Admin |
| `_add_project_modal.html` | A reusable Jinja `{% include %}` partial — the "add project" form modal, shared between `projects.html` and other listing pages. | Admin |

### 4b. JavaScript (`static/js/*.js`)

| File | Purpose |
|---|---|
| `projects.js` | Shared "Add Project" form logic and the dynamic project-grid rendering — used by `projects.html`. |
| `settings.js` | Settings page logic — tabs, activity log rendering, all-projects table, delete-password form. |
| `chimney.js`, `chimney_viewer.js` | ⚠️ **Orphaned — see §6.** Not part of this project (Chimney module was split out into a separate project). |

### 4c. CSS

There's no standalone `.css` file for the main app — every template has its own `<style>` block at the top, using a shared set of CSS custom properties (colors, spacing, radii) defined once in `base.html` and referenced everywhere else (`var(--accent)`, `var(--bg-elevated)`, etc.). This keeps each page visually consistent without a build step. The one exception is the React frontend, which has its own `frontend/src/styles/tokens.css`.

---

## 5. The React Frontend (`frontend/`)

A **separate, partial** single-page app, built with Vite + React, living entirely in its own folder. It is **not** a full replacement for the Jinja templates — by design, it currently only implements login and the dashboard shell. Every other page (Projects, Users, Settings, the map, etc.) is reached via a full-page link back out to the existing Flask-rendered routes (see `frontend/src/moduleRoutes.js` for the explanation of this bridge strategy).

| File | Purpose |
|---|---|
| `src/main.jsx` | React app entry point. |
| `src/App.jsx` | Top-level component — routing setup. |
| `src/auth/AuthContext.jsx`, `RequireAuth.jsx` | Session/auth state management for the SPA. |
| `src/api/auth.js`, `client.js` | Fetch wrappers for calling `auth_api.py`'s JSON endpoints. |
| `src/pages/Login.jsx`, `Dashboard.jsx`, `LegacyRedirect.jsx` | The only three pages actually built in this SPA so far. |
| `src/moduleRoutes.js` | Maps each module to either an in-SPA route or a redirect back to the Flask backend. |
| `src/styles/tokens.css` | This frontend's own design tokens (separate from the Jinja templates' inline CSS variables). |
| `package.json`, `vite.config.js` | Standard Vite/React project config. |

**Why it exists:** the project's architecture decision (documented in `frontend/README.md`) was explicitly *not* to split the backend into microservices — the Flask backend stays one service, and this frontend is meant to replace the Jinja templates gradually, page by page, rather than requiring a big-bang rewrite before anything can ship.

---

## 6. Files Confirmed Unused (Left Over from the Chimney/Land Survey Split)

Earlier in this project's history, it was split from a combined codebase into two separate projects — this one (Transmission Line + TRANS + Pilot) and a separate Chimney + Land Survey project. The split correctly removed the *routes* and most templates for the Chimney/Land Survey modules, but a handful of static files and a couple of templates were never deleted. I checked each of these directly against the current code (grepped for any reference anywhere in `.py`/`.html` files) and confirmed **zero** references — they're inert, taking up space but not affecting anything:

| File | Why it's orphaned |
|---|---|
| `templates/land_survey_sites.html` | Land Survey module isn't part of this project. |
| `templates/project_overview.html` | Its backing route (`/projects/<id>/overview`) was removed during the split. |
| `templates/_add_trans_project_modal.html` | Nothing includes it anymore. |
| `static/js/chimney.js`, `static/js/chimney_viewer.js` | The entire Chimney (3D Inspection) module lives in the other project now. `chimney_viewer.js` alone is ~3,000 lines. |
| `static/images/landsurvey_Cover.png`, `chimney_cover_default.png` | Cover images for modules that don't exist here. |
| `static/uploads/chimney_covers/`, `chimney_defect_images/`, `chimney_rectified_images/`, `chimney_tilesets/` | Uploaded chimney data from before the split. |
| `static/data/towers.json` | Backed the `/api/towers` route, which was removed along with Land Survey. |
| `USE drogo_aerospace;.sql` (root and a duplicate copy inside `templates/`) | A one-off manual SQL cleanup script (`DELETE FROM chimney_projects WHERE id = ...`) — not part of the application, and references a table (`chimney_projects`) that doesn't even exist in this project's schema. Looks like a scratch file that got committed by accident. |

None of these cause errors — they're just dead weight. Worth a cleanup pass if you want a leaner checkout, but not urgent.

---

## 7. Configuration & Dependencies

| File | Purpose |
|---|---|
| `.env` | Environment variables: `DATABASE_URL` (currently MSSQL, PostgreSQL alternative commented below it), Gmail credentials for `mailer.py`, `GEMINI_API_KEY` for the AI assistant. |
| `requirements.txt` | Python dependencies: `flask`, `flask-sqlalchemy`, `flask-migrate`, `werkzeug`, `python-dotenv`, `reportlab` (PDF generation), `Pillow` (image processing), `google-genai` (AI assistant), `pyodbc` (MSSQL driver), `psycopg2-binary` (PostgreSQL driver, ready if you switch). |
| `.gitignore` | Standard exclusions (two copies — one at root, one inside `frontend/`). |

---

## 8. Database Roles & Access Model, Briefly

Three roles: **Admin** (full read/write), **Client User** (read-only, optionally restricted to specific projects), **Pilot** (field capture only — assigned specific lines by Admin, no module-level access otherwise). Every API endpoint that writes data checks the session role server-side (not just hiding buttons in the UI), and Client visibility of tower photos/defects is gated on Admin explicitly marking that tower's "Inspection Done" — not just whether defects happen to exist.
