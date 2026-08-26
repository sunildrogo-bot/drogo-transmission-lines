"""
settings_routes.py — Blueprint for /settings/* — Admin-only.

Covers:
  - Setting/changing the shared "delete password" required to delete any
    project (Transmission Line / TRANS).
  - Activity log (who deleted what, which module, when).
  - Active users + last login snapshot.

Register in app.py with: app.register_blueprint(settings_bp)
"""
from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for, current_app
from datetime import datetime, date, timedelta
import os

import settings as app_settings
from models import db, ActivityLog, User, Project, TowerDefect, TowerPhoto, Line, PilotLocation, Division, TowerInspectionStatus
from storage_cleanup import delete_stored_files

settings_bp = Blueprint('settings_bp', __name__)


def _login_guard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return None


def _require_admin():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


# ── Page ─────────────────────────────────────────────────────────────────────

@settings_bp.route('/settings')
def settings_page():
    guard = _require_admin()
    if guard:
        return guard
    return render_template(
        'settings.html',
        user_name=session['user_name'],
        has_delete_password=app_settings.has_delete_password(),
        gemini_configured=bool(
            os.environ.get('GEMINI_API_KEY_OVERRIDE')
            or os.environ.get('GEMINI_API_KEY')
        ),
    )


def _format_bytes(value):
    """Return a compact human-readable storage value."""
    size = float(max(0, value or 0))
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    for unit in units:
        if size < 1024 or unit == units[-1]:
            precision = 0 if unit == 'B' else 2
            return f'{size:.{precision}f} {unit}'
        size /= 1024


@settings_bp.route('/api/settings/storage-summary', methods=['GET'])
def api_storage_summary():
    """Summarise files stored by this application.

    It reads the same upload directory on a laptop or VPS, so the Settings UI
    does not need environment-specific storage logic.
    """
    guard = _require_admin()
    if guard:
        return guard

    uploads_root = os.path.join(current_app.static_folder, 'uploads')
    total_bytes = 0
    file_count = 0
    try:
        for root, _dirs, filenames in os.walk(uploads_root):
            for filename in filenames:
                path = os.path.join(root, filename)
                try:
                    total_bytes += os.path.getsize(path)
                    file_count += 1
                except OSError:
                    continue
    except OSError:
        pass

    return jsonify({
        'bytes': total_bytes,
        'formatted_size': _format_bytes(total_bytes),
        'file_count': file_count,
        'storage_type': 'Application uploads folder',
    })


# ── Delete password ────────────────────────────────────────────────────────────

@settings_bp.route('/api/settings/delete-password', methods=['POST'])
def api_set_delete_password():
    guard = _require_admin()
    if guard:
        return guard

    data = request.get_json(force=True, silent=True) or {}
    new_password = (data.get('new_password') or '').strip()
    current_password = (data.get('current_password') or '').strip()

    if len(new_password) < 4:
        return jsonify({'error': 'New password must be at least 4 characters.'}), 400

    # If a delete password already exists, the current one must be supplied
    # and correct before it can be changed.
    if app_settings.has_delete_password():
        if not app_settings.verify_delete_password(current_password):
            return jsonify({'error': 'Current delete password is incorrect.'}), 403

    app_settings.set_delete_password(new_password)
    ActivityLog.log(action='update', entity_type='Setting', entity_name='Delete Password',
                     performed_by=session.get('user_name', ''), details='Delete password set/changed')
    db.session.commit()
    return jsonify({'ok': True})


# ── Activity log ───────────────────────────────────────────────────────────────

@settings_bp.route('/api/settings/activity-log', methods=['GET'])
def api_activity_log():
    guard = _require_admin()
    if guard:
        return guard
    entries = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(200).all()
    return jsonify({'entries': [e.to_dict() for e in entries]})


# ── Active users / login details ──────────────────────────────────────────────

@settings_bp.route('/api/settings/users-overview', methods=['GET'])
def api_users_overview():
    guard = _require_admin()
    if guard:
        return guard
    users = User.query.order_by(User.username.asc()).all()
    return jsonify({'users': [u.to_dict() for u in users]})


# ── Project Management (all modules, one place) ────────────────────────────────

@settings_bp.route('/api/settings/all-projects', methods=['GET'])
def api_all_projects():
    guard = _require_admin()
    if guard:
        return guard

    rows = []
    for p in Project.query.order_by(Project.created_at.desc()).all():
        rows.append({
            'id': p.id,
            'module': p.module,
            'name': p.name,
            'detail': f'{p.state}, {p.country}' if p.state or p.country else (p.email or '—'),
            'created_at': p.created_at.strftime('%d %b %Y') if p.created_at else '—',
            'delete_url': f'/api/projects/{p.id}',
            'open_url': None,  # generic projects route to their module listing, not a single page
        })
    rows.sort(key=lambda r: r['created_at'], reverse=True)
    return jsonify({'projects': rows})


@settings_bp.route('/api/settings/projects/<int:project_id>/divisions', methods=['GET'])
def api_settings_project_divisions(project_id):
    """Divisions for the drill-down browser in Project Management —
    project -> divisions -> lines -> towers, ending in a per-tower
    delete. Same underlying data as the map sidebar, just reached from
    Settings instead."""
    guard = _require_admin()
    if guard:
        return guard
    project = Project.query.get_or_404(project_id)
    # One aggregated line-count-per-division query instead of a separate
    # COUNT per division (was 17 queries for 15 divisions in testing;
    # this is 2 regardless of how many divisions the project has).
    line_counts = dict(
        db.session.query(Line.division_id, db.func.count(Line.id))
        .join(Division, Line.division_id == Division.id)
        .filter(Division.project_id == project_id)
        .group_by(Line.division_id).all()
    )
    rows = [{'id': d.id, 'name': d.name, 'line_count': line_counts.get(d.id, 0)} for d in project.divisions]
    return jsonify({'project_name': project.name, 'divisions': rows})


@settings_bp.route('/api/settings/divisions/<int:division_id>/lines', methods=['GET'])
def api_settings_division_lines(division_id):
    guard = _require_admin()
    if guard:
        return guard
    division = Division.query.get_or_404(division_id)
    rows = [{'id': l.id, 'name': l.name, 'tower_count': l.tower_count or 0} for l in division.lines]
    return jsonify({'division_name': division.name, 'lines': rows})


@settings_bp.route('/api/settings/lines/<int:line_id>/towers', methods=['GET'])
def api_settings_line_towers(line_id):
    """Every tower that actually has data on it (at least one uploaded
    photo) — a tower_label that only exists in the KML with nothing
    uploaded yet has nothing to delete, so it's not listed here."""
    guard = _require_admin()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    towers = {}
    for photo in TowerPhoto.query.filter_by(line_id=line_id).all():
        t = towers.setdefault(photo.tower_label, {'label': photo.tower_label, 'photo_count': 0, 'defect_count': 0})
        t['photo_count'] += 1
        t['defect_count'] += len(photo.defects)
    rows = sorted(towers.values(), key=lambda t: t['label'])
    return jsonify({'line_name': line.name, 'towers': rows})


@settings_bp.route('/api/settings/lines/<int:line_id>/towers/<path:tower_label>', methods=['DELETE'])
def api_settings_delete_tower(line_id, tower_label):
    """Deletes everything for one specific tower on this line: every
    photo (raw file + the defects/ copy if one was made), every defect
    marked on those photos, every thermal measurement on them, and the
    tower's inspection-done/zone status. Password-gated the same way
    project deletion is — this is just as irreversible."""
    guard = _require_admin()
    if guard:
        return guard
    line = Line.query.get_or_404(line_id)

    data = request.get_json(force=True, silent=True) or {}
    password = (data.get('password') or request.args.get('password') or '').strip()
    if not app_settings.verify_delete_password(password):
        return jsonify({'error': 'Incorrect delete password.'}), 403

    photos = TowerPhoto.query.filter_by(line_id=line_id, tower_label=tower_label).all()
    if not photos:
        return jsonify({'error': 'No data found for this tower.'}), 404

    deleted_photo_count = 0
    deleted_defect_count = 0
    stored_paths = set()
    for photo in photos:
        deleted_defect_count += len(photo.defects)
        stored_paths.update(filter(None, (
            photo.image_path,
            photo.thumbnail_path,
            photo.defect_copy_path,
        )))
        stored_paths.update(
            event.evidence_image_path
            for defect in photo.defects for event in defect.resolution_events
            if event.evidence_image_path
        )
        db.session.delete(photo)  # cascades to its TowerDefect + ThermalPoint rows
        deleted_photo_count += 1

    status = TowerInspectionStatus.query.filter_by(line_id=line_id, tower_label=tower_label).first()
    if status:
        db.session.delete(status)

    ActivityLog.log(action='delete_tower', entity_type='Line', entity_name=f'{line.name} — Tower {tower_label}',
                     performed_by=session.get('user_name', ''), role=session.get('role', ''),
                     details=f'Deleted {deleted_photo_count} photo(s), {deleted_defect_count} defect(s)')
    db.session.commit()
    cleanup = delete_stored_files(stored_paths)
    if cleanup['errors']:
        current_app.logger.warning(
            'File cleanup after deleting line %s tower %s was incomplete: %s',
            line_id, tower_label, cleanup['errors'],
        )
    return jsonify({'deleted_photos': deleted_photo_count, 'deleted_defects': deleted_defect_count})


# ── Dashboard summary (stats) ──────────────────────────────────────────────────

@settings_bp.route('/api/dashboard/notes', methods=['GET'])
def api_get_dashboard_notes():
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get(session.get('user_id'))
    return jsonify({'notes': (user.dashboard_notes if user else '') or ''})


@settings_bp.route('/api/dashboard/notes', methods=['PUT'])
def api_save_dashboard_notes():
    guard = _require_admin()
    if guard:
        return guard
    user = User.query.get(session.get('user_id'))
    if not user:
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json(force=True, silent=True) or {}
    user.dashboard_notes = (data.get('notes') or '')[:20000]  # sane cap, this is a scratch pad not a document store
    db.session.commit()
    return jsonify({'ok': True})


@settings_bp.route('/api/dashboard/live-load-history', methods=['GET'])
def api_dashboard_live_load_history():
    guard = _require_admin()
    if guard:
        return guard
    from load_monitor import get_snapshot_history
    return jsonify({'history': get_snapshot_history()})


@settings_bp.route('/api/dashboard/live-load', methods=['GET'])
def api_dashboard_live_load():
    guard = _require_admin()
    if guard:
        return guard
    from load_monitor import get_live_stats
    return jsonify(get_live_stats())


@settings_bp.route('/api/dashboard/summary', methods=['GET'])
def api_dashboard_summary():
    guard = _require_admin()
    if guard:
        return guard

    tline_count = Project.query.filter_by(module='Transmission Line').count()
    trans_count = Project.query.filter_by(module='TRANS').count()
    total_projects = tline_count + trans_count

    total_users = User.query.count()
    active_users = sum(1 for u in User.query.all() if u.effective_status() == 'Active')
    pending_users = User.query.filter_by(status='Pending').count()

    # Active pilots "on field" — same 30-minute freshness window used by
    # the map's own Pilots toggle (see api_list_pilot_locations), so this
    # dashboard number and what you see plotted on the map always agree.
    pilot_cutoff = datetime.utcnow() - timedelta(minutes=30)
    active_pilots = PilotLocation.query.filter(PilotLocation.updated_at >= pilot_cutoff).count()

    # Defect severity across TowerDefect (Transmission Line / TRANS) — the
    # only defect system in this project.
    severity_counts = {'Critical': 0, 'Major': 0, 'Minor': 0}
    for sev, count in (db.session.query(TowerDefect.severity, db.func.count(TowerDefect.id))
                        .group_by(TowerDefect.severity).all()):
        if sev in severity_counts:
            severity_counts[sev] += count

    # Needs Attention — things worth an admin's notice at a glance rather
    # than something they have to go looking for.
    open_critical = TowerDefect.query.filter_by(severity='Critical', resolution_status='Open').count()

    # Towers with a KML tower_count but zero photos yet, across every line
    # in every TRANS-family project — same "coverage" idea already used
    # per-line in the map sidebar, rolled up to one dashboard number.
    # One aggregated query for every line's photographed-tower count,
    # instead of a separate COUNT per line — the previous version issued
    # one extra query per line (21 queries for just 20 lines in testing),
    # which only gets worse as more lines accumulate over time.
    photographed_by_line = dict(
        db.session.query(TowerPhoto.line_id, db.func.count(db.func.distinct(TowerPhoto.tower_label)))
        .group_by(TowerPhoto.line_id).all()
    )
    all_lines = Line.query.all()
    towers_pending = 0
    for line in all_lines:
        if not line.tower_count:
            continue
        photographed = photographed_by_line.get(line.id, 0)
        towers_pending += max(0, line.tower_count - photographed)

    last_24h = datetime.utcnow() - timedelta(hours=24)
    recent = (ActivityLog.query.filter(ActivityLog.created_at >= last_24h)
              .order_by(ActivityLog.created_at.desc()).limit(60).all())

    # Activity trend, last 7 days — how much is actually happening in the
    # portal day to day. This is genuinely dashboard-level information;
    # nothing per-project shows a cross-module activity trend like this.
    today = date.today()
    day_labels = [(today - timedelta(days=i)) for i in range(6, -1, -1)]
    day_counts = {d.isoformat(): 0 for d in day_labels}
    week_start = datetime.combine(day_labels[0], datetime.min.time())
    week_rows = (ActivityLog.query
                 .filter(ActivityLog.created_at >= week_start)
                 .with_entities(ActivityLog.created_at).all())
    for (created_at,) in week_rows:
        key = created_at.date().isoformat()
        if key in day_counts:
            day_counts[key] += 1
    activity_trend = [{'label': d.strftime('%a'), 'count': day_counts[d.isoformat()]} for d in day_labels]

    # Operational project progress follows the real workflow:
    # Admin uploads -> SME reviews -> SME marks Inspection Done -> Client sees.
    # There is deliberately no Admin-approval stage and Pilot uploads are not
    # counted because Admin owns all application uploads.
    project_progress = []
    for project in Project.query.order_by(Project.created_at.desc()).all():
        line_ids = [
            row[0] for row in
            db.session.query(Line.id)
            .join(Division, Line.division_id == Division.id)
            .filter(Division.project_id == project.id).all()
        ]
        lines = Line.query.filter(Line.id.in_(line_ids)).all() if line_ids else []
        total_towers = sum(max(0, int(line.tower_count or 0)) for line in lines)

        photographed_rows = []
        inspected_rows = []
        photos_uploaded = 0
        open_defects = 0
        closed_defects = 0
        if line_ids:
            photographed_rows = (
                db.session.query(TowerPhoto.line_id, TowerPhoto.tower_label)
                .filter(TowerPhoto.line_id.in_(line_ids)).distinct().all()
            )
            inspected_rows = (
                db.session.query(TowerInspectionStatus.line_id, TowerInspectionStatus.tower_label)
                .filter(
                    TowerInspectionStatus.line_id.in_(line_ids),
                    TowerInspectionStatus.inspection_done.is_(True),
                ).distinct().all()
            )
            photos_uploaded = TowerPhoto.query.filter(TowerPhoto.line_id.in_(line_ids)).count()
            defect_counts = dict(
                db.session.query(TowerDefect.resolution_status, db.func.count(TowerDefect.id))
                .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
                .filter(TowerPhoto.line_id.in_(line_ids))
                .group_by(TowerDefect.resolution_status).all()
            )
            open_defects = int(defect_counts.get('Open', 0) or 0)
            closed_defects = int(defect_counts.get('Closed', 0) or 0)

        photographed = len(photographed_rows)
        inspected = len(inspected_rows)
        review_pending = max(0, photographed - inspected)
        not_captured = max(0, total_towers - photographed)
        completion_pct = round(inspected / total_towers * 100) if total_towers else 0
        project_progress.append({
            'id': project.id,
            'name': project.name,
            'module': project.module,
            'total_towers': total_towers,
            'not_captured': not_captured,
            'admin_uploaded_towers': photographed,
            'photos_uploaded': photos_uploaded,
            'sme_review_pending': review_pending,
            'inspection_done': inspected,
            'client_visible': inspected,
            'open_defects': open_defects,
            'closed_defects': closed_defects,
            'completion_pct': completion_pct,
            'open_url': f'/projects/{project.id}/divisions',
        })

    return jsonify({
        'projects': {
            'total':              total_projects,
            'transmission_line':  tline_count,
            'trans':               trans_count,
        },
        'users': {
            'total':   total_users,
            'active':  active_users,
            'pending': pending_users,
        },
        'active_pilots': active_pilots,
        'severity_counts': severity_counts,
        'needs_attention': {
            'open_critical_defects': open_critical,
            'towers_pending_photos': towers_pending,
        },
        'recent_activity': [e.to_dict() for e in recent],
        'activity_trend': activity_trend,
        'project_progress': project_progress,
    })
