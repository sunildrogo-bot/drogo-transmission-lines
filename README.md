# NOVA+ Flask Application

Power Transmission Intelligence Platform — rebuilt with Flask.

## Project Structure

```
nova_flask/
├── app.py                  # Flask application & routes
├── requirements.txt
├── static/
│   └── data/
│       └── towers.json     # DVC tower & GOMD data (served via /api/towers)
└── templates/
    ├── base.html           # Shared layout (navbar, CSS, Leaflet CDN)
    ├── home.html           # Landing page with animated SVG cover
    ├── login.html          # Login form (POST → session)
    ├── register.html       # Register form (POST → session)
    ├── admin.html          # Admin dashboard (modules + user management)
    ├── projects.html       # Project selection (DVC, MPPTCL)
    ├── dvc.html            # DVC interactive Leaflet map
    └── mpptcl.html         # MPPTCL map (placeholder)
```

## How to Run

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Apply versioned database migrations
flask --app app db upgrade

# 3. Optional: load local demo data into a fresh database
python seed_db.py

# 4. Start the development server
python app.py

# 5. Open in browser
http://localhost:5000
```

For an existing PostgreSQL database, back it up first and follow
[`MIGRATION_GUIDE.md`](MIGRATION_GUIDE.md). The web server never changes the
schema at startup.

## Key Improvements over Single-File HTML

| Feature | Old (vibe-coded HTML) | New (Flask) |
|---|---|---|
| Auth | JS array in browser (insecure) | Server-side session with `flask.session` |
| Routing | JS `show()` function | Proper URL routes (`/login`, `/admin`, `/dvc`) |
| Data | Inline JS `const STATIC_TOWERS` | JSON file served via `/api/towers` API |
| Templates | One giant HTML file | Jinja2 templates with `base.html` inheritance |
| Security | Passwords visible in source | Passwords never sent to browser |
| Extensibility | Hard to add features | Add new routes & templates easily |

## Database

The application uses SQLAlchemy and supports SQLite for local development or
PostgreSQL through `DATABASE_URL`. Schema changes live under `migrations/` and
are applied with `flask --app app db upgrade`.

## Environment Variables (Production)

```bash
export SECRET_KEY="your-strong-random-secret"
export FLASK_ENV=production
```
