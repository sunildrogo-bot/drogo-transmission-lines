import os
import time
import hmac
import secrets
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, make_response, abort
from functools import wraps
import json
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text as sql_text
from dotenv import load_dotenv
load_dotenv()
# ── App factory ───────────────────────────────────────────────────────────────

def create_app(db_url: str | None = None) -> Flask:
    app = Flask(__name__)

    # ── Config ────────────────────────────────────────────────────────────────
    configured_secret = os.environ.get('SECRET_KEY', '').strip()
    if not configured_secret:
        if os.environ.get('DEPLOYMENT_ENV', '').strip().casefold() == 'production':
            raise RuntimeError('SECRET_KEY is required when DEPLOYMENT_ENV=production.')
        configured_secret = secrets.token_hex(32)
    app.secret_key = configured_secret

    # SQLite by default; override with DATABASE_URL env var for PostgreSQL/MySQL
    base_dir = os.path.abspath(os.path.dirname(__file__))
    default_db = f"sqlite:///{os.path.join(base_dir, 'instance', 'nova.db')}"
    app.config['SQLALCHEMY_DATABASE_URI'] = db_url or os.environ.get('DATABASE_URL', default_db)
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    # Emailed security links are built from this trusted origin, never from
    # the request Host header. Set the public HTTPS origin when deploying.
    app.config['PUBLIC_BASE_URL'] = os.environ.get('PUBLIC_BASE_URL', 'http://127.0.0.1:5000')
    app.config['DEPLOYMENT_ENV'] = os.environ.get('DEPLOYMENT_ENV', 'development').casefold()
    app.config['STORAGE_BACKEND'] = os.environ.get('STORAGE_BACKEND', 'local')
    app.config['STORAGE_BUCKET'] = os.environ.get('STORAGE_BUCKET', '')
    app.config['STORAGE_PREFIX'] = os.environ.get('STORAGE_PREFIX', '')
    app.config['STORAGE_ENDPOINT_URL'] = os.environ.get('STORAGE_ENDPOINT_URL', '')
    app.config['STORAGE_REGION'] = os.environ.get('STORAGE_REGION', '')
    app.config['MAP_TILE_MODE'] = os.environ.get('MAP_TILE_MODE', 'osm').casefold()
    app.config['PMTILES_URL'] = os.environ.get('PMTILES_URL', '/static/maps/region.pmtiles')
    app.config['MAP_TILE_URL'] = os.environ.get('MAP_TILE_URL', 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png')
    app.config['MAP_TILE_ATTRIBUTION'] = os.environ.get('MAP_TILE_ATTRIBUTION', '&copy; OpenStreetMap contributors')
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_SECURE'] = os.environ.get('SESSION_COOKIE_SECURE', '').casefold() in {'1', 'true', 'yes'}
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(
        minutes=max(15, int(os.environ.get('SESSION_LIFETIME_MINUTES', '480'))))
    proxy_count = max(0, min(2, int(os.environ.get('TRUST_PROXY_COUNT', '0'))))
    if proxy_count:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_count, x_proto=proxy_count,
                                x_host=proxy_count, x_port=proxy_count)

    # ── Extensions ────────────────────────────────────────────────────────────
    from models import db, ActivityLog, User
    db.init_app(app)

    # ── Request security ────────────────────────────────────────────────────
    # A per-session token protects every state-changing browser request. Jinja
    # pages receive it through csrf_token(); the React client fetches it from
    # /api/csrf-token before its first write.
    def _csrf_token():
        token = session.get('_csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['_csrf_token'] = token
        return token

    @app.context_processor
    def _security_template_context():
        return {'csrf_token': _csrf_token,
                'map_tile_mode': app.config['MAP_TILE_MODE'],
                'pmtiles_url': app.config['PMTILES_URL'],
                'map_tile_url': app.config['MAP_TILE_URL'],
                'map_tile_attribution': app.config['MAP_TILE_ATTRIBUTION']}

    def _clear_authenticated_session():
        """Clear authentication state without rotating the CSRF token."""
        csrf_token = session.get('_csrf_token')
        session.clear()
        if csrf_token:
            session['_csrf_token'] = csrf_token

    @app.before_request
    def _validate_authenticated_session():
        """Re-check account state on every authenticated application request.

        Flask's default session is stored in a signed browser cookie. Without
        this database check, disabling a user or changing their permissions
        would leave an already-issued cookie valid until it expired.
        """
        if 'user_id' not in session:
            return None
        session.permanent = True
        now = datetime.utcnow()
        try:
            last_activity = datetime.fromisoformat(session.get('_last_activity', ''))
        except (TypeError, ValueError):
            last_activity = now
        if now - last_activity > app.config['PERMANENT_SESSION_LIFETIME']:
            _clear_authenticated_session()
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Your session expired. Please sign in again.'}), 401
            return redirect(url_for('login', next=request.path))
        session['_last_activity'] = now.isoformat()
        # Public CSS/JS/images do not need an account query. Private uploaded
        # media is deliberately excluded and is validated here and below.
        if request.path.startswith('/static/') and not request.path.startswith('/static/uploads/'):
            return None

        try:
            user = db.session.get(User, int(session.get('user_id')))
            session_version = int(session.get('session_version'))
        except (TypeError, ValueError):
            user = None
            session_version = -1

        roles = user.role_names() if user else []
        invalid = (
            user is None
            or user.status != 'Active'
            or session_version != int(user.session_version or 1)
            or session.get('role') not in roles
        )
        if invalid:
            _clear_authenticated_session()
            # A stale cookie must not prevent a fresh login or a public form.
            if request.endpoint in {
                'login', 'forgot_password', 'reset_password',
                'auth_api_bp.api_login', 'api_csrf_token', 'api_submit_contact',
            }:
                return None
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Your session is no longer valid. Please sign in again.'}), 401
            return redirect(url_for('login', next=request.path))

        # Keep non-security display/access data synchronized with the database.
        session['user_name'] = user.username
        session['user_email'] = user.email
        session['all_roles'] = roles
        session['modules'] = user.module_names()
        return None

    @app.before_request
    def _protect_state_changes():
        if request.method not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            return None
        expected = session.get('_csrf_token')
        supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf_token')
        if expected and supplied and hmac.compare_digest(str(expected), str(supplied)):
            return None
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Invalid or missing CSRF token. Refresh the page and try again.'}), 400
        return 'Invalid or missing CSRF token. Refresh the page and try again.', 400

    @app.before_request
    def _protect_uploaded_media():
        if not request.path.startswith('/static/uploads/'):
            return None
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        from access_control import can_access_upload
        stored_path = request.path[len('/static/'):]
        if not can_access_upload(stored_path):
            abort(403)
        return None

    # ── Live portal load tracking — see load_monitor.py for the honest
    #    single-process caveat. Times every single request; deliberately
    #    lightweight (in-memory, no DB write per request) so this doesn't
    #    become load of its own.
    from load_monitor_shared import record_request

    # The monitoring endpoints themselves (polled every 1s by the dashboard's
    # own live chart) must NOT be counted as "load" — otherwise the monitor
    # measures its own polling traffic rather than real portal usage. Just
    # having the dashboard open would guarantee 60 requests/minute from this
    # alone, which was already enough to push the badge into "moderate"
    # (>40/min) or even "high" (>120/min with a couple of tabs open) with
    # zero actual user activity anywhere else in the app.
    LOAD_MONITOR_EXCLUDED_PATHS = {
        '/api/dashboard/live-load',
        '/api/dashboard/live-load-history',
    }

    @app.before_request
    def _load_monitor_start():
        request._load_monitor_start_time = time.time()

    @app.after_request
    def _load_monitor_end(response):
        start = getattr(request, '_load_monitor_start_time', None)
        if start is not None:
            record_request(
                (time.time() - start) * 1000,
                user_id=session.get('user_id'),
                count_toward_load=request.path not in LOAD_MONITOR_EXCLUDED_PATHS,
            )
        return response

    @app.after_request
    def _cache_uploaded_media(response):
        # Tower photos, thermal images, KML, etc. live under
        # /static/uploads/ and NEVER change once written — every upload
        # gets a unique path. Flask's default /static handler doesn't set
        # a long cache, so browsers were re-requesting/re-validating every
        # image on every view (hundreds per tower), which is a big part of
        # the loading lag. These responses are private because access is
        # user/project scoped; a browser may cache them briefly, but a shared
        # CDN/proxy must never store them as public content. Deliberately
        # scoped to uploaded media only: app JS/CSS use Flask's normal policy.
        if (request.path.startswith('/static/uploads/') or request.path.startswith('/uploads/')) and response.status_code in (200, 206, 304):
            response.headers['Cache-Control'] = 'private, max-age=86400'
            response.vary.add('Cookie')
        return response

    @app.after_request
    def _production_security_headers(response):
        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
        response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
        response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(self)')
        response.headers.setdefault('Content-Security-Policy',
            "default-src 'self'; img-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; script-src 'self' 'unsafe-inline' https://unpkg.com; connect-src 'self' https:; frame-ancestors 'self'; base-uri 'self'; form-action 'self'")
        if request.is_secure and os.environ.get('ENABLE_HSTS', 'true').casefold() in {'1', 'true', 'yes'}:
            response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
        return response

    @app.get('/healthz')
    def healthz():
        return jsonify({'status': 'ok'})

    @app.get('/readyz')
    def readyz():
        started = time.perf_counter()
        checks, status_code = {}, 200
        try:
            db.session.execute(sql_text('SELECT 1'))
            checks['database'] = 'ok'
        except Exception as exc:
            db.session.rollback(); checks['database'] = str(exc)[:160]; status_code = 503
        try:
            from storage_service import get_storage
            checks['storage'] = get_storage().health()
        except Exception as exc:
            checks['storage'] = {'status': 'error', 'error': str(exc)[:160]}; status_code = 503
        return jsonify({'status': 'ok' if status_code == 200 else 'degraded', 'checks': checks,
                        'response_ms': round((time.perf_counter() - started) * 1000, 2)}), status_code

    from flask_migrate import Migrate
    Migrate(app, db)

    # Schema changes are intentionally NOT performed during application
    # startup. Run `flask --app app db upgrade` before starting this version.

    # ── Blueprints ────────────────────────────────────────────────────────────
    from users_routes import users_bp
    app.register_blueprint(users_bp)

    from projects_routes import projects_bp
    app.register_blueprint(projects_bp)

    from settings_routes import settings_bp
    app.register_blueprint(settings_bp)

    from announcement_routes import announcement_bp
    app.register_blueprint(announcement_bp)

    from help_routes import help_bp
    app.register_blueprint(help_bp)

    from auth_api import auth_api_bp
    app.register_blueprint(auth_api_bp)

    from assistant_api import assistant_bp
    app.register_blueprint(assistant_bp)

    from notification_routes import notification_bp
    app.register_blueprint(notification_bp)

    from training_export_routes import training_export_bp
    app.register_blueprint(training_export_bp)

    from backup_routes import backup_bp
    app.register_blueprint(backup_bp)

    # ── Auth decorators ───────────────────────────────────────────────────────

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            return view(*args, **kwargs)
        return wrapped

    def module_required(module_name):
        def decorator(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if 'user_id' not in session:
                    return redirect(url_for('login'))
                from access_control import has_module_access
                if not has_module_access(module_name):
                    return render_template('home.html',
                        error="You don't have access to this module."), 403
                return view(*args, **kwargs)
            return wrapped
        return decorator

    # Make decorators available inside route definitions below
    app.login_required  = login_required
    app.module_required = module_required

    # ── Routes ────────────────────────────────────────────────────────────────
    import users as user_store

    @app.route('/uploads/<path:filename>')
    def serve_upload(filename):
        # Compatibility route for uploaded files. Access is checked against
        # the current user's project/line/tower permissions before Flask is
        # allowed to read anything from disk.
        if 'user_id' not in session:
            return redirect(url_for('login', next=request.path))
        from access_control import can_access_upload
        if not can_access_upload(f'uploads/{filename}'):
            abort(403)
        from storage_service import get_storage
        try:
            return get_storage().response(f'uploads/{filename}', max_age=86400)
        except (OSError, ValueError, RuntimeError):
            abort(404)

    @app.route('/api/csrf-token')
    def api_csrf_token():
        return jsonify({'csrf_token': _csrf_token()})

    @app.route('/')
    def home():
        # The landing page shows Login when logged out and Logout when
        # logged in. Browsers were caching this HTML, so after logging
        # out (or in) the stale copy still showed the old button until a
        # hard refresh. no-store forces the browser to re-fetch it every
        # time, so the button always reflects the real session state.
        resp = make_response(render_template('home.html'))
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
        return resp

    @app.route('/api/contact', methods=['POST'])
    def api_submit_contact():
        """Public — no login required. Anyone on the homepage's contact
        box can reach out, including someone who has never had an
        account here."""
        from models import ContactInquiry
        data = request.get_json(force=True, silent=True) or {}
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip()
        company = (data.get('company') or '').strip()
        message = (data.get('message') or '').strip()

        if not name:
            return jsonify({'error': 'Name is required.'}), 400
        if not email or '@' not in email:
            return jsonify({'error': 'A valid email is required.'}), 400
        if not message:
            return jsonify({'error': 'Message is required.'}), 400

        inquiry = ContactInquiry(name=name[:120], email=email[:150], company=company[:150], message=message)
        db.session.add(inquiry)
        db.session.commit()
        return jsonify({'ok': True}), 201

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if request.method == 'GET':
            return render_template('login.html')

        email    = request.form.get('email', '')
        password = request.form.get('password', '')
        login_as = request.form.get('login_as', 'Client User')

        from security_controls import clear_login_failures, login_limit, record_login_failure
        limited, retry_after = login_limit(email)
        if limited:
            response = make_response(render_template(
                'login.html',
                error='Too many unsuccessful login attempts. Please wait and try again.',
                login_as=login_as,
            ), 429)
            response.headers['Retry-After'] = str(retry_after)
            return response

        user, error = user_store.authenticate(email, password, login_as=login_as)
        if not user:
            now_limited, retry_after = record_login_failure(email)
            if now_limited:
                response = make_response(render_template(
                    'login.html',
                    error='Too many unsuccessful login attempts. Please wait and try again.',
                    login_as=login_as,
                ), 429)
                response.headers['Retry-After'] = str(retry_after)
                return response
            return render_template('login.html', error=error, login_as=login_as), 401

        clear_login_failures(email)

        session['user_id']    = user['id']
        session['user_name']  = user['username']
        session['user_email'] = user['email']
        session['role']       = login_as
        session['all_roles']  = user['roles']
        session['modules']    = user['modules']
        session['session_version'] = int(user.get('session_version') or 1)
        session['login_at']   = datetime.utcnow().isoformat()

        user_store.record_login(user['id'])
        ActivityLog.log(action='login', entity_type='User', entity_name=user['username'],
                         performed_by=user['username'], role=login_as, details=f'Logged in as {login_as}')
        db.session.commit()

        if login_as == 'Admin':
            return redirect(url_for('admin'))
        if login_as == 'Pilot':
            return redirect(url_for('pilot_dashboard'))
        if login_as == 'SME':
            return redirect(url_for('sme_dashboard'))
        return redirect(url_for('client_dashboard'))

    @app.route('/forgot-password', methods=['GET', 'POST'])
    def forgot_password():
        from models import User
        from security_controls import consume_password_reset, one_time_token, public_url

        if request.method == 'GET':
            return render_template('forgot_password.html')

        email = (request.form.get('email') or '').strip()
        # Always show the same confirmation regardless of whether the
        # email matches an account — telling a stranger "that email
        # isn't registered" is a way to enumerate who has an account
        # here, so the response looks identical either way.
        confirmation = render_template('forgot_password.html',
            sent=True, sent_email=email)

        # Preserve the same response for known and unknown addresses while
        # preventing repeated requests from flooding a mailbox.
        if not consume_password_reset(email):
            return confirmation

        user = User.query.filter(db.func.lower(User.email) == email.lower()).first() if email else None
        if not user:
            return confirmation

        token, token_hash = one_time_token()
        user.reset_token = token_hash
        user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
        db.session.commit()

        reset_url = public_url('reset_password', token=token)
        from mailer import send_password_reset_email
        sent = send_password_reset_email(user.email, user.username, reset_url)
        if not sent:
            # Never print a live password-reset token into terminal logs.
            app.logger.warning('Password reset email could not be delivered.')

        return confirmation

    @app.route('/reset-password/<token>', methods=['GET', 'POST'])
    def reset_password(token):
        from models import User
        from security_controls import token_digest

        user = User.query.filter_by(reset_token=token_digest(token)).first()
        valid = bool(user and user.reset_token_expires and user.reset_token_expires > datetime.utcnow())

        if request.method == 'GET':
            return render_template('reset_password.html', valid=valid, token=token)

        if not valid:
            return render_template('reset_password.html', valid=False, token=token)

        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if len(password) < 12:
            return render_template('reset_password.html', valid=True, token=token,
                                    error='Password must be at least 12 characters.')
        if password != confirm:
            return render_template('reset_password.html', valid=True, token=token,
                                    error='Passwords do not match.')

        from werkzeug.security import generate_password_hash
        user.password_hash = generate_password_hash(password)
        user.session_version = int(user.session_version or 1) + 1
        user.reset_token = None
        user.reset_token_expires = None
        db.session.commit()
        ActivityLog.log(action='reset_password', entity_type='User', entity_name=user.username,
                         performed_by=user.username, role='')
        db.session.commit()

        return render_template('reset_password.html', valid=True, token=token, done=True)

    @app.route('/logout', methods=['POST'])
    def logout():
        # Log how long this session lasted before clearing it, so the
        # Activity Log can show a "worked for" duration per session.
        if 'user_id' in session and 'login_at' in session:
            try:
                started = datetime.fromisoformat(session['login_at'])
                duration = (datetime.utcnow() - started).total_seconds()
                ActivityLog.log(action='logout', entity_type='User', entity_name=session.get('user_name', ''),
                                 performed_by=session.get('user_name', ''), role=session.get('role', ''),
                                 duration_seconds=duration, details='Logged out')
                db.session.commit()
            except Exception:
                pass
        csrf_token = session.get('_csrf_token')
        session.clear()
        if csrf_token:
            session['_csrf_token'] = csrf_token
        return redirect(url_for('home'))

    @app.route('/switch-to-admin', methods=['POST'])
    @login_required
    def switch_to_admin():
        if 'Admin' not in session.get('all_roles', []):
            return redirect(url_for('client_dashboard'))
        session['role'] = 'Admin'
        return redirect(url_for('admin'))

    @app.route('/switch-to-client', methods=['POST'])
    @login_required
    def switch_to_client():
        if 'Client User' not in session.get('all_roles', []):
            return redirect(url_for('admin'))
        session['role'] = 'Client User'
        return redirect(url_for('client_dashboard'))

    @app.route('/admin')
    @login_required
    def admin():
        if session.get('role') != 'Admin':
            return redirect(url_for('client_dashboard'))
        can_switch_client = 'Client User' in session.get('all_roles', [])
        return render_template('admin.html',
            user_name=session['user_name'],
            can_switch_client=can_switch_client)

    @app.route('/dashboard')
    @login_required
    def client_dashboard():
        if session.get('role') == 'Admin':
            return redirect(url_for('admin'))
        if session.get('role') == 'Pilot':
            return redirect(url_for('pilot_dashboard'))
        if session.get('role') == 'SME':
            return redirect(url_for('sme_dashboard'))
        assigned = session.get('modules', [])
        cards = []
        from access_control import has_module_access
        for m in assigned:
            if not has_module_access(m):
                continue
            route = user_store.MODULE_ROUTES.get(m)
            if not route:
                continue
            meta = user_store.MODULE_CARDS.get(m, {})
            cards.append({
                'name': m,
                'route': route,
                'image': meta.get('image'),
                'accent': meta.get('accent', '#b7caf1'),
                'desc': meta.get('desc'),
            })
        can_switch_admin = 'Admin' in session.get('all_roles', [])

        # The client's own end-to-end picture: every project they've
        # actually been given access to (Admin assigns this per-project
        # in User Management), each with its real stats computed the
        # same way the Overview page does — so nothing here can drift
        # out of sync with what they'd see if they opened that project.
        project_cards = []
        from models import User, Project
        from projects_routes import _build_project_defect_summary, _build_project_chart_data, _build_project_activity_data
        user = User.query.get(session.get('user_id'))
        if user:
            for project in sorted(user.allowed_projects, key=lambda p: p.name.lower()):
                if project.module not in user.module_names():
                    continue
                s = _build_project_defect_summary(project)
                chart_data = _build_project_chart_data(s)
                activity_data = _build_project_activity_data(s, project.id)
                from users import MODULE_ROUTES
                project_cards.append({
                    'id': project.id,
                    'name': project.name,
                    'module': project.module,
                    'client_name': project.client_name or '',
                    'division_count': s['division_count'],
                    'line_count': s['line_count'],
                    'tower_count': s['tower_count'],
                    'towers_photographed': s['towers_photographed'],
                    'photos_uploaded': s['photos_uploaded'],
                    'towers_inspected': s['towers_inspected'],
                    'photographed_pct': round(s['towers_photographed'] / s['tower_count'] * 100) if s['tower_count'] else 0,
                    'inspected_pct': round(s['towers_inspected'] / s['tower_count'] * 100) if s['tower_count'] else 0,
                    'division_stats': s['division_stats'],
                    'total_defects': s['total_defects'],
                    'severity_counts': s['severity_counts'],
                    'severity_pie_gradient': chart_data['severity_pie_gradient'],
                    'division_bars': chart_data['division_bars'],
                    'defect_type_bars': chart_data['defect_type_bars'],
                    'open_count': activity_data['open_count'],
                    'closed_count': activity_data['closed_count'],
                    'resolution_pct': activity_data['resolution_pct'],
                    'needs_attention': activity_data['needs_attention'],
                    'aging': activity_data['aging'],
                    'map_url': url_for('projects_bp.project_divisions', project_id=project.id),
                    'overview_url': url_for('projects_bp.project_overview', project_id=project.id),
                    'tower_wise_url': url_for('projects_bp.project_defects_by_tower', project_id=project.id),
                    'report_url': url_for('projects_bp.project_defect_report', project_id=project.id),
                    'csv_export_url': url_for('projects_bp.project_defects_export_csv', project_id=project.id),
                })

        # A quick roll-up across every assigned project — only meaningful
        # (and only shown) when there's more than one, so a single-project
        # client isn't shown a summary of the one thing already right below it.
        portfolio_summary = None
        if len(project_cards) > 1:
            portfolio_summary = {
                'project_count': len(project_cards),
                'total_towers': sum(p['tower_count'] for p in project_cards),
                'total_defects': sum(p['total_defects'] for p in project_cards),
                'total_open': sum(p['open_count'] for p in project_cards),
                'total_critical_open': sum(
                    1 for p in project_cards for d in p['needs_attention']
                ),
            }

        return render_template('client_dashboard.html',
            user_name=session['user_name'],
            user_email=session.get('user_email', ''),
            role=session.get('role', ''),
            all_roles=session.get('all_roles', []),
            modules=cards,
            project_cards=project_cards,
            portfolio_summary=portfolio_summary,
            can_switch_admin=can_switch_admin)

    @app.route('/pilot')
    @login_required
    def pilot_dashboard():
        if session.get('role') != 'Pilot':
            return redirect(url_for('client_dashboard'))
        return render_template('pilot_dashboard.html',
            user_name=session['user_name'])

    @app.route('/sme')
    @login_required
    def sme_dashboard():
        if session.get('role') != 'SME':
            return redirect(url_for('client_dashboard'))
        return render_template('sme_dashboard.html',
            user_name=session['user_name'])

    @app.route('/pilot/lines/<int:line_id>')
    @login_required
    def pilot_line_work(line_id):
        if session.get('role') != 'Pilot':
            return redirect(url_for('client_dashboard'))
        from models import db, Line, PilotAssignment
        line = Line.query.get_or_404(line_id)
        assignment = PilotAssignment.query.filter_by(line_id=line_id, pilot_user_id=session.get('user_id')).first()
        if not assignment:
            return "This line isn't assigned to you.", 403
        kml_url = f'/api/lines/{line.id}/geojson' if line.kml_path else ''
        return render_template('pilot_line_work.html',
            user_name=session['user_name'], line=line, kml_url=kml_url)

    @app.route('/projects')
    @module_required('Transmission Line')
    def projects():
        ActivityLog.log(action='enter_module', entity_type='Module', entity_name='Transmission Line',
                         module='Transmission Line', performed_by=session.get('user_name', ''), role=session.get('role', ''))
        db.session.commit()
        return render_template('projects.html', user_name=session['user_name'], module='Transmission Line')

    @app.route('/trans')
    @module_required('TRANS')
    def trans_dashboard():
        ActivityLog.log(action='enter_module', entity_type='Module', entity_name='TRANS',
                         module='TRANS', performed_by=session.get('user_name', ''), role=session.get('role', ''))
        db.session.commit()
        return render_template('projects.html', user_name=session['user_name'], module='TRANS')

    return app


# ── Entry point ───────────────────────────────────────────────────────────────
app = create_app()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
