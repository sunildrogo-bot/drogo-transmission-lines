"""
projects_routes.py — Blueprint for all dynamic Project / Division / Line data.

Replaces the old hardcoded MPPTCL / DVC project cards with a database-backed
system shared by every module:

    Module ("Transmission Line", "Land Survey", ...)
      └── Project   (+ Add Project: name, contact no, email, country, state, logo)
            └── Division   (Transmission Line only — + Add Division: name, lat, lng)
                  └── Line     (+ Add Line: name, start/end lat-lng, length, towers, KML)

Register in app.py with: app.register_blueprint(projects_bp)
"""
import os
import math
import json
import re
import hashlib
import numpy as np
from datetime import datetime, timedelta
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from werkzeug.utils import secure_filename
from models import db, Project, Division, Line, ActivityLog, TowerPhoto, TowerDefect, TowerReport, User, TowerInspectionStatus, PilotAssignment, ThermalPoint, CorridorPhoto, SmeAssignment, PilotLocation
import settings as app_settings
import thermal_decode

projects_bp = Blueprint('projects_bp', __name__)

LOGO_EXTS  = {'jpg', 'jpeg', 'png'}
KML_EXTS   = {'kml', 'kmz'}
IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'webp'}

UPLOAD_BASE = os.path.join('static', 'uploads')


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


def _admin_guard():
    """Blocks write actions for anyone whose active session role isn't
    Admin — tower photo upload/defect marking/deletion is Admin-only;
    Client User sessions get view-only access."""
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Your account has view-only access to this module.'}), 403
    return None


def _pilot_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'Pilot':
        return jsonify({'error': 'This is only available to Pilot accounts.'}), 403
    return None


def _sme_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') != 'SME':
        return jsonify({'error': 'This is only available to SME accounts.'}), 403
    return None


def _inspect_guard(line_id):
    """Admin can always inspect (mark defects, take thermal measurements,
    mark inspection done, generate reports); an SME can do the same, but
    only on a Line they've actually been assigned — this is the
    per-action enforcement that backs that up (the UI-level "can this
    role see the inspection tools" check is broader/simpler — see
    can_inspect in project_map() — but this is what actually decides
    whether a given write succeeds)."""
    guard = _login_guard()
    if guard:
        return guard
    role = session.get('role')
    if role == 'Admin':
        return None
    if role == 'SME':
        assigned = SmeAssignment.query.filter_by(line_id=line_id, sme_user_id=session.get('user_id')).first()
        if assigned:
            return None
        return jsonify({'error': "You haven't been assigned to this line."}), 403
    return jsonify({'error': 'Your account has view-only access to this module.'}), 403


def _inspect_guard_for_photo(tower_photo_id):
    """Same as _inspect_guard, but resolves the Line from a TowerPhoto id
    first — for routes (defect/thermal-point create+delete) that only
    have a photo id to work with, not a line id directly."""
    photo = TowerPhoto.query.get(tower_photo_id)
    if not photo:
        return jsonify({'error': 'Photo not found.'}), 404
    return _inspect_guard(photo.line_id)


def _visible_project_ids(module_name):
    """None means "no restriction, show every project in this module" —
    Admin sessions always get this, and so does a Client User who hasn't
    had any specific projects assigned for this module (the
    backward-compatible default). Otherwise, the set of project IDs this
    Client User is actually allowed to see."""
    if session.get('role') == 'Admin':
        return None
    user = User.query.get(session.get('user_id'))
    if not user:
        return set()  # no valid session user — show nothing rather than guess
    return user.restricted_project_ids_for_module(module_name)


def _project_access_guard(project):
    """For direct-URL access to a specific project's pages (map/overview/
    info) — the list endpoint filtering above only helps if someone
    actually goes through the list; this closes the gap for anyone who
    has (or guesses) a direct link to a project they're not allowed to
    see. Returns a Flask response to abort with, or None if access is OK."""
    allowed_ids = _visible_project_ids(project.module)
    if allowed_ids is not None and project.id not in allowed_ids:
        return jsonify({'error': "You don't have access to this project."}), 403
    return None


def _ext_ok(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed


def _save_upload(file_storage, subfolder, allowed_exts):
    """Save an uploaded file under static/uploads/<subfolder>/ and return the
    path relative to /static (or '' if no valid file was supplied)."""
    if not file_storage or not file_storage.filename:
        return ''
    if not _ext_ok(file_storage.filename, allowed_exts):
        return None  # signals an invalid file type to the caller
    base_dir = current_app.root_path if hasattr(current_app, 'root_path') else '.'
    folder_fs = os.path.join(base_dir, UPLOAD_BASE, subfolder)
    os.makedirs(folder_fs, exist_ok=True)
    safe_name = secure_filename(file_storage.filename)
    # avoid collisions
    name_root, name_ext = os.path.splitext(safe_name)
    final_name = safe_name
    i = 1
    while os.path.exists(os.path.join(folder_fs, final_name)):
        final_name = f"{name_root}_{i}{name_ext}"
        i += 1
    file_storage.save(os.path.join(folder_fs, final_name))
    return f"uploads/{subfolder}/{final_name}"


def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# Photos attached to a tower must be taken within this many meters of it —
# a generous buffer for normal GPS inaccuracy, not a tight survey
# tolerance. A photo with no GPS EXIF data at all is rejected too (see
# api_upload_tower_photo) — drone photos reliably carry it, so a photo
# without it can't be verified as belonging to this tower.
TOWER_PHOTO_GPS_BUFFER_M = 70


def _extract_gps_from_image(file_storage):
    """Best-effort read of (lat, lng) from an uploaded image's EXIF GPS
    tags. Returns None if the image has no GPS data, isn't a format Pillow
    can read EXIF from, or anything else goes wrong — this is advisory
    validation, not something that should ever crash the upload.

    Uses Image.getexif() + get_ifd(GPSInfo) — Pillow's current, documented
    way to read the GPS IFD. The older/legacy Image._getexif() approach
    (which just returns whatever's under the 'GPSInfo' key of its flat
    dict) doesn't reliably decode the nested GPS IFD across Pillow
    versions — it can come back as a bare IFD offset/reference rather than
    the actual decoded tag dict, silently failing to find real GPS data
    even in images (like standard drone photos) that do have it."""
    try:
        from PIL import Image, ExifTags
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        exif = img.getexif()
        file_storage.stream.seek(0)  # rewind so _save_upload can still read the full file
        if not exif:
            return None
        gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
        if not gps_ifd:
            return None
        gps = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}

        def _to_degrees(value):
            d, m, s = value[0], value[1], value[2]
            return float(d) + float(m) / 60.0 + float(s) / 3600.0

        if 'GPSLatitude' not in gps or 'GPSLongitude' not in gps:
            return None
        lat = _to_degrees(gps['GPSLatitude'])
        if gps.get('GPSLatitudeRef') != 'N':
            lat = -lat
        lng = _to_degrees(gps['GPSLongitude'])
        if gps.get('GPSLongitudeRef') != 'E':
            lng = -lng
        return (lat, lng)
    except Exception:
        try:
            file_storage.stream.seek(0)
        except Exception:
            pass
        return None


@projects_bp.route('/api/me', methods=['GET'])
def api_my_profile():
    """The logged-in user's own basic info — for a simple "my profile"
    view (e.g. SME's dashboard, which has no admin-style settings page
    to check this from). Never exposes anyone else's info; there's no
    id parameter, just whoever's actually signed in."""
    guard = _login_guard()
    if guard:
        return guard
    user = User.query.get(session.get('user_id'))
    if not user:
        return jsonify({'error': 'Not found.'}), 404
    return jsonify({
        'username': user.username,
        'email': user.email,
        'contact': user.contact or '',
        'role': session.get('role', ''),
        'member_since': user.created_at.strftime('%d %b %Y') if user.created_at else '',
        'last_login': user.last_login or 'Never',
    })


# ── Projects ────────────────────────────────────────────────────────────────

@projects_bp.route('/api/projects', methods=['GET'])
def api_list_projects():
    guard = _login_guard()
    if guard:
        return guard
    module = request.args.get('module', '')
    q = Project.query
    if module:
        q = q.filter_by(module=module)
    projects = q.order_by(Project.created_at.asc()).all()

    # Project-wise access restriction (on top of module-level access,
    # which already gated the page/nav getting here) — only actually
    # narrows anything down for a Client User who's had specific projects
    # assigned; everyone else sees the full list unfiltered.
    if module:
        allowed_ids = _visible_project_ids(module)
        if allowed_ids is not None:
            projects = [p for p in projects if p.id in allowed_ids]

    return jsonify({'projects': [p.to_dict() for p in projects]})


@projects_bp.route('/api/projects', methods=['POST'])
def api_create_project():
    guard = _login_guard()
    if guard:
        return guard

    form = request.form
    name = (form.get('name') or '').strip()
    module = (form.get('module') or '').strip()
    email = (form.get('email') or '').strip()

    if not name:
        return jsonify({'error': 'Project name is required.'}), 400
    if not module:
        return jsonify({'error': 'Module is required.'}), 400
    if email and '@' not in email:
        return jsonify({'error': 'Invalid email address.'}), 400

    # Multi-select — a project can do RGB only, Thermal only, or both.
    # Sent as repeated form fields (checkboxes); default to both if
    # nothing was actually picked, so this never silently produces a
    # project with no inspection type at all.
    raw_types = request.form.getlist('inspection_types')
    inspection_types = sorted({t for t in raw_types if t in ('rgb', 'thermal')}) or ['rgb', 'thermal']

    logo_path = ''
    logo_file = request.files.get('logo')
    if logo_file and logo_file.filename:
        saved = _save_upload(logo_file, 'logos', LOGO_EXTS)
        if saved is None:
            return jsonify({'error': 'Company logo must be a .jpg or .png file.'}), 400
        logo_path = saved

    def _int_or_none(raw):
        raw = (raw or '').strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    project = Project(
        module=module,
        name=name,
        contact_no=(form.get('contact_no') or '').strip(),
        email=email,
        country=(form.get('country') or '').strip(),
        state=(form.get('state') or '').strip(),
        logo_path=logo_path,
        client_name=(form.get('client_name') or '').strip(),
        planned_divisions=_int_or_none(form.get('planned_divisions')),
        planned_towers=_int_or_none(form.get('planned_towers')),
        timeline=(form.get('timeline') or '').strip(),
        inspection_types=json.dumps(inspection_types),
        created_by=session.get('user_id'),
    )
    db.session.add(project)
    db.session.commit()
    return jsonify(project.to_dict()), 201


@projects_bp.route('/api/projects/<int:project_id>', methods=['GET'])
def api_get_project(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    data = project.to_dict()
    data['divisions'] = [d.to_dict() for d in project.divisions]
    return jsonify(data)


@projects_bp.route('/api/projects/<int:project_id>', methods=['DELETE'])
def api_delete_project(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    name, module = project.name, project.module
    db.session.delete(project)
    ActivityLog.log(action='delete', entity_type='Project', entity_name=name,
                     module=module, performed_by=session.get('user_name', ''))
    db.session.commit()
    return jsonify({'deleted': project_id})


# ── Project map page (dynamic Transmission Line view) ────────────────────────

@projects_bp.route('/projects/<int:project_id>/map')
def project_map(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'
    # SME gets the same inspection tools (marking, thermal measurement,
    # corridor viewing, report generation) as Admin, just never the
    # upload buttons — those stay gated on is_admin specifically. This is
    # a page-wide flag for simplicity; the actual write actions are still
    # checked per-line against that SME's real assignment server-side
    # (see _inspect_guard) — an SME who isn't assigned to a given line
    # will see the tools but have their save rejected, rather than the
    # tools being hidden per-line here too.
    can_inspect = is_admin or session.get('role') == 'SME'
    ActivityLog.log(action='enter_project', entity_type='Project', entity_name=project.name,
                     module=project.module, performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return render_template('project_map.html', project=project, user_name=session.get('user_name', ''),
                            back_endpoint=back_endpoint, is_admin=is_admin, can_inspect=can_inspect)


def _build_project_defect_summary(project):
    """Every stat, chart-ready number, and grouped defect list needed for
    both the web Overview page and the PDF report — computed once here so
    the two can never drift out of sync with each other. Web-only
    presentation details (the CSS conic-gradient string, bar-chart
    percentages) are layered on top of this in project_overview() rather
    than baked in here, since the PDF report has no use for them."""
    divisions = project.divisions
    lines = [l for d in divisions for l in d.lines]
    line_ids = [l.id for l in lines]

    # line_id -> (line, division), so each defect row can show which line
    # and division it came from without a query per row.
    line_lookup = {}
    for d in divisions:
        for l in d.lines:
            line_lookup[l.id] = (l, d)

    defects = []
    if line_ids:
        defects = (TowerDefect.query
                   .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
                   .filter(TowerPhoto.line_id.in_(line_ids))
                   .order_by(TowerDefect.created_at.desc()).all())

    severity_counts = {'Critical': 0, 'Major': 0, 'Minor': 0}
    division_defect_counts = {}
    defect_type_counts = {}
    defect_rows = []
    photographed_towers = set()
    for defect in defects:
        photo = defect.photo
        line, division = line_lookup.get(photo.line_id, (None, None))
        sev = defect.severity if defect.severity in severity_counts else 'Minor'
        severity_counts[sev] += 1
        div_name = division.name if division else '—'
        division_defect_counts[div_name] = division_defect_counts.get(div_name, 0) + 1
        dtype = defect.defect_type or 'Unspecified'
        defect_type_counts[dtype] = defect_type_counts.get(dtype, 0) + 1
        photographed_towers.add((photo.line_id, photo.tower_label))
        try:
            shape_coords = json.loads(defect.shape_coords) if defect.shape_coords else []
        except (TypeError, ValueError):
            shape_coords = []
        defect_rows.append({
            'id': defect.id,
            'component_name': defect.component_name,
            'defect_type': defect.defect_type,
            'severity': defect.severity,
            'location': defect.location,
            'status': defect.status,
            'comments': defect.comments,
            'tower_label': photo.tower_label,
            'line_id': photo.line_id,
            'line_name': line.name if line else '—',
            'division_name': div_name,
            'created_at': defect.created_at,
            'created_by': defect.created_by,
            'image_path': photo.image_path or '',
            'image_url': f'/static/{photo.image_path}' if photo.image_path else '',
            'shape_type': defect.shape_type,
            'shape_coords': shape_coords,
        })

    total_defects = len(defect_rows)

    # Per-division breakdown — lines, towers, and defects for each division
    # on its own, not just the project-wide totals.
    division_stats = []
    for d in divisions:
        d_lines = d.lines
        d_towers = sum(l.tower_count or 0 for l in d_lines)
        division_stats.append({
            'name': d.name,
            'line_count': len(d_lines),
            'tower_count': d_towers,
            'defect_count': division_defect_counts.get(d.name, 0),
        })

    # Defects grouped by tower (division -> line -> tower) — kept for
    # anything that still wants "everything found at this tower" framing.
    tower_groups_map = {}
    tower_group_order = []
    for row in defect_rows:
        key = (row['division_name'], row['line_name'], row['tower_label'])
        if key not in tower_groups_map:
            tower_groups_map[key] = []
            tower_group_order.append(key)
        tower_groups_map[key].append(row)
    tower_group_order.sort(key=lambda k: (k[0], k[1], k[2]))
    tower_groups = [
        {
            'division_name': key[0], 'line_name': key[1], 'tower_label': key[2],
            'defects': tower_groups_map[key],
        }
        for key in tower_group_order
    ]

    # Defects grouped by TYPE instead — "here's every Anti Climbing
    # Device defect, wherever it is" rather than "here's everything at
    # this tower". Each row keeps its own tower/division/line/location
    # since that's no longer implied by a shared group header. Ordered
    # by count (most-marked type first), matching the bar chart above it.
    type_groups_map = {}
    for row in defect_rows:
        key = row['defect_type'] or 'Unspecified'
        type_groups_map.setdefault(key, []).append(row)
    type_groups = [
        {'defect_type': key, 'defects': type_groups_map[key]}
        for key in sorted(type_groups_map.keys(), key=lambda k: -len(type_groups_map[k]))
    ]

    return {
        'division_count': len(divisions),
        'line_count': len(lines),
        'tower_count': sum(l.tower_count or 0 for l in lines),
        'towers_photographed': len(photographed_towers),
        'total_defects': total_defects,
        'severity_counts': severity_counts,
        'division_defect_counts': division_defect_counts,
        'defect_type_counts': defect_type_counts,
        'division_stats': division_stats,
        'defect_rows': defect_rows,
        'tower_groups': tower_groups,
        'type_groups': type_groups,
    }


@projects_bp.route('/projects/<int:project_id>/overview')
def project_overview(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')
    is_admin = session.get('role') == 'Admin'

    s = _build_project_defect_summary(project)
    total_defects = s['total_defects']
    severity_counts = s['severity_counts']
    division_defect_counts = s['division_defect_counts']
    defect_type_counts = s['defect_type_counts']

    # Severity pie — same CSS conic-gradient technique as the chimney
    # module's overview page. Web-only presentation, not part of the
    # shared summary above.
    def _deg(n):
        return round((n / total_defects * 360), 2) if total_defects else 0
    crit_deg = _deg(severity_counts['Critical'])
    major_deg = _deg(severity_counts['Major'])
    if total_defects:
        severity_pie_gradient = (
            f"conic-gradient(var(--danger) 0deg {crit_deg}deg, "
            f"#e0a53a {crit_deg}deg {crit_deg + major_deg}deg, "
            f"var(--success) {crit_deg + major_deg}deg 360deg)"
        )
    else:
        severity_pie_gradient = 'conic-gradient(var(--border) 0deg 360deg)'

    # Bar chart — defects per division, tallest first.
    max_div_count = max(division_defect_counts.values()) if division_defect_counts else 1
    division_bars = [
        {'name': name, 'count': count, 'pct': round(count / max_div_count * 100)}
        for name, count in sorted(division_defect_counts.items(), key=lambda kv: -kv[1])
    ]

    # Bar chart — one bar per defect TYPE that's actually been marked
    # (Anti Climbing Device, Arcing Horn, etc.) — a type with zero
    # defects marked never shows up here at all, only types that have
    # at least one. A-Z, not by count — this is a menu to click into,
    # not a ranking.
    max_type_count = max(defect_type_counts.values()) if defect_type_counts else 1
    defect_type_bars = [
        {'name': name, 'count': count, 'pct': round(count / max_type_count * 100)}
        for name, count in sorted(defect_type_counts.items(), key=lambda kv: kv[0].lower())
    ]

    return render_template('project_overview.html',
        project=project, user_name=session.get('user_name', ''), is_admin=is_admin,
        back_endpoint=back_endpoint,
        division_count=s['division_count'], line_count=s['line_count'], tower_count=s['tower_count'],
        towers_photographed=s['towers_photographed'],
        total_defects=total_defects, severity_counts=severity_counts,
        severity_pie_gradient=severity_pie_gradient, division_bars=division_bars,
        defect_type_bars=defect_type_bars,
        division_stats=s['division_stats'], tower_groups=s['tower_groups'],
        type_groups=s['type_groups'],
        defect_rows=s['defect_rows'],
    )


@projects_bp.route('/projects/<int:project_id>/defects/<path:defect_type>')
def project_defects_by_type(project_id, defect_type):
    """The list a 'Defects by Type' bar links to — every defect of that
    one type, wherever it is, with its tower/position/severity/status.
    Reuses the same summary builder as the Overview page so the counts
    always agree with the bar the person clicked."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')

    s = _build_project_defect_summary(project)
    defects = [row for row in s['defect_rows'] if (row['defect_type'] or 'Unspecified') == defect_type]

    return render_template('defect_type_list.html',
        project=project, user_name=session.get('user_name', ''),
        back_endpoint=back_endpoint, defect_type=defect_type, defects=defects,
    )


@projects_bp.route('/projects/<int:project_id>/defects/by-tower')
def project_defects_by_tower(project_id):
    """Line dropdown -> tower list (with a defect count per tower) ->
    click a tower to see its full defect list. All the per-line/per-tower
    counts are computed once here and handed to the page as one JSON
    blob, so switching lines/towers in the browser is instant — no
    round-trip needed until a tower's defect list is actually opened."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    from users import MODULE_ROUTES
    back_endpoint = MODULE_ROUTES.get(project.module, 'projects')

    s = _build_project_defect_summary(project)

    # line_id -> {tower_label -> count}, built from the same defect_rows
    # the rest of the Overview page uses, so this always agrees with it.
    line_tower_counts = {}
    for row in s['defect_rows']:
        line_tower_counts.setdefault(row['line_id'], {}).setdefault(row['tower_label'], 0)
        line_tower_counts[row['line_id']][row['tower_label']] += 1

    lines_data = []
    for division in project.divisions:
        for line in division.lines:
            towers = line_tower_counts.get(line.id, {})
            lines_data.append({
                'id': line.id,
                'name': line.name,
                'division_name': division.name,
                'towers': [
                    {'label': label, 'count': count}
                    for label, count in sorted(towers.items(), key=lambda kv: kv[0])
                ],
            })

    return render_template('defects_by_tower.html',
        project=project, user_name=session.get('user_name', ''),
        back_endpoint=back_endpoint, lines_data=lines_data,
    )


@projects_bp.route('/projects/<int:project_id>/report')
def project_defect_report(project_id):
    """Generates and streams the defect report PDF for download/view.
    Open to both Admin and Client sessions, same as the chimney module's
    report — reuses _build_project_defect_summary() so the numbers in this
    PDF are always identical to what's shown on the Overview page."""
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    summary = _build_project_defect_summary(project)

    try:
        from trans_report import build_trans_report_pdf
        static_root = os.path.join(current_app.root_path, 'static')
        pdf_buf = build_trans_report_pdf(project, summary, static_root)
    except Exception as e:
        current_app.logger.exception('TRANS report generation failed for project %s', project_id)
        return jsonify({'error': f'Report generation failed: {e}'}), 500

    from flask import send_file
    safe_name = ''.join(c for c in project.name if c.isalnum() or c in ' _-').strip().replace(' ', '_')
    download_name = f'{safe_name or "transmission_line"}_inspection_report.pdf'
    return send_file(pdf_buf, as_attachment=False, download_name=download_name, mimetype='application/pdf')


@projects_bp.route('/projects/<int:project_id>/info')
def project_info(project_id):
    guard = _login_guard()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    guard = _project_access_guard(project)
    if guard:
        return guard
    return render_template('project_info.html', project=project, user_name=session.get('user_name', ''))


# ── Divisions ──────────────────────────────────────────────────────────────

@projects_bp.route('/api/projects/<int:project_id>/divisions', methods=['GET'])
def api_list_divisions(project_id):
    guard = _login_guard()
    if guard:
        return guard
    Project.query.get_or_404(project_id)
    divisions = Division.query.filter_by(project_id=project_id).order_by(Division.created_at.asc()).all()
    if session.get('role') == 'SME':
        # SME only ever sees divisions that actually contain a line
        # they've been assigned — not the whole project structure the
        # way Admin (or even Client, who's assigned at the project
        # level) sees it.
        assigned_division_ids = {
            line_id_division_id[0] for line_id_division_id in
            db.session.query(Line.division_id)
            .join(SmeAssignment, SmeAssignment.line_id == Line.id)
            .filter(SmeAssignment.sme_user_id == session.get('user_id'))
            .distinct().all()
        }
        divisions = [d for d in divisions if d.id in assigned_division_ids]
    return jsonify({'divisions': [d.to_dict() for d in divisions]})


@projects_bp.route('/api/projects/<int:project_id>/divisions', methods=['POST'])
def api_create_division(project_id):
    guard = _login_guard()
    if guard:
        return guard
    Project.query.get_or_404(project_id)

    data = request.get_json(force=True, silent=True) or request.form
    name = (data.get('name') or '').strip()
    try:
        lat = float(data.get('latitude'))
        lng = float(data.get('longitude'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Latitude and longitude must be valid numbers.'}), 400

    if not name:
        return jsonify({'error': 'Division name is required.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'Latitude/longitude out of range.'}), 400

    planned_towers_raw = (data.get('planned_towers') or '').strip() if isinstance(data.get('planned_towers'), str) else data.get('planned_towers')
    planned_towers = None
    if planned_towers_raw not in (None, ''):
        try:
            planned_towers = int(planned_towers_raw)
        except (TypeError, ValueError):
            planned_towers = None

    division = Division(
        project_id=project_id, name=name, latitude=lat, longitude=lng,
        client_name=(data.get('client_name') or '').strip() if isinstance(data.get('client_name'), str) else '',
        state=(data.get('state') or '').strip() if isinstance(data.get('state'), str) else '',
        planned_towers=planned_towers,
    )
    db.session.add(division)
    db.session.commit()
    return jsonify(division.to_dict()), 201


@projects_bp.route('/api/divisions/<int:division_id>', methods=['DELETE'])
def api_delete_division(division_id):
    guard = _login_guard()
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)
    db.session.delete(division)
    db.session.commit()
    return jsonify({'deleted': division_id})


# ── Lines ──────────────────────────────────────────────────────────────────

@projects_bp.route('/api/divisions/<int:division_id>/lines', methods=['GET'])
def api_list_lines(division_id):
    guard = _login_guard()
    if guard:
        return guard
    Division.query.get_or_404(division_id)
    lines = Line.query.filter_by(division_id=division_id).order_by(Line.created_at.asc()).all()
    if session.get('role') == 'SME':
        # Only lines this SME has actually been assigned — same principle
        # as the division filter above.
        assigned_line_ids = {row[0] for row in
                              db.session.query(SmeAssignment.line_id)
                              .filter(SmeAssignment.sme_user_id == session.get('user_id')).all()}
        lines = [l for l in lines if l.id in assigned_line_ids]
    return jsonify({'lines': [l.to_dict() for l in lines]})


@projects_bp.route('/api/divisions/<int:division_id>/lines', methods=['POST'])
def api_create_line(division_id):
    guard = _login_guard()
    if guard:
        return guard
    Division.query.get_or_404(division_id)

    form = request.form
    name = (form.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Line name is required.'}), 400

    try:
        start_lat = float(form.get('start_lat'))
        start_lng = float(form.get('start_lng'))
        end_lat   = float(form.get('end_lat'))
        end_lng   = float(form.get('end_lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Start/end position must be valid lat, long numbers.'}), 400

    length_raw = (form.get('length_km') or '').strip()
    if length_raw:
        try:
            length_km = float(length_raw)
        except ValueError:
            return jsonify({'error': 'Line length must be a number.'}), 400
    else:
        length_km = round(_haversine_km(start_lat, start_lng, end_lat, end_lng), 2)

    try:
        tower_count = int(form.get('tower_count') or 0)
    except ValueError:
        return jsonify({'error': 'Number of towers must be a whole number.'}), 400

    kml_path = ''
    kml_file = request.files.get('kml_file')
    if kml_file and kml_file.filename:
        saved = _save_upload(kml_file, 'kml', KML_EXTS)
        if saved is None:
            return jsonify({'error': 'KML file must have a .kml or .kmz extension.'}), 400
        kml_path = saved
        # The KML itself is authoritative once uploaded — auto-derive the
        # real tower count from its actual Point placemarks rather than
        # trusting whatever number was typed (or defaulted to 0).
        full_kml_path = os.path.join(current_app.root_path, 'static', kml_path)
        auto_count = _count_kml_tower_points(full_kml_path)
        if auto_count is not None:
            tower_count = auto_count

    line = Line(
        division_id=division_id,
        name=name,
        start_lat=start_lat, start_lng=start_lng,
        end_lat=end_lat, end_lng=end_lng,
        length_km=length_km,
        tower_count=tower_count,
        kml_path=kml_path,
    )
    db.session.add(line)
    db.session.commit()
    return jsonify(line.to_dict()), 201


@projects_bp.route('/api/lines/<int:line_id>', methods=['GET'])
def api_get_line(line_id):
    """A single line's own info plus its division/project ids — for
    deep-linking straight to a specific line on the map (e.g. from the
    SME dashboard's "Start Work" card), without the caller needing to
    already know which division it's under."""
    guard = _login_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    division = line.division
    project = division.project if division else None
    return jsonify({
        'line': line.to_dict(),
        'division_id': line.division_id,
        'project_id': project.id if project else None,
    })


@projects_bp.route('/api/lines/<int:line_id>/kml-attributes', methods=['POST'])
def api_set_visible_kml_attrs(line_id):
    """Which raw KML ExtendedData keys Admin wants shown in the Tower
    Details panel — the KML itself is still parsed client-side (same as
    always, via togeojson), this just remembers the chosen subset so
    every tower's panel filters down to it instead of dumping every raw
    field (styleUrl, styleHash, etc.) every time."""
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    attrs = data.get('attrs')
    if not isinstance(attrs, list) or not all(isinstance(a, str) for a in attrs):
        return jsonify({'error': 'attrs must be a list of attribute name strings.'}), 400
    line.visible_kml_attrs = json.dumps(attrs)
    db.session.commit()
    return jsonify(line.to_dict())


_KML_TOWER_FIELD_CANDIDATES = ['tower_no', 'towerno', 'tower_number', 'towernum', 'tower', 'twr_no', 'twrno', 'point_no', 'pointno']


def _kml_local_tag(elem):
    tag = elem.tag
    return tag.split('}')[-1] if '}' in tag else tag


def _count_kml_tower_points(kml_full_path):
    """Counts Point Placemarks in an uploaded KML/KMZ file — used to set
    a line's tower_count automatically from what the KML actually
    contains, instead of relying on a manually typed number that can
    drift out of sync with it. Returns None (leave tower_count alone) if
    the file can't be read/parsed for any reason."""
    import xml.etree.ElementTree as ET
    import zipfile
    try:
        if kml_full_path.lower().endswith('.kmz'):
            with zipfile.ZipFile(kml_full_path) as z:
                kml_name = next((n for n in z.namelist() if n.lower().endswith('.kml')), None)
                if not kml_name:
                    return None
                root = ET.fromstring(z.read(kml_name))
        else:
            root = ET.parse(kml_full_path).getroot()
    except Exception:
        return None

    count = 0
    for placemark in root.iter():
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        for child in placemark.iter():
            if _kml_local_tag(child) == 'Point':
                count += 1
                break
    return count


def _kml_placemark_props(placemark_elem):
    """{name: value} for a Placemark's ExtendedData/SimpleData/Data fields
    plus its own <name> — close enough to what the frontend's togeojson
    parse produces to reuse the exact same tower-label matching logic."""
    props = {}
    name_text = None
    for child in placemark_elem.iter():
        tag = _kml_local_tag(child)
        if tag == 'name' and name_text is None:
            name_text = (child.text or '').strip()
        elif tag == 'Data':
            key = child.get('name')
            value_text = None
            for sub in child:
                if _kml_local_tag(sub) == 'value':
                    value_text = (sub.text or '').strip()
                    break
            if key and value_text:
                props[key] = value_text
        elif tag == 'SimpleData':
            key = child.get('name')
            if key and child.text:
                props[key] = child.text.strip()
    if name_text:
        props.setdefault('name', name_text)
    return props


def _kml_pick_tower_label(props):
    """Mirrors the frontend's pickTowerLabel() exactly, so a point edited
    here matches the same point the Admin was looking at in the browser."""
    def norm(s):
        return ''.join(ch for ch in s.lower() if ch.isalnum())
    for cand in _KML_TOWER_FIELD_CANDIDATES:
        cand_norm = norm(cand)
        for k, v in props.items():
            if norm(k) == cand_norm and v:
                return str(v)
    if props.get('name'):
        return str(props['name'])
    return 'Point'


def _kml_remove_placemark(root, placemark):
    """xml.etree has no getparent() (that's an lxml-only feature) — has
    to find the direct parent by searching, then remove from there."""
    for parent in root.iter():
        if placemark in list(parent):
            parent.remove(placemark)
            return True
    return False



@projects_bp.route('/api/lines/<int:line_id>/kml-edit', methods=['POST'])
def api_edit_kml(line_id):
    """Rewrites the line's KML file to match the map editor's current
    state exactly: existing points move to their new coordinates, points
    no longer present get removed, points with no existing match get
    added as new Placemarks, and any newly-drawn lines are appended as
    additional LineString Placemarks. Everything else already in the file
    (styles, the original route line, unrelated Placemarks) is left
    untouched. Since Download/View just serves this same file, it
    automatically reflects the edit — no separate "edited version" to
    manage."""
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    if not line.kml_path:
        return jsonify({'error': 'This line has no KML file uploaded.'}), 400

    data = request.get_json(force=True, silent=True) or {}
    points = data.get('points')
    lines_in = data.get('lines') or []
    if not isinstance(points, list):
        return jsonify({'error': 'points must be a list.'}), 400
    if not isinstance(lines_in, list):
        return jsonify({'error': 'lines must be a list.'}), 400

    full_path = os.path.join(current_app.root_path, 'static', line.kml_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'KML file not found on disk.'}), 404

    import xml.etree.ElementTree as ET
    KML_NS = 'http://www.opengis.net/kml/2.2'
    ET.register_namespace('', KML_NS)  # keep the default xmlns unprefixed on write
    try:
        tree = ET.parse(full_path)
    except ET.ParseError as e:
        return jsonify({'error': f'Could not parse the existing KML file: {e}'}), 500
    root = tree.getroot()

    # Validate + collect the desired point set first (label -> lat/lng).
    desired = {}
    for p in points:
        label = str(p.get('label', '')).strip()
        try:
            lat, lng = float(p['lat']), float(p['lng'])
        except (KeyError, TypeError, ValueError):
            continue
        if label and -90 <= lat <= 90 and -180 <= lng <= 180:
            desired[label] = (lat, lng)

    # Find the Document element (or root itself) — new Placemarks get
    # appended there, matching where existing ones already live.
    doc_el = root
    for child in root:
        if _kml_local_tag(child) == 'Document':
            doc_el = child
            break

    existing_labels = set()
    for placemark in list(root.iter()):
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        point_el, coords_el = None, None
        for child in placemark.iter():
            if _kml_local_tag(child) == 'Point':
                point_el = child
            elif point_el is not None and coords_el is None and _kml_local_tag(child) == 'coordinates':
                coords_el = child
        if point_el is None or coords_el is None:
            continue  # not a point Placemark (e.g. the route LineString) — leave entirely alone

        label = _kml_pick_tower_label(_kml_placemark_props(placemark))
        existing_labels.add(label)
        if label in desired:
            lat, lng = desired[label]
            existing_parts = (coords_el.text or '').strip().split(',')
            alt_suffix = f',{existing_parts[2]}' if len(existing_parts) >= 3 else ''
            coords_el.text = f'{lng},{lat}{alt_suffix}'
        else:
            # Present in the file but not in the map's current point set
            # anymore — the pilot/admin deleted it in the editor.
            _kml_remove_placemark(root, placemark)

    # Anything in `desired` that wasn't matched to an existing Placemark
    # is a brand-new point — add it.
    added_points = 0
    for label, (lat, lng) in desired.items():
        if label in existing_labels:
            continue
        placemark = ET.SubElement(doc_el, f'{{{KML_NS}}}Placemark')
        name_el = ET.SubElement(placemark, f'{{{KML_NS}}}name')
        name_el.text = label
        ext_data = ET.SubElement(placemark, f'{{{KML_NS}}}ExtendedData')
        data_el = ET.SubElement(ext_data, f'{{{KML_NS}}}Data', {'name': 'tower_no'})
        value_el = ET.SubElement(data_el, f'{{{KML_NS}}}value')
        value_el.text = label
        point_el = ET.SubElement(placemark, f'{{{KML_NS}}}Point')
        coords_el = ET.SubElement(point_el, f'{{{KML_NS}}}coordinates')
        coords_el.text = f'{lng},{lat},0'
        added_points += 1

    added_lines = 0
    for line_in in lines_in:
        coords = line_in.get('coords')
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            coord_pairs = [(float(c[1]), float(c[0])) for c in coords]  # (lng, lat) for KML
        except (TypeError, ValueError, IndexError):
            continue
        placemark = ET.SubElement(doc_el, f'{{{KML_NS}}}Placemark')
        name_el = ET.SubElement(placemark, f'{{{KML_NS}}}name')
        name_el.text = str(line_in.get('name') or 'New Line')
        ls_el = ET.SubElement(placemark, f'{{{KML_NS}}}LineString')
        coords_el = ET.SubElement(ls_el, f'{{{KML_NS}}}coordinates')
        coords_el.text = ' '.join(f'{lng},{lat},0' for lng, lat in coord_pairs)
        added_lines += 1

    lines_edit_in = data.get('lines_edit') or []
    if not isinstance(lines_edit_in, list):
        return jsonify({'error': 'lines_edit must be a list.'}), 400

    # Collect every existing LineString Placemark in document order, so
    # edits can be matched back by name (if the Placemark has one) or by
    # that same position — exactly how the editor identified them when
    # it first loaded the file.
    linestring_placemarks = []
    for placemark in root.iter():
        if _kml_local_tag(placemark) != 'Placemark':
            continue
        ls_el, coords_el = None, None
        for child in placemark.iter():
            if _kml_local_tag(child) == 'LineString':
                ls_el = child
            elif ls_el is not None and coords_el is None and _kml_local_tag(child) == 'coordinates':
                coords_el = child
        if ls_el is not None and coords_el is not None:
            name_text = None
            for child in placemark:
                if _kml_local_tag(child) == 'name':
                    name_text = (child.text or '').strip() or None
                    break
            linestring_placemarks.append({'coords_el': coords_el, 'name': name_text})

    edited_lines = 0
    for edit in lines_edit_in:
        coords = edit.get('coords')
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        try:
            coord_pairs = [(float(c[1]), float(c[0])) for c in coords]  # (lng, lat) for KML
        except (TypeError, ValueError, IndexError):
            continue

        target = None
        edit_name = edit.get('name')
        if edit_name:
            target = next((p for p in linestring_placemarks if p['name'] == edit_name), None)
        if target is None:
            idx = edit.get('index')
            if isinstance(idx, int) and 0 <= idx < len(linestring_placemarks):
                target = linestring_placemarks[idx]
        if target is None:
            continue  # couldn't confidently match this one — leave the file alone rather than guess wrong

        target['coords_el'].text = ' '.join(f'{lng},{lat},0' for lng, lat in coord_pairs)
        edited_lines += 1

    tree.write(full_path, encoding='utf-8', xml_declaration=True)

    # Points may have been added/removed in this edit — keep tower_count
    # in sync with what the KML now actually contains.
    auto_count = _count_kml_tower_points(full_path)
    if auto_count is not None:
        line.tower_count = auto_count
        db.session.commit()

    return jsonify({
        'updated_points': len(desired), 'added_points': added_points,
        'added_lines': added_lines, 'edited_lines': edited_lines,
    })


@projects_bp.route('/api/lines/<int:line_id>', methods=['DELETE'])
def api_delete_line(line_id):
    guard = _login_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    db.session.delete(line)
    db.session.commit()
    return jsonify({'deleted': line_id})


# ── Tower photos ─────────────────────────────────────────────────────────
# Tower points come from parsing a Line's KML client-side (not individual
# DB rows) — photos are matched to a specific point by line_id + the
# tower's label as it appears in the KML (e.g. "T12"), passed by the client
# exactly as shown in the tower details panel.

@projects_bp.route('/api/lines/<int:line_id>/tower-photos', methods=['GET'])
def api_list_tower_photos(line_id):
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    photos = (TowerPhoto.query
              .filter_by(line_id=line_id, tower_label=tower_label)
              .order_by(TowerPhoto.id.asc()).all())
    # Client sessions only ever see a tower's photos once Admin has
    # explicitly marked that tower's inspection as done — NOT just
    # whether defects happen to be marked. A tower with zero defects is
    # either a genuinely good tower or one nobody's finished reviewing
    # yet; "inspection done" is how Admin distinguishes those, so a good
    # tower still needs that explicit sign-off before a client sees it,
    # same as a tower with real defects does. Enforced here, not just
    # hidden in the UI, so a Client session can't see everything by
    # calling this endpoint directly. Pilot is deliberately excluded from
    # this restriction — a pilot needs to see everything THEY'VE captured
    # regardless of whether Admin has finished reviewing it yet.
    if session.get('role') == 'Client User':
        status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
        if not status or not status.inspection_done:
            photos = []
    return jsonify({'photos': [p.to_dict() for p in photos]})


@projects_bp.route('/api/lines/<int:line_id>/tower-photos', methods=['POST'])
def api_upload_tower_photo(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    tower_label = (request.form.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    # A project only does RGB, only Thermal, or both — reject an upload
    # that doesn't match, rather than silently accepting photos the
    # project was never meant to have. Detected the same way thermal
    # photos are recognized everywhere else in the app: a "_T" (or
    # "_T_something") suffix on the filename.
    project = line.division.project if line.division else None
    allowed_types = project.get_inspection_types() if project else ['rgb', 'thermal']
    stem = re.sub(r'\.[^.]+$', '', image_file.filename)
    is_thermal_upload = bool(re.search(r'_T(_\S+)?$', stem, re.IGNORECASE))
    if is_thermal_upload and 'thermal' not in allowed_types:
        return jsonify({'error': f'This project is RGB-only — "{image_file.filename}" looks like a thermal photo and was not uploaded.'}), 400
    if not is_thermal_upload and 'rgb' not in allowed_types:
        return jsonify({'error': f'This project is Thermal-only — "{image_file.filename}" looks like an RGB photo and was not uploaded.'}), 400

    # Reject an exact re-upload of a photo already on this tower — same
    # bytes, not just a similar filename (a fresh EXIF-preserving copy or
    # a re-run of the same folder upload should never silently create a
    # duplicate row + a duplicate file on disk).
    image_file.stream.seek(0)
    content_hash = hashlib.sha256(image_file.read()).hexdigest()
    image_file.stream.seek(0)
    if TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label, content_hash=content_hash).first():
        return jsonify({'error': f'"{image_file.filename}" is already uploaded for this tower — skipped as a duplicate.'}), 400

    # GPS proximity check: an uploaded photo should actually have been
    # taken at this tower, not some other one. Read the tower's own
    # coordinates from the form (the client has them from the KML point
    # that was clicked), read the photo's own EXIF GPS tag, and compare —
    # drone photos reliably carry accurate GPS in their EXIF, so this is a
    # straightforward accept/reject: within range, upload; too far away,
    # or no GPS data on the photo at all, reject.
    try:
        tower_lat = float(request.form.get('tower_lat'))
        tower_lng = float(request.form.get('tower_lng'))
    except (TypeError, ValueError):
        tower_lat = tower_lng = None

    if tower_lat is not None and tower_lng is not None:
        gps = _extract_gps_from_image(image_file)
        if gps is None:
            return jsonify({
                'error': "This photo has no location data in it, so it can't be verified against this tower. "
                         "Please upload a photo that has GPS data."
            }), 400
        dist_m = _haversine_km(gps[0], gps[1], tower_lat, tower_lng) * 1000
        if dist_m > TOWER_PHOTO_GPS_BUFFER_M:
            return jsonify({
                'error': f"This photo's location is about {round(dist_m)}m from this tower — photos must be "
                         f"taken within {TOWER_PHOTO_GPS_BUFFER_M}m. It looks like it may belong to a "
                         f"different tower."
            }), 400
    else:
        gps = None

    saved = _save_upload(image_file, 'tower_photos', IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = TowerPhoto(
        line_id=line_id,
        tower_label=tower_label,
        image_path=saved,
        uploaded_by=session.get('user_name', ''),
        gps_lat=gps[0] if gps else None,
        gps_lng=gps[1] if gps else None,
        content_hash=content_hash,
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/lines/<int:line_id>/tower-defects-flat', methods=['GET'])
def api_tower_defects_flat(line_id):
    """One entry PER DEFECT (not per photo) for a tower, each carrying its
    parent photo's image_url. Used for the client-facing gallery: marking
    2 defects on the same uploaded photo should show as 2 separate image
    entries there — one per defect, each highlighting just that one
    marking — rather than one image with both defects combined."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    defects = (TowerDefect.query
               .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
               .filter(TowerPhoto.line_id == line_id, TowerPhoto.tower_label == tower_label)
               .order_by(TowerPhoto.id.asc(), TowerDefect.created_at.asc()).all())
    out = []
    for d in defects:
        entry = d.to_dict()
        entry['image_url'] = f'/static/{d.photo.image_path}' if d.photo.image_path else ''
        out.append(entry)
    return jsonify({'defects': out})


@projects_bp.route('/api/lines/<int:line_id>/tower-thermal-points-flat', methods=['GET'])
def api_tower_thermal_points_flat(line_id):
    """Which thermal photos on this tower actually have a measured point/
    rect/line on them — mirrors api_tower_defects_flat's shape (one entry
    per measurement, each carrying its parent photo's image_url), used
    the same way client-side: a thermal photo with zero measurements on
    it shouldn't show in the Client gallery any more than an RGB photo
    with zero marked defects does."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    points = (ThermalPoint.query
              .join(TowerPhoto, ThermalPoint.tower_photo_id == TowerPhoto.id)
              .filter(TowerPhoto.line_id == line_id, TowerPhoto.tower_label == tower_label)
              .order_by(TowerPhoto.id.asc(), ThermalPoint.created_at.asc()).all())
    out = []
    for pt in points:
        entry = pt.to_dict()
        entry['image_url'] = f'/static/{pt.photo.image_path}' if pt.photo.image_path else ''
        out.append(entry)
    return jsonify({'points': out})


@projects_bp.route('/api/divisions/<int:division_id>/photo-coverage', methods=['GET'])
def api_division_photo_coverage(division_id):
    """For every line in this division: how many of its towers have at
    least one photo, out of its total tower_count. One request covers
    the whole division's sidebar at once, rather than one request per
    line — avoids an N+1 pattern when a division has many lines."""
    guard = _login_guard()
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)
    lines = division.lines
    line_ids = [l.id for l in lines]

    photographed_counts = {}
    if line_ids:
        rows = (db.session.query(TowerPhoto.line_id, TowerPhoto.tower_label)
                .filter(TowerPhoto.line_id.in_(line_ids)).distinct().all())
        for line_id, _label in rows:
            photographed_counts[line_id] = photographed_counts.get(line_id, 0) + 1

    return jsonify({
        'coverage': {
            l.id: {'photographed': photographed_counts.get(l.id, 0), 'total': l.tower_count or 0}
            for l in lines
        }
    })


@projects_bp.route('/api/lines/<int:line_id>/tower-photo-labels', methods=['GET'])
def api_tower_photo_labels(line_id):
    """Which tower labels on this line have at least one photo — used for
    the map's photo-indicator dots and the Tower Photo Checklist. Admin
    and Pilot both see every uploaded label regardless of inspection
    status — Admin needs to catch what's missing, Pilot needs to see
    everything they've captured regardless of whether Admin's reviewed
    it yet. A Client session only ever sees labels for towers whose
    inspection has been explicitly marked done, matching
    api_list_tower_photos — otherwise a Client's map dot would point at a
    tower whose photos, once opened, turn out to be empty for them."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    q = db.session.query(TowerPhoto.tower_label).filter_by(line_id=line_id)
    if session.get('role') == 'Client User':
        done_labels = {s.tower_label for s in TowerInspectionStatus.query.filter_by(line_id=line_id, inspection_done=True).all()}
        q = q.filter(TowerPhoto.tower_label.in_(done_labels)) if done_labels else q.filter(db.false())
    rows = q.distinct().all()
    return jsonify({'labels': [r[0] for r in rows]})


@projects_bp.route('/api/lines/<int:line_id>/tower-photo-points', methods=['GET'])
def api_tower_photo_points(line_id):
    """Every individual photo's OWN gps_lat/gps_lng on this line — one
    entry per photo, not deduped to one per tower — for a real per-image
    dot on the map instead of a single dot marking "this tower has
    photos". Same visibility rule as tower-photo-labels above (Client
    only sees labels from towers marked inspection-done). Photos with no
    stored GPS (uploaded before this was tracked, or where EXIF/capture
    GPS wasn't available) are skipped — there's nowhere to put their dot."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    q = (TowerPhoto.query.filter_by(line_id=line_id)
         .filter(TowerPhoto.gps_lat.isnot(None), TowerPhoto.gps_lng.isnot(None)))
    if session.get('role') == 'Client User':
        done_labels = {s.tower_label for s in TowerInspectionStatus.query.filter_by(line_id=line_id, inspection_done=True).all()}
        q = q.filter(TowerPhoto.tower_label.in_(done_labels)) if done_labels else q.filter(db.false())
    photos = q.all()
    return jsonify({'points': [
        {'id': p.id, 'tower_label': p.tower_label, 'lat': p.gps_lat, 'lng': p.gps_lng}
        for p in photos
    ]})


@projects_bp.route('/api/lines/<int:line_id>/tower-summary', methods=['GET'])
def api_tower_summary(line_id):
    """Inspection-done status and defect count for every tower on this
    line, in one call — used by the tower list sidebar so it doesn't
    need a separate request per tower. Two grouped queries (defect
    counts, inspection statuses) rather than N+1 per-tower lookups."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)

    defect_counts = dict(
        db.session.query(TowerPhoto.tower_label, db.func.count(TowerDefect.id))
        .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
        .filter(TowerPhoto.line_id == line_id)
        .group_by(TowerPhoto.tower_label)
        .all()
    )
    inspection_rows = TowerInspectionStatus.query.filter_by(line_id=line_id).all()
    inspection_done = {r.tower_label: bool(r.inspection_done) for r in inspection_rows}

    labels = set(defect_counts.keys()) | set(inspection_done.keys())
    return jsonify({'towers': {
        label: {'defect_count': defect_counts.get(label, 0), 'inspection_done': inspection_done.get(label, False)}
        for label in labels
    }})


@projects_bp.route('/api/lines/<int:line_id>/tower-zones', methods=['GET'])
def api_get_tower_zones(line_id):
    """All zone classifications set so far for this line's towers, plus
    which ones the Pilot has already submitted — one call, used to
    populate the pilot's tower list and map markers without a separate
    request per tower."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    rows = TowerInspectionStatus.query.filter_by(line_id=line_id).filter(TowerInspectionStatus.zone != '').all()
    submitted_rows = TowerInspectionStatus.query.filter_by(line_id=line_id, pilot_submitted=True).all()
    return jsonify({
        'zones': {r.tower_label: r.zone for r in rows},
        'submitted_labels': [r.tower_label for r in submitted_rows],
    })


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/inspection-status', methods=['GET'])
def api_get_inspection_status(line_id, tower_label):
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if status:
        return jsonify(status.to_dict())
    return jsonify({'line_id': line_id, 'tower_label': tower_label, 'inspection_done': False, 'marked_by': '', 'marked_at': '',
                     'zone': '', 'zone_set_by': '', 'zone_set_at': ''})


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/inspection-status', methods=['POST'])
def api_set_inspection_status(line_id, tower_label):
    guard = _inspect_guard(line_id)
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    data = request.get_json(force=True, silent=True) or {}
    done = bool(data.get('inspection_done'))

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status:
        status = TowerInspectionStatus(line_id=line_id, tower_label=tower_label)
        db.session.add(status)
    status.inspection_done = done
    status.marked_by = session.get('user_name', '') if done else ''
    status.marked_at = datetime.utcnow() if done else None
    db.session.commit()
    return jsonify(status.to_dict())


VALID_ZONES = {'red', 'yellow', 'green'}


def _pilot_or_admin_guard():
    guard = _login_guard()
    if guard:
        return guard
    if session.get('role') not in ('Pilot', 'Admin'):
        return jsonify({'error': 'Only Pilot and Admin accounts can set a tower\'s zone.'}), 403
    return None


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/zone', methods=['POST'])
def api_set_tower_zone(line_id, tower_label):
    """Pilot's required risk-zone classification for a tower — set before
    capturing a photo there. Admin can also set/correct it, but Client
    never can (view-only, same as everything else client-facing)."""
    guard = _pilot_or_admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()
    data = request.get_json(force=True, silent=True) or {}
    zone = (data.get('zone') or '').strip().lower()
    if zone not in VALID_ZONES:
        return jsonify({'error': "Zone must be 'red', 'yellow', or 'green'."}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status:
        status = TowerInspectionStatus(line_id=line_id, tower_label=tower_label)
        db.session.add(status)
    status.zone = zone
    status.zone_set_by = session.get('user_name', '')
    status.zone_set_at = datetime.utcnow()
    db.session.commit()
    return jsonify(status.to_dict())


@projects_bp.route('/api/lines/<int:line_id>/towers/<path:tower_label>/pilot-submit', methods=['POST'])
def api_pilot_submit_tower(line_id, tower_label):
    """Pilot's "done here" for a tower — zone must already be set; photos
    themselves come from the actual drone, not through this app, so
    there's nothing to upload here. This just closes out the visit."""
    guard = _pilot_or_admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = tower_label.strip()

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.zone:
        return jsonify({'error': 'Set a zone (red/yellow/green) for this tower before submitting.'}), 400
    status.pilot_submitted = True
    status.pilot_submitted_by = session.get('user_name', '')
    status.pilot_submitted_at = datetime.utcnow()
    db.session.commit()
    return jsonify(status.to_dict())


@projects_bp.route('/api/pilot/location', methods=['POST'])
def api_ping_pilot_location():
    """The Pilot's own device reports its current position here,
    periodically, while their portal tab is open — with their consent
    (browser geolocation permission). Overwrites their previous ping;
    this isn't a location history, just "where are they right now"."""
    guard = _pilot_guard()
    if guard:
        return guard
    data = request.get_json(force=True, silent=True) or {}
    try:
        lat = float(data.get('lat'))
        lng = float(data.get('lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'lat and lng are required numbers.'}), 400
    if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
        return jsonify({'error': 'lat/lng out of range.'}), 400

    loc = PilotLocation.query.get(session.get('user_id'))
    if not loc:
        loc = PilotLocation(pilot_user_id=session.get('user_id'), lat=lat, lng=lng)
        db.session.add(loc)
    else:
        loc.lat, loc.lng, loc.updated_at = lat, lng, datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/pilot-locations', methods=['GET'])
def api_list_pilot_locations():
    """Every Pilot's last-known position — for Admin's live map. Only
    pings from the last 30 minutes count as "current"; anything older
    means that pilot's tab has likely been closed a while, so they're
    left off rather than showing a stale dot."""
    guard = _admin_guard()
    if guard:
        return guard
    cutoff = datetime.utcnow() - timedelta(minutes=30)
    locs = PilotLocation.query.filter(PilotLocation.updated_at >= cutoff).all()
    return jsonify({'locations': [l.to_dict() for l in locs]})


@projects_bp.route('/api/pilots', methods=['GET'])
def api_list_pilots():
    """Users with the Pilot role, for the "Assign Pilot" dropdown when
    uploading/editing a Line's KML."""
    guard = _admin_guard()
    if guard:
        return guard
    from models import Role
    pilot_role = Role.query.filter_by(name='Pilot').first()
    pilots = pilot_role.users if pilot_role else []
    return jsonify({'pilots': [{'id': p.id, 'username': p.username, 'email': p.email} for p in pilots]})


@projects_bp.route('/api/lines/<int:line_id>/assign-pilot', methods=['GET'])
def api_get_line_assignment(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    assignment = PilotAssignment.query.filter_by(line_id=line_id).first()
    return jsonify({'assignment': assignment.to_dict() if assignment else None})


@projects_bp.route('/api/lines/<int:line_id>/assign-pilot', methods=['POST'])
def api_assign_pilot(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    pilot_user_id = data.get('pilot_user_id')

    if not pilot_user_id:
        # Explicit unassign — clears whoever's currently on this line.
        PilotAssignment.query.filter_by(line_id=line_id).delete()
        db.session.commit()
        return jsonify({'ok': True, 'assignment': None})

    pilot = User.query.get(int(pilot_user_id))
    if not pilot or 'Pilot' not in pilot.role_names():
        return jsonify({'error': 'That user does not have the Pilot role.'}), 400

    # One pilot per line at a time — reassigning replaces rather than adds.
    PilotAssignment.query.filter_by(line_id=line_id).delete()
    assignment = PilotAssignment(
        line_id=line_id, pilot_user_id=pilot.id,
        assigned_by=session.get('user_name', ''), seen_by_pilot=False,
    )
    db.session.add(assignment)
    ActivityLog.log(action='assign_pilot', entity_type='Line', entity_name=line.name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=f'Assigned to pilot {pilot.username}')
    db.session.commit()
    return jsonify({'ok': True, 'assignment': assignment.to_dict()})


@projects_bp.route('/api/smes', methods=['GET'])
def api_list_smes():
    """Users with the SME role, for the "Assign SME" dropdown in a Line's
    Details modal."""
    guard = _admin_guard()
    if guard:
        return guard
    from models import Role
    sme_role = Role.query.filter_by(name='SME').first()
    smes = sme_role.users if sme_role else []
    return jsonify({'smes': [{'id': u.id, 'username': u.username, 'email': u.email} for u in smes]})


@projects_bp.route('/api/lines/<int:line_id>/assign-sme', methods=['GET'])
def api_get_line_sme_assignment(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    assignment = SmeAssignment.query.filter_by(line_id=line_id).first()
    return jsonify({'assignment': assignment.to_dict() if assignment else None})


@projects_bp.route('/api/smes/<int:sme_user_id>/assignments', methods=['GET'])
def api_sme_all_assignments(sme_user_id):
    """Every line a specific SME is currently assigned to, across the
    whole system — not just the one line a Details modal happens to be
    open for. Exists so Admin can see (and clean up) a case like "this
    SME got assigned to 6 lines over several testing attempts, only 1
    of which is actually correct" instead of having to check line by
    line."""
    guard = _admin_guard()
    if guard:
        return guard
    rows = SmeAssignment.query.filter_by(sme_user_id=sme_user_id).order_by(SmeAssignment.assigned_at.desc()).all()
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        out.append({
            'assignment_id': a.id, 'line_id': a.line_id,
            'line_name': line.name if line else '—',
            'division_name': division.name if division else '—',
            'assigned_at': a.assigned_at.strftime('%d %b %Y, %H:%M') if a.assigned_at else '',
        })
    return jsonify({'assignments': out})


@projects_bp.route('/api/sme-assignments/<int:assignment_id>', methods=['DELETE'])
def api_delete_sme_assignment(assignment_id):
    """Unassign one specific SME-line pairing directly, by assignment id —
    for cleaning up extras from the all-assignments list above, without
    needing to open that particular line's own Details modal."""
    guard = _admin_guard()
    if guard:
        return guard
    assignment = SmeAssignment.query.get_or_404(assignment_id)
    db.session.delete(assignment)
    db.session.commit()
    return jsonify({'deleted': assignment_id})


@projects_bp.route('/api/lines/<int:line_id>/assign-sme', methods=['POST'])
def api_assign_sme(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)
    data = request.get_json(force=True, silent=True) or {}
    sme_user_id = data.get('sme_user_id')

    if not sme_user_id:
        # Explicit unassign — clears whoever's currently on this line.
        SmeAssignment.query.filter_by(line_id=line_id).delete()
        db.session.commit()
        return jsonify({'ok': True, 'assignment': None})

    sme = User.query.get(int(sme_user_id))
    if not sme or 'SME' not in sme.role_names():
        return jsonify({'error': 'That user does not have the SME role.'}), 400

    # One SME per line at a time — reassigning replaces rather than adds.
    SmeAssignment.query.filter_by(line_id=line_id).delete()
    assignment = SmeAssignment(
        line_id=line_id, sme_user_id=sme.id,
        assigned_by=session.get('user_name', ''), seen_by_sme=False,
    )
    db.session.add(assignment)
    ActivityLog.log(action='assign_sme', entity_type='Line', entity_name=line.name,
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=f'Assigned to SME {sme.username}')
    db.session.commit()
    return jsonify({'ok': True, 'assignment': assignment.to_dict()})


@projects_bp.route('/api/pilot/assignments', methods=['GET'])
def api_pilot_assignments():
    """The current Pilot's own assigned work — what their dashboard's
    "Assigned Work" list and new-assignment popup are built from."""
    guard = _pilot_guard()
    if guard:
        return guard
    rows = (PilotAssignment.query
            .filter_by(pilot_user_id=session.get('user_id'))
            .order_by(PilotAssignment.assigned_at.desc()).all())
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        project = division.project if division else None
        entry = a.to_dict()
        entry['line_name'] = line.name if line else '—'
        entry['division_name'] = division.name if division else '—'
        entry['project_name'] = project.name if project else '—'
        entry['project_module'] = project.module if project else ''
        entry['kml_url'] = f'/static/{line.kml_path}' if line and line.kml_path else ''
        out.append(entry)
    return jsonify({'assignments': out})


@projects_bp.route('/api/pilot/assignments/<int:assignment_id>/acknowledge', methods=['POST'])
def api_acknowledge_assignment(assignment_id):
    guard = _pilot_guard()
    if guard:
        return guard
    assignment = PilotAssignment.query.get_or_404(assignment_id)
    if assignment.pilot_user_id != session.get('user_id'):
        return jsonify({'error': 'Not your assignment.'}), 403
    assignment.seen_by_pilot = True
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/sme/assignments', methods=['GET'])
def api_sme_assignments():
    """The current SME's own assigned work — what their dashboard's card
    grid and new-assignment popup are built from. Each entry also carries
    a towers-done/total tally and how many days it's been since Admin
    assigned it, since the dashboard shows both as a completion
    indicator."""
    guard = _sme_guard()
    if guard:
        return guard
    rows = (SmeAssignment.query
            .filter_by(sme_user_id=session.get('user_id'))
            .order_by(SmeAssignment.assigned_at.desc()).all())
    out = []
    for a in rows:
        line = a.line
        division = line.division if line else None
        project = division.project if division else None
        entry = a.to_dict()
        entry['line_name'] = line.name if line else '—'
        entry['division_name'] = division.name if division else '—'
        entry['project_name'] = project.name if project else '—'
        entry['project_id'] = project.id if project else None
        entry['project_module'] = project.module if project else ''
        entry['tower_count'] = line.tower_count if line else 0
        entry['towers_done'] = (TowerInspectionStatus.query
                                 .filter_by(line_id=a.line_id, inspection_done=True).count()) if line else 0
        entry['days_since_assigned'] = (datetime.utcnow() - a.assigned_at).days if a.assigned_at else 0
        out.append(entry)
    return jsonify({'assignments': out})


@projects_bp.route('/api/sme/assignments/<int:assignment_id>/acknowledge', methods=['POST'])
def api_acknowledge_sme_assignment(assignment_id):
    guard = _sme_guard()
    if guard:
        return guard
    assignment = SmeAssignment.query.get_or_404(assignment_id)
    if assignment.sme_user_id != session.get('user_id'):
        return jsonify({'error': 'Not your assignment.'}), 403
    assignment.seen_by_sme = True
    db.session.commit()
    return jsonify({'ok': True})


@projects_bp.route('/api/lines/<int:line_id>/tower-photos/pilot-capture', methods=['POST'])
def api_pilot_capture_photo(line_id):
    """Upload path for the Pilot's in-app live camera capture.

    Unlike api_upload_tower_photo above (which reads GPS from the
    photo's own EXIF), a canvas-captured frame from getUserMedia has NO
    EXIF data at all — there's nothing embedded to check. So this
    validates against capture_lat/capture_lng instead: the device's live
    GPS reading taken via navigator.geolocation at the moment the pilot
    pressed the shutter, sent alongside the image. Same 70m buffer and
    same rejection behavior as the EXIF-based path, just a different
    source of truth for "where was this actually taken.\""""
    guard = _pilot_guard()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    tower_label = (request.form.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.zone:
        return jsonify({'error': 'Set this tower\'s zone (Red/Yellow/Green) before capturing a photo.'}), 400

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    try:
        tower_lat = float(request.form.get('tower_lat'))
        tower_lng = float(request.form.get('tower_lng'))
        capture_lat = float(request.form.get('capture_lat'))
        capture_lng = float(request.form.get('capture_lng'))
    except (TypeError, ValueError):
        return jsonify({'error': 'Missing location data — GPS must be available to capture.'}), 400

    dist_m = _haversine_km(capture_lat, capture_lng, tower_lat, tower_lng) * 1000
    if dist_m > TOWER_PHOTO_GPS_BUFFER_M:
        return jsonify({
            'error': f"You're about {round(dist_m)}m from this tower — you need to be within "
                     f"{TOWER_PHOTO_GPS_BUFFER_M}m to capture it. Please move to the correct location."
        }), 400

    saved = _save_upload(image_file, 'tower_photos', IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = TowerPhoto(
        line_id=line_id, tower_label=tower_label, image_path=saved,
        uploaded_by=session.get('user_name', ''),
        gps_lat=capture_lat, gps_lng=capture_lng,
    )
    db.session.add(photo)
    ActivityLog.log(action='capture_photo', entity_type='TowerPhoto',
                     entity_name=f"Tower {tower_label} — {line.name}",
                     module='TRANS', performed_by=session.get('user_name', ''), role='Pilot',
                     details=f'Captured within {round(dist_m)}m of tower location')
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/tower-photos/<int:photo_id>', methods=['DELETE'])
def api_delete_tower_photo(photo_id):
    guard = _admin_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'deleted': photo_id})


# ── Corridor photos — anywhere along the line, not tied to a tower ──────
# Same idea as tower photos (GPS-verified upload), but with no 70m-of-a-
# tower requirement and no per-tower grouping — one flat gallery per line,
# each photo optionally carrying a short observation note. Viewing is
# open to any logged-in role; uploading/editing/deleting is Admin-only.

@projects_bp.route('/api/lines/<int:line_id>/corridor-photos', methods=['GET'])
def api_list_corridor_photos(line_id):
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    photos = CorridorPhoto.query.filter_by(line_id=line_id).order_by(CorridorPhoto.created_at.desc()).all()
    return jsonify({'photos': [p.to_dict() for p in photos]})


@projects_bp.route('/api/lines/<int:line_id>/corridor-photos', methods=['POST'])
def api_upload_corridor_photo(line_id):
    guard = _admin_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)

    image_file = request.files.get('image')
    if not image_file or not image_file.filename:
        return jsonify({'error': 'An image file is required.'}), 400

    # No tower/distance check here (that's the whole point of a corridor
    # photo — it can be anywhere) but GPS itself is still required, since
    # there'd be nowhere to put its map dot otherwise.
    gps = _extract_gps_from_image(image_file)
    if gps is None:
        return jsonify({
            'error': "This photo has no location data in it, so it can't be placed on the map. "
                     "Please upload a photo that has GPS data."
        }), 400

    saved = _save_upload(image_file, 'corridor_photos', IMAGE_EXTS)
    if saved is None:
        return jsonify({'error': 'Image must be a .jpg, .png, or .webp file.'}), 400
    if saved == '':
        return jsonify({'error': 'An image file is required.'}), 400

    photo = CorridorPhoto(
        line_id=line_id, image_path=saved, gps_lat=gps[0], gps_lng=gps[1],
        uploaded_by=session.get('user_name', ''),
    )
    db.session.add(photo)
    db.session.commit()
    return jsonify(photo.to_dict()), 201


@projects_bp.route('/api/corridor-photos/<int:photo_id>/observation', methods=['PUT'])
def api_update_corridor_photo_observation(photo_id):
    photo = CorridorPhoto.query.get_or_404(photo_id)
    guard = _inspect_guard(photo.line_id)
    if guard:
        return guard
    data = request.get_json(force=True, silent=True) or {}
    photo.observation = (data.get('observation') or '').strip()
    db.session.commit()
    return jsonify(photo.to_dict())


@projects_bp.route('/api/corridor-photos/<int:photo_id>', methods=['DELETE'])
def api_delete_corridor_photo(photo_id):
    guard = _admin_guard()
    if guard:
        return guard
    photo = CorridorPhoto.query.get_or_404(photo_id)
    db.session.delete(photo)
    db.session.commit()
    return jsonify({'deleted': photo_id})


# ── Tower defects (marked directly on a tower photo) ────────────────────────
# The 2D equivalent of the chimney module's 3D defect markings — a
# polygon/rectangle/circle drawn over the photo, plus a short observation
# form. Viewing is open to both Admin and Client sessions; marking/editing/
# deleting is Admin-only (Client sessions see the marked-up photos but
# can't add to them).

VALID_SHAPE_TYPES = {'polygon', 'rect', 'circle'}
VALID_LOCATIONS = {'Top', 'Middle', 'Bottom'}
VALID_SEVERITIES = {'Minor', 'Major', 'Critical'}
VALID_DEFECT_STATUSES = {'OK', 'Missing'}


@projects_bp.route('/api/tower-photos/<int:photo_id>/defects', methods=['GET'])
def api_list_tower_defects(photo_id):
    guard = _login_guard()
    if guard:
        return guard
    TowerPhoto.query.get_or_404(photo_id)
    defects = (TowerDefect.query.filter_by(tower_photo_id=photo_id)
               .order_by(TowerDefect.created_at.asc()).all())
    return jsonify({'defects': [d.to_dict() for d in defects]})


@projects_bp.route('/api/tower-photos/<int:photo_id>/defects', methods=['POST'])
def api_create_tower_defect(photo_id):
    guard = _inspect_guard_for_photo(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)

    data = request.get_json(force=True, silent=True) or {}
    shape_type = (data.get('shape_type') or '').strip()
    if shape_type not in VALID_SHAPE_TYPES:
        return jsonify({'error': 'shape_type must be one of: polygon, rect, circle.'}), 400

    shape_coords = data.get('shape_coords')
    if not isinstance(shape_coords, list) or len(shape_coords) < 2:
        return jsonify({'error': 'shape_coords must be a list of at least 2 {x, y} points.'}), 400
    try:
        clean_coords = []
        for pt in shape_coords:
            x, y = float(pt['x']), float(pt['y'])
            if not (0 <= x <= 100) or not (0 <= y <= 100):
                return jsonify({'error': 'shape_coords must be percentages between 0 and 100.'}), 400
            clean_coords.append({'x': x, 'y': y})
    except (KeyError, TypeError, ValueError):
        return jsonify({'error': 'Each shape_coords point needs numeric x and y.'}), 400

    location = (data.get('location') or '').strip()
    if location and location not in VALID_LOCATIONS:
        return jsonify({'error': 'location must be one of: Top, Middle, Bottom.'}), 400

    severity = (data.get('severity') or 'Minor').strip()
    if severity not in VALID_SEVERITIES:
        severity = 'Minor'

    status = (data.get('status') or 'OK').strip()
    if status not in VALID_DEFECT_STATUSES:
        status = 'OK'

    defect = TowerDefect(
        tower_photo_id=photo_id,
        shape_type=shape_type,
        shape_coords=json.dumps(clean_coords),
        component_name=(data.get('component_name') or '').strip(),
        location=location,
        defect_type=(data.get('defect_type') or '').strip(),
        severity=severity,
        status=status,
        comments=(data.get('comments') or '').strip(),
        created_by=session.get('user_name', ''),
    )
    db.session.add(defect)
    ActivityLog.log(action='mark_defect', entity_type='TowerDefect',
                     entity_name=f"{defect.component_name or 'Defect'} — Tower {photo.tower_label}",
                     performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify(defect.to_dict()), 201


@projects_bp.route('/api/tower-defects/<int:defect_id>', methods=['DELETE'])
def api_delete_tower_defect(defect_id):
    defect = TowerDefect.query.get_or_404(defect_id)
    guard = _inspect_guard_for_photo(defect.tower_photo_id)
    if guard:
        return guard
    db.session.delete(defect)
    db.session.commit()
    return jsonify({'deleted': defect_id})


# ── Thermal point-temperature measurements ──────────────────────────────
# Click a spot on a thermal photo, get back the actual radiometric
# temperature at that pixel (decoded from the R-JPEG via thermal_decode.py
# / the DJI Thermal SDK) — same idea as DJI Thermal Analysis Tool 3's point
# measurement. Viewing is open to Admin + Client (same as defects); adding/
# deleting a point is Admin-only.

@projects_bp.route('/api/tower-photos/<int:photo_id>/thermal-points', methods=['GET'])
def api_list_thermal_points(photo_id):
    guard = _login_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    points = (ThermalPoint.query.filter_by(tower_photo_id=photo_id)
              .order_by(ThermalPoint.created_at.asc()).all())

    # Whole-frame min/max — for the color-scale bar next to the image, not
    # tied to any specific measurement. Computed with the current default
    # params (distance/humidity/etc); a per-point override only affects
    # that one point's own reading, not this frame-wide range.
    frame_min_c = frame_max_c = None
    image_abs_path = os.path.join(current_app.static_folder, photo.image_path) if photo.image_path else ''
    if image_abs_path and os.path.exists(image_abs_path):
        try:
            matrix = thermal_decode.get_temperature_matrix(image_abs_path)
            frame_min_c = float(np.nanmin(matrix))
            frame_max_c = float(np.nanmax(matrix))
        except Exception:  # noqa: BLE001 — the color bar is a nice-to-have, not worth failing the whole list over
            pass

    return jsonify({
        'points': [p.to_dict() for p in points],
        'frame_min_c': frame_min_c,
        'frame_max_c': frame_max_c,
        'default_params': thermal_decode.DEFAULT_PARAMS_DICT,
    })


def _validate_and_measure_thermal(photo, data):
    """Shared by both the persisting (Admin) and ephemeral (any logged-in
    role) thermal measurement endpoints — validates the request body and
    runs the actual decode, without touching the database either way.
    Returns (shape_type, clean_coords, avg_c, min_c, max_c, raw_avg,
    raw_min, raw_max, error) on success, or (None, error_response,
    status_code) on a validation failure the caller should return as-is."""
    shape_type = (data.get('shape_type') or 'point').strip()
    if shape_type not in ('point', 'rect', 'line'):
        return None, jsonify({'error': "shape_type must be one of: point, rect, line."}), 400

    coords_in = data.get('shape_coords')
    min_needed = 1 if shape_type == 'point' else 2
    if not isinstance(coords_in, list) or len(coords_in) < min_needed:
        return None, jsonify({'error': f'{shape_type} needs at least {min_needed} coordinate(s).'}), 400
    try:
        clean_coords = []
        for pt in coords_in:
            x, y = float(pt['x']), float(pt['y'])
            if not (0 <= x <= 100) or not (0 <= y <= 100):
                return None, jsonify({'error': 'Coordinates must be percentages between 0 and 100.'}), 400
            clean_coords.append({'x': x, 'y': y})
    except (KeyError, TypeError, ValueError):
        return None, jsonify({'error': 'Each coordinate needs numeric x and y.'}), 400

    # Optional per-measurement override of distance/humidity/emissivity/
    # reflection/ambient_temp (the "Parameters" panel) — any key not sent
    # falls back to thermal_decode.DEFAULT_PARAMS_DICT.
    params_in = data.get('params') or {}
    params_dict = {}
    if isinstance(params_in, dict):
        for key in ('distance', 'humidity', 'emissivity', 'reflection', 'ambient_temp'):
            if key in params_in and params_in[key] not in (None, ''):
                try:
                    params_dict[key] = float(params_in[key])
                except (TypeError, ValueError):
                    return None, jsonify({'error': f'params.{key} must be a number.'}), 400

    # Whether a photo is "thermal" is decided by what's actually IN the
    # file (does it have an embedded radiometric data block?), not by its
    # filename — filename conventions vary/get mangled by uploads, and a
    # naming mismatch shouldn't block a photo that genuinely has real
    # thermal data. Extraction is attempted first; the filename is only
    # used afterward, to word the error usefully if it fails.
    image_abs_path = os.path.join(current_app.static_folder, photo.image_path) if photo.image_path else ''
    avg_c = min_c = max_c = raw_avg = raw_min = raw_max = None
    error = 'Photo has no saved image file.'
    if image_abs_path and os.path.exists(image_abs_path):
        coords_pct = [(c['x'], c['y']) for c in clean_coords]
        avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error = thermal_decode.get_shape_stats(image_abs_path, shape_type, coords_pct, params_dict)
        if error and isinstance(error, str) and 'No APP3 segments found' in error:
            filename = os.path.basename(photo.image_path or '')
            stem = re.sub(r'\.[^.]+$', '', filename)
            if not re.search(r'_T(_\S+)?$', stem, re.IGNORECASE):
                error = f"This isn't a thermal photo ({filename}) — temperature measurement only works on thermal photos."
    # error column is capped (VARCHAR(255) on some DBs) — the SDK's own
    # error strings can run longer than that (e.g. "file not found at
    # <full path>"), which was crashing the save outright instead of
    # storing a shorter version of the same message.
    if error and len(error) > 255:
        error = error[:252] + '...'

    return (shape_type, clean_coords, avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error), None, None


@projects_bp.route('/api/tower-photos/<int:photo_id>/thermal-points', methods=['POST'])
def api_create_thermal_point(photo_id):
    guard = _inspect_guard_for_photo(photo_id)
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    data = request.get_json(force=True, silent=True) or {}

    result, err_response, err_status = _validate_and_measure_thermal(photo, data)
    if result is None:
        return err_response, err_status
    shape_type, clean_coords, avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error = result

    point = ThermalPoint(
        tower_photo_id=photo_id,
        x_pct=clean_coords[0]['x'],
        y_pct=clean_coords[0]['y'],
        shape_type=shape_type,
        shape_coords=json.dumps(clean_coords),
        temperature_c=avg_c,
        min_c=min_c,
        max_c=max_c,
        avg_c=avg_c,
        raw_avg=raw_avg,
        raw_min=raw_min,
        raw_max=raw_max,
        error=error,
        label=(data.get('label') or '').strip(),
        created_by=session.get('user_name', ''),
    )
    db.session.add(point)
    db.session.commit()
    return jsonify(point.to_dict()), 201


@projects_bp.route('/api/tower-photos/<int:photo_id>/thermal-measure', methods=['POST'])
def api_measure_thermal_ephemeral(photo_id):
    """Same decode as thermal-points, but nothing is written to the
    database — for Client, who can measure temperature on a thermal photo
    to explore it themselves, but can't leave a permanent marker. Open to
    any logged-in role (Admin included, though Admin normally uses the
    persisting endpoint above); the response shape matches
    ThermalPoint.to_dict() so the frontend can render it identically."""
    guard = _login_guard()
    if guard:
        return guard
    photo = TowerPhoto.query.get_or_404(photo_id)
    data = request.get_json(force=True, silent=True) or {}

    result, err_response, err_status = _validate_and_measure_thermal(photo, data)
    if result is None:
        return err_response, err_status
    shape_type, clean_coords, avg_c, min_c, max_c, raw_avg, raw_min, raw_max, error = result

    return jsonify({
        'id': None,
        'tower_photo_id': photo_id,
        'x_pct': clean_coords[0]['x'],
        'y_pct': clean_coords[0]['y'],
        'shape_type': shape_type,
        'shape_coords': clean_coords,
        'temperature_c': avg_c,
        'min_c': min_c,
        'max_c': max_c,
        'avg_c': avg_c,
        'raw_avg': raw_avg,
        'raw_min': raw_min,
        'raw_max': raw_max,
        'label': '',
        'error': error,
        'created_by': session.get('user_name', ''),
        'created_at': '',
        'ephemeral': True,
    }), 200


@projects_bp.route('/api/thermal-points/<int:point_id>', methods=['DELETE'])
def api_delete_thermal_point(point_id):
    point = ThermalPoint.query.get_or_404(point_id)
    guard = _inspect_guard_for_photo(point.tower_photo_id)
    if guard:
        return guard
    db.session.delete(point)
    db.session.commit()
    return jsonify({'deleted': point_id})


# ── Per-tower RGB Visual Inspection Report ──────────────────────────────
# Generation is Admin-only; once generated, the PDF is stored on disk with
# a TowerReport row pointing at it, so a Client session can download the
# already-generated report without being able to trigger generation
# itself. Re-generating replaces the existing file/row for that tower
# rather than accumulating duplicates.

@projects_bp.route('/api/lines/<int:line_id>/tower-report', methods=['GET'])
def api_get_tower_report(line_id):
    """Does a report already exist for this tower? Used by the panel to
    decide whether to show a "Download Report" link."""
    guard = _login_guard()
    if guard:
        return guard
    Line.query.get_or_404(line_id)
    tower_label = (request.args.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400
    report = (TowerReport.query
              .filter_by(line_id=line_id, tower_label=tower_label)
              .order_by(TowerReport.generated_at.desc()).first())
    return jsonify({'report': report.to_dict() if report else None})


@projects_bp.route('/api/lines/<int:line_id>/tower-report', methods=['POST'])
def api_generate_tower_report(line_id):
    guard = _inspect_guard(line_id)
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    data = request.get_json(force=True, silent=True) or {}
    tower_label = (data.get('tower') or '').strip()
    if not tower_label:
        return jsonify({'error': 'tower is required.'}), 400

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if not status or not status.inspection_done:
        return jsonify({'error': 'This tower has not been marked "Inspection Done" yet — mark it done before generating a report.'}), 400

    try:
        tower_lat = float(data.get('tower_lat'))
        tower_lng = float(data.get('tower_lng'))
        coordinates = f'{tower_lat:.5f}, {tower_lng:.5f}'
    except (TypeError, ValueError):
        coordinates = '—'

    photos = TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label).all()
    photo_ids = [p.id for p in photos]
    defects = []
    if photo_ids:
        defects = (TowerDefect.query
                   .filter(TowerDefect.tower_photo_id.in_(photo_ids))
                   .order_by(TowerDefect.created_at.asc()).all())

    photo_by_id = {p.id: p for p in photos}
    defect_dicts = []
    for d in defects:
        photo = photo_by_id.get(d.tower_photo_id)
        entry = d.to_dict()
        entry['image_path'] = photo.image_path if photo else ''
        defect_dicts.append(entry)

    # Thermal — RGB always comes first in the report, thermal appended
    # after (see build_tower_report_pdf); one entry per thermal photo
    # that has at least one measurement on it, each carrying its own
    # SP1/SP2/… points list.
    thermal_points = []
    if photo_ids:
        thermal_points = (ThermalPoint.query
                          .filter(ThermalPoint.tower_photo_id.in_(photo_ids))
                          .order_by(ThermalPoint.tower_photo_id.asc(), ThermalPoint.created_at.asc()).all())
    thermal_by_photo = {}
    for pt in thermal_points:
        thermal_by_photo.setdefault(pt.tower_photo_id, []).append(pt.to_dict())
    thermal_photo_dicts = [
        {'image_path': photo_by_id[photo_id].image_path if photo_by_id.get(photo_id) else '', 'points': pts}
        for photo_id, pts in thermal_by_photo.items()
    ]

    info = {
        'line_name': line.name,
        'tower_id': tower_label,
        'voltage_level': line.voltage_level or '',
        'coordinates': coordinates,
        'survey_date': line.survey_date.strftime('%d %b %Y') if line.survey_date else '',
        'pilot_name': line.pilot_name or '',
        'inspection_name': line.inspection_name or '',
        'report_date': datetime.utcnow().strftime('%d %b %Y'),
    }

    try:
        from tower_report import build_tower_report_pdf
        static_root = os.path.join(current_app.root_path, 'static')
        project = line.division.project if line.division else None
        client_logo_path = project.logo_path if project else ''
        inspection_types = project.get_inspection_types() if project else ['rgb', 'thermal']
        pdf_buf = build_tower_report_pdf(info, defect_dicts, static_root, client_logo_path, thermal_photo_dicts,
                                          inspection_types=inspection_types)
    except Exception as e:
        current_app.logger.exception('Tower report generation failed for line %s tower %s', line_id, tower_label)
        return jsonify({'error': f'Report generation failed: {e}'}), 500

    # Save to disk under static/uploads/tower_reports/, same pattern as
    # _save_upload() but for a PDF we built ourselves rather than an
    # uploaded file.
    folder_fs = os.path.join(current_app.root_path, UPLOAD_BASE, 'tower_reports')
    os.makedirs(folder_fs, exist_ok=True)
    safe_tower = secure_filename(tower_label) or 'tower'
    filename = f'line{line_id}_{safe_tower}_report.pdf'
    full_path = os.path.join(folder_fs, filename)
    with open(full_path, 'wb') as f:
        f.write(pdf_buf.getvalue())
    report_path = f'uploads/tower_reports/{filename}'

    # Replace any existing report for this exact tower rather than
    # accumulating duplicate rows/files each time it's regenerated.
    existing = TowerReport.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if existing:
        existing.report_path = report_path
        existing.generated_by = session.get('user_name', '')
        existing.generated_at = datetime.utcnow()
        report = existing
    else:
        report = TowerReport(
            line_id=line_id, tower_label=tower_label, report_path=report_path,
            generated_by=session.get('user_name', ''),
        )
        db.session.add(report)
    db.session.commit()
    return jsonify(report.to_dict()), 201


@projects_bp.route('/api/tower-reports/<int:report_id>/download', methods=['GET'])
def api_download_tower_report(report_id):
    guard = _login_guard()
    if guard:
        return guard
    report = TowerReport.query.get_or_404(report_id)
    full_path = os.path.join(current_app.root_path, 'static', report.report_path)
    if not os.path.exists(full_path):
        return jsonify({'error': 'Report file not found — try generating it again.'}), 404
    from flask import send_file
    download_name = f'Tower_{secure_filename(report.tower_label)}_Inspection_Report.pdf'
    return send_file(full_path, as_attachment=True, download_name=download_name, mimetype='application/pdf')
