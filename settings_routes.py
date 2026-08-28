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
from sqlalchemy import or_

import settings as app_settings
from models import db, ActivityLog, User, Project, TowerDefect, TowerPhoto, Line, PilotLocation, Division, TowerInspectionStatus, InspectionComponent, InspectionDefectType
import json
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


@settings_bp.route('/inspection-quality')
def inspection_quality_page():
    """Admin's tower-level inspection and evidence quality workspace."""
    guard = _require_admin()
    if guard:
        return guard
    return render_template(
        'inspection_quality.html',
        user_name=session['user_name'],
        can_switch_client='Client User' in session.get('all_roles', []),
    )


def _quality_file_exists(stored_path):
    """Resolve the relative paths stored by old and current app versions."""
    value = (stored_path or '').strip().replace('\\', '/')
    if not value:
        return False
    if os.path.isabs(value):
        return os.path.isfile(value)
    value = value.lstrip('/')
    if value.startswith('static/'):
        candidate = os.path.join(current_app.root_path, *value.split('/'))
    else:
        candidate = os.path.join(current_app.static_folder, *value.split('/'))
    return os.path.isfile(candidate)


@settings_bp.route('/api/inspection-quality', methods=['GET'])
def api_inspection_quality():
    """Tower-level completion, defects and evidence health.

    There is deliberately no image-review metric. Inspection Done is the
    authoritative SME decision and the Client-release gate.
    """
    guard = _require_admin()
    if guard:
        return guard

    lines = Line.query.order_by(Line.name.asc()).all()
    line_ids = [line.id for line in lines]

    done_by_line = {line_id: set() for line_id in line_ids}
    photo_labels_by_line = {line_id: set() for line_id in line_ids}
    photo_count_by_line = {line_id: 0 for line_id in line_ids}
    rgb_count_by_line = {line_id: 0 for line_id in line_ids}
    thermal_count_by_line = {line_id: 0 for line_id in line_ids}
    missing_files_by_line = {line_id: 0 for line_id in line_ids}
    missing_thumbs_by_line = {line_id: 0 for line_id in line_ids}

    if line_ids:
        for row in TowerInspectionStatus.query.filter(
            TowerInspectionStatus.line_id.in_(line_ids),
            TowerInspectionStatus.inspection_done.is_(True),
        ).all():
            done_by_line.setdefault(row.line_id, set()).add(row.tower_label)

        for photo in TowerPhoto.query.filter(TowerPhoto.line_id.in_(line_ids)).all():
            photo_labels_by_line.setdefault(photo.line_id, set()).add(photo.tower_label)
            photo_count_by_line[photo.line_id] = photo_count_by_line.get(photo.line_id, 0) + 1
            if photo.is_thermal_image():
                thermal_count_by_line[photo.line_id] = thermal_count_by_line.get(photo.line_id, 0) + 1
            else:
                rgb_count_by_line[photo.line_id] = rgb_count_by_line.get(photo.line_id, 0) + 1
            if not _quality_file_exists(photo.display_image_path()):
                missing_files_by_line[photo.line_id] = missing_files_by_line.get(photo.line_id, 0) + 1
            if not _quality_file_exists(photo.thumbnail_path):
                missing_thumbs_by_line[photo.line_id] = missing_thumbs_by_line.get(photo.line_id, 0) + 1

    open_by_line = {}
    critical_by_line = {}
    taxonomy_by_line = {}
    if line_ids:
        open_by_line = dict(
            db.session.query(TowerPhoto.line_id, db.func.count(TowerDefect.id))
            .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
            .filter(
                TowerPhoto.line_id.in_(line_ids),
                TowerDefect.deleted_at.is_(None),
                TowerDefect.resolution_status == 'Open',
            ).group_by(TowerPhoto.line_id).all()
        )
        critical_by_line = dict(
            db.session.query(TowerPhoto.line_id, db.func.count(TowerDefect.id))
            .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
            .filter(
                TowerPhoto.line_id.in_(line_ids),
                TowerDefect.deleted_at.is_(None),
                TowerDefect.resolution_status == 'Open',
                TowerDefect.severity == 'Critical',
            ).group_by(TowerPhoto.line_id).all()
        )
        taxonomy_by_line = dict(
            db.session.query(TowerPhoto.line_id, db.func.count(TowerDefect.id))
            .join(TowerDefect, TowerDefect.tower_photo_id == TowerPhoto.id)
            .filter(
                TowerPhoto.line_id.in_(line_ids),
                TowerDefect.deleted_at.is_(None),
                or_(
                    db.func.trim(db.func.coalesce(TowerDefect.component_name, '')) == '',
                    db.func.trim(db.func.coalesce(TowerDefect.defect_type, '')) == '',
                ),
            ).group_by(TowerPhoto.line_id).all()
        )

    rows = []
    project_options = {}
    totals = {
        'towers': 0, 'inspection_done': 0, 'inspection_not_done': 0,
        'open_defects': 0, 'critical_defects': 0, 'missing_files': 0,
        'missing_thumbnails': 0, 'taxonomy_issues': 0,
        'rgb_images': 0, 'thermal_images': 0,
    }
    for line in lines:
        project = line.division.project if line.division and line.division.project else None
        project_id = project.id if project else 0
        project_name = project.name if project else 'Unassigned project'
        project_options[project_id] = project_name
        known_labels = photo_labels_by_line.get(line.id, set()) | done_by_line.get(line.id, set())
        configured_total = max(0, int(line.tower_count or 0))
        tower_total = max(configured_total, len(known_labels))
        done = len(done_by_line.get(line.id, set()))
        not_done = max(0, tower_total - done)
        row = {
            'line_id': line.id,
            'line_name': line.name,
            'division_name': line.division.name if line.division else '',
            'project_id': project_id,
            'project_name': project_name,
            'total_towers': tower_total,
            'inspection_done': done,
            'client_visible': done,
            'inspection_not_done': not_done,
            'photos': photo_count_by_line.get(line.id, 0),
            'rgb_images': rgb_count_by_line.get(line.id, 0),
            'thermal_images': thermal_count_by_line.get(line.id, 0),
            'open_defects': int(open_by_line.get(line.id, 0) or 0),
            'critical_defects': int(critical_by_line.get(line.id, 0) or 0),
            'missing_files': missing_files_by_line.get(line.id, 0),
            'missing_thumbnails': missing_thumbs_by_line.get(line.id, 0),
            'taxonomy_issues': int(taxonomy_by_line.get(line.id, 0) or 0),
            'open_url': f'/projects/{project_id}/map?line={line.id}' if project_id else '',
        }
        row['attention_score'] = (
            row['critical_defects'] * 1000 + row['missing_files'] * 100
            + row['inspection_not_done'] * 10 + row['taxonomy_issues']
        )
        rows.append(row)
        for key in totals:
            if key == 'towers':
                totals[key] += tower_total
            elif key in row:
                totals[key] += row[key]

    rows.sort(key=lambda item: (-item['attention_score'], (item['project_name'] or '').lower(), (item['line_name'] or '').lower()))
    return jsonify({
        'summary': totals,
        'projects': [{'id': key, 'name': value} for key, value in sorted(project_options.items(), key=lambda item: item[1].lower())],
        'lines': rows,
        'workflow': {
            'image_review_required': False,
            'client_release_rule': 'Inspection Done',
        },
    })


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


@settings_bp.route('/api/settings/inspection-taxonomy', methods=['GET'])
def api_get_inspection_taxonomy():
    guard = _require_admin()
    if guard:
        return guard
    components = (InspectionComponent.query
                  .order_by(InspectionComponent.display_order, InspectionComponent.name).all())
    observed = (db.session.query(TowerDefect.component_name, TowerDefect.defect_type, db.func.count(TowerDefect.id))
                .filter(TowerDefect.deleted_at.is_(None))
                .group_by(TowerDefect.component_name, TowerDefect.defect_type).all())
    return jsonify({
        'components': [row.to_dict() for row in components],
        'observed': [{'component': component or 'Unspecified', 'defect_type': defect_type or 'Unspecified', 'count': count}
                     for component, defect_type, count in observed],
    })


@settings_bp.route('/api/settings/inspection-taxonomy', methods=['POST'])
def api_save_inspection_taxonomy():
    guard = _require_admin()
    if guard:
        return guard
    payload = request.get_json(force=True, silent=True) or {}
    rows = payload.get('components')
    if not isinstance(rows, list):
        return jsonify({'error': 'components must be a list.'}), 400
    seen_components = set()
    for index, item in enumerate(rows):
        if not isinstance(item, dict):
            return jsonify({'error': f'Component row {index + 1} must be an object.'}), 400
        raw_name = item.get('name')
        if raw_name is not None and not isinstance(raw_name, str):
            return jsonify({'error': f'Component row {index + 1} has an invalid name.'}), 400
        name = (raw_name or '').strip()[:150]
        if not name:
            return jsonify({'error': f'Component row {index + 1} needs a name.'}), 400
        key = name.casefold()
        if key in seen_components:
            return jsonify({'error': f'Duplicate component: {name}'}), 400
        seen_components.add(key)
        component_id = item.get('id')
        if component_id is not None:
            try:
                component_id = int(component_id)
            except (TypeError, ValueError):
                return jsonify({'error': f'Component row {index + 1} has an invalid id.'}), 400
        component = db.session.get(InspectionComponent, component_id) if component_id else None
        if not component:
            component = InspectionComponent.query.filter(db.func.lower(InspectionComponent.name) == name.lower()).first()
        if not component:
            component = InspectionComponent(name=name)
            db.session.add(component)
            db.session.flush()
        component.name = name
        component.description = str(item.get('description') or '').strip()[:255]
        component.active = item.get('active') is not False
        component.display_order = index
        component.supports_rgb = item.get('supports_rgb') is not False
        component.supports_thermal = bool(item.get('supports_thermal'))
        component.severity_required = item.get('severity_required') is not False

        type_rows = item.get('defect_types') or []
        if not isinstance(type_rows, list):
            return jsonify({'error': f'Defect types under {name} must be a list.'}), 400
        seen_types = set()
        for type_index, type_item in enumerate(type_rows):
            if not isinstance(type_item, dict):
                return jsonify({'error': f'Defect type row {type_index + 1} under {name} must be an object.'}), 400
            raw_type_name = type_item.get('name')
            if raw_type_name is not None and not isinstance(raw_type_name, str):
                return jsonify({'error': f'Defect type row {type_index + 1} under {name} has an invalid name.'}), 400
            type_name = (raw_type_name or '').strip()[:150]
            if not type_name:
                continue
            type_key = type_name.casefold()
            if type_key in seen_types:
                return jsonify({'error': f'Duplicate defect type under {name}: {type_name}'}), 400
            seen_types.add(type_key)
            defect_type_id = type_item.get('id')
            if defect_type_id is not None:
                try:
                    defect_type_id = int(defect_type_id)
                except (TypeError, ValueError):
                    return jsonify({'error': f'Defect type row {type_index + 1} under {name} has an invalid id.'}), 400
            defect_type = db.session.get(InspectionDefectType, defect_type_id) if defect_type_id else None
            if not defect_type or defect_type.component_id != component.id:
                defect_type = InspectionDefectType.query.filter_by(component_id=component.id, name=type_name).first()
            if not defect_type:
                defect_type = InspectionDefectType(component=component, name=type_name)
                db.session.add(defect_type)
            defect_type.name = type_name
            defect_type.report_name = str(type_item.get('report_name') or type_name).strip()[:150]
            defect_type.training_class = str(type_item.get('training_class') or type_name).strip()[:150]
            aliases = type_item.get('aliases') or []
            severities = type_item.get('severities') or []
            if not isinstance(aliases, list) or not isinstance(severities, list):
                return jsonify({'error': f'Aliases and severities under {name} must be lists.'}), 400
            defect_type.aliases_json = json.dumps([str(value).strip()[:150] for value in aliases if str(value).strip()])
            allowed = [value for value in severities if value in {'Minor', 'Major', 'Critical'}]
            defect_type.severities_json = json.dumps(allowed or ['Minor', 'Major', 'Critical'])
            annotation_method = str(type_item.get('annotation_method') or 'box').strip().lower()
            if annotation_method not in {'box', 'polygon', 'circle'}:
                return jsonify({'error': f'Unsupported annotation method under {name}.'}), 400
            defect_type.annotation_method = annotation_method
            defect_type.active = type_item.get('active') is not False
            defect_type.display_order = type_index

    ActivityLog.log(action='update_taxonomy', entity_type='Settings', entity_name='Components & Defect Types',
                    performed_by=session.get('user_name', ''), role=session.get('role', ''))
    db.session.commit()
    return jsonify({'saved': True})


@settings_bp.route('/api/inspection-taxonomy/active', methods=['GET'])
def api_active_inspection_taxonomy():
    guard = _login_guard()
    if guard:
        return guard
    rows = (InspectionComponent.query.filter_by(active=True)
            .order_by(InspectionComponent.display_order, InspectionComponent.name).all())
    data = []
    for component in rows:
        item = component.to_dict()
        item['defect_types'] = [defect.to_dict() for defect in component.defect_types if defect.active]
        data.append(item)
    return jsonify({'components': data})


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
                        .filter(TowerDefect.deleted_at.is_(None))
                        .group_by(TowerDefect.severity).all()):
        if sev in severity_counts:
            severity_counts[sev] += count

    # Needs Attention — things worth an admin's notice at a glance rather
    # than something they have to go looking for.
    open_critical = (TowerDefect.query.filter_by(severity='Critical', resolution_status='Open')
                     .filter(TowerDefect.deleted_at.is_(None)).count())

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
    # Admin uploads -> SME inspects images -> SME marks Inspection Done -> Client sees.
    # There is deliberately no Admin-approval stage and Pilot uploads are not
    # counted because Admin owns all application uploads.
    project_progress = []
    total_inspection_not_done = 0
    total_inspection_done = 0
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
                .filter(TowerPhoto.line_id.in_(line_ids), TowerDefect.deleted_at.is_(None))
                .group_by(TowerDefect.resolution_status).all()
            )
            open_defects = int(defect_counts.get('Open', 0) or 0)
            closed_defects = int(defect_counts.get('Closed', 0) or 0)

        photographed = len(photographed_rows)
        inspected = len(inspected_rows)
        inspection_not_done_uploaded = max(0, photographed - inspected)
        not_captured = max(0, total_towers - photographed)
        total_inspection_not_done += max(0, total_towers - inspected)
        total_inspection_done += inspected
        completion_pct = round(inspected / total_towers * 100) if total_towers else 0
        project_progress.append({
            'id': project.id,
            'name': project.name,
            'module': project.module,
            'total_towers': total_towers,
            'not_captured': not_captured,
            'admin_uploaded_towers': photographed,
            'photos_uploaded': photos_uploaded,
            'inspection_not_done_uploaded': inspection_not_done_uploaded,
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
            'inspection_not_done': total_inspection_not_done,
            'inspection_done': total_inspection_done,
        },
        'recent_activity': [e.to_dict() for e in recent],
        'activity_trend': activity_trend,
        'project_progress': project_progress,
    })
