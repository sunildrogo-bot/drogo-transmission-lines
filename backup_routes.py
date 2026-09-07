"""Admin-only data health checks and recoverable application backups."""
import hashlib
import csv
import io
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import uuid
import zipfile
from datetime import datetime

from flask import Blueprint, current_app, jsonify, request, send_file, session, Response
from sqlalchemy import text

from models import (
    ActivityLog, Announcement, CorridorPhoto, DefectResolutionEvent, Division,
    BackgroundJob, Line, Project, TowerDefect, TowerPhoto, TowerReport, User, db,
)


backup_bp = Blueprint('backup_bp', __name__)
_backup_lock = threading.Lock()
_process_id = uuid.uuid4().hex


def _admin_guard():
    if 'user_id' not in session:
        return jsonify({'error': 'Please sign in.'}), 401
    if session.get('role') != 'Admin':
        return jsonify({'error': 'Admin access required.'}), 403
    return None


def _backup_root(app=None):
    app = app or current_app
    path = os.path.join(app.instance_path, 'backups')
    os.makedirs(path, exist_ok=True)
    return path


def _job_path(job_id, app=None):
    return os.path.join(_backup_root(app), f'{job_id}.json')


def _read_job(job_id, app=None):
    try:
        with open(_job_path(job_id, app), encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _write_job(job, app=None):
    path = _job_path(job['id'], app)
    temporary = f'{path}.{uuid.uuid4().hex}.tmp'
    with _backup_lock:
        with open(temporary, 'w', encoding='utf-8') as handle:
            json.dump(job, handle, indent=2)
        os.replace(temporary, path)
    background_id = job.get('background_job_id')
    if background_id:
        background = db.session.get(BackgroundJob, background_id)
        if background:
            background.progress_current = max(0, min(100, int(job.get('progress') or 0)))
            background.progress_total = 100
            background.heartbeat_at = datetime.utcnow()
            db.session.commit()


def _recover_interrupted_job(job, app=None):
    """A non-terminal job from another process cannot still be running."""
    if job and job.get('background_job_id'):
        background = db.session.get(BackgroundJob, job['background_job_id'])
        if background and background.status in {'Queued', 'Processing'}:
            return job
        if background and background.status == 'Failed' and job.get('status') not in {'Completed', 'Failed'}:
            job.update(status='Failed', finished_at=datetime.utcnow().isoformat() + 'Z',
                       error=background.error_message or 'The persistent worker could not complete this backup.')
            _write_job(job, app)
            return job
    if (job and job.get('status') not in {'Completed', 'Failed'}
            and job.get('process_id') != _process_id):
        job.update(status='Failed', finished_at=datetime.utcnow().isoformat() + 'Z',
                   error='Backup was interrupted because the application stopped or restarted.')
        _write_job(job, app)
    return job


def _format_bytes(value):
    size = float(max(0, value or 0))
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if size < 1024 or unit == 'TB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.2f} {unit}'
        size /= 1024


def _normalise_stored_path(value):
    path = (value or '').replace('\\', '/').lstrip('/')
    return path[len('static/'):] if path.startswith('static/') else path


def _tracked_upload_paths():
    tracked = set()
    fields = [
        (Project, ('logo_path', 'legacy_banner')),
        (Line, ('kml_path',)),
        (CorridorPhoto, ('image_path', 'thumbnail_path')),
        (TowerPhoto, ('image_path', 'thumbnail_path', 'defect_copy_path')),
        (TowerReport, ('report_path',)),
        (DefectResolutionEvent, ('evidence_image_path',)),
        (Announcement, ('image_path',)),
        (User, ('photo_path',)),
    ]
    for model, names in fields:
        for row in model.query.all():
            for name in names:
                value = _normalise_stored_path(getattr(row, name, ''))
                if value.startswith('uploads/'):
                    tracked.add(value)
    return tracked


def _tracked_upload_references():
    fields = [
        (Project, ('logo_path', 'legacy_banner')), (Line, ('kml_path',)),
        (CorridorPhoto, ('image_path', 'thumbnail_path')),
        (TowerPhoto, ('image_path', 'thumbnail_path', 'defect_copy_path')),
        (TowerReport, ('report_path',)), (DefectResolutionEvent, ('evidence_image_path',)),
        (Announcement, ('image_path',)), (User, ('photo_path',)),
    ]
    for model, names in fields:
        for row in model.query.all():
            for field in names:
                key = _normalise_stored_path(getattr(row, field, ''))
                if key.startswith('uploads/'):
                    yield model.__tablename__, row, field, key


def _file_category(key):
    parts = (key or '').split('/')
    return parts[1] if len(parts) > 2 else 'other'


def _upload_inventory(app):
    from storage_service import get_storage
    storage = get_storage()
    files = [(None, item['key'], item['size']) for item in storage.inventory()]
    total_bytes = sum(item[2] for item in files)
    return files, total_bytes


def _schema_revision():
    try:
        value = db.session.execute(text('SELECT version_num FROM alembic_version')).scalar()
        return value or 'unknown'
    except Exception:
        db.session.rollback()
        return 'not-versioned'


def _sqlite_database_path():
    if db.engine.url.get_backend_name() != 'sqlite':
        return ''
    database = db.engine.url.database or ''
    return os.path.abspath(database) if database else ''


def _postgres_backup_available():
    return db.engine.url.get_backend_name().startswith('postgresql') and bool(shutil.which('pg_dump'))


def _snapshot_postgresql(destination):
    """Create a custom-format dump without placing the password in argv."""
    executable = shutil.which('pg_dump')
    if not executable:
        raise ValueError('pg_dump is not installed or is not available in PATH.')
    url = db.engine.url
    command = [executable, '--format=custom', '--no-owner', '--no-privileges',
               '--file', destination]
    if url.host:
        command.extend(['--host', url.host])
    if url.port:
        command.extend(['--port', str(url.port)])
    if url.username:
        command.extend(['--username', url.username])
    if url.database:
        command.extend(['--dbname', url.database])
    environment = os.environ.copy()
    if url.password:
        environment['PGPASSWORD'] = url.password
    sslmode = url.query.get('sslmode') if url.query else None
    if sslmode:
        environment['PGSSLMODE'] = str(sslmode)
    completed = subprocess.run(command, env=environment, capture_output=True, text=True,
                               timeout=60 * 60, check=False)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or 'pg_dump failed').strip()
        raise ValueError(f'PostgreSQL backup failed: {message[:800]}')
    if not os.path.isfile(destination) or os.path.getsize(destination) == 0:
        raise ValueError('pg_dump completed without creating a usable backup file.')


def _health_payload(app):
    upload_files, upload_bytes = _upload_inventory(app)
    actual = {relative for _path, relative, _size in upload_files}
    tracked = _tracked_upload_paths()
    missing = sorted(tracked - actual)
    untracked = sorted(actual - tracked)
    missing_by_category = {}
    for key in missing:
        category = _file_category(key)
        missing_by_category[category] = missing_by_category.get(category, 0) + 1
    untracked_by_name = {}
    for key in untracked:
        untracked_by_name.setdefault(os.path.basename(key).casefold(), []).append(key)
    relink_suggestions = []
    for old_key in missing:
        matches = untracked_by_name.get(os.path.basename(old_key).casefold(), [])
        if len(matches) == 1:
            relink_suggestions.append({'missing': old_key, 'candidate': matches[0],
                                       'category': _file_category(old_key)})
    missing_thumbnails = sum(
        1 for photo in TowerPhoto.query.filter(TowerPhoto.thumbnail_path.isnot(None)).all()
        if _normalise_stored_path(photo.thumbnail_path) not in actual
    )
    database_path = _sqlite_database_path()
    database_ok = bool(database_path and os.path.isfile(database_path)) if database_path else True
    disk = shutil.disk_usage(app.instance_path)
    issues = []
    if missing:
        issues.append(f'{len(missing)} database-linked file(s) are missing.')
    if missing_thumbnails:
        issues.append(f'{missing_thumbnails} thumbnail file(s) are missing and can be regenerated.')
    if disk.free < max(1024 ** 3, upload_bytes * .1):
        issues.append('Available disk space is low for the current data size.')
    if not database_ok:
        issues.append('The SQLite database file could not be found.')
    backend = db.engine.url.get_backend_name()
    snapshot_supported = backend == 'sqlite' or _postgres_backup_available()
    if backend.startswith('postgresql') and not snapshot_supported:
        issues.append('PostgreSQL is configured but pg_dump is unavailable on this server.')
    return {
        'status': 'Attention required' if issues else 'Healthy',
        'checked_at': datetime.utcnow().isoformat() + 'Z',
        'database': {
            'backend': backend,
            'snapshot_supported': snapshot_supported,
            'available': database_ok,
            'migration_revision': _schema_revision(),
        },
        'records': {
            'users': User.query.count(), 'projects': Project.query.count(),
            'divisions': Division.query.count(), 'lines': Line.query.count(),
            'photos': TowerPhoto.query.count(),
            'active_defects': TowerDefect.query.filter(TowerDefect.deleted_at.is_(None)).count(),
        },
        'files': {
            'upload_count': len(upload_files), 'upload_bytes': upload_bytes,
            'upload_size': _format_bytes(upload_bytes), 'tracked_count': len(tracked),
            'missing_count': len(missing), 'missing_examples': missing[:50],
            'missing_by_category': missing_by_category,
            'untracked_count': len(untracked), 'untracked_examples': untracked[:50],
            'missing_thumbnail_count': missing_thumbnails,
            'relink_suggestions': relink_suggestions[:100],
        },
        'disk': {'free_bytes': disk.free, 'free': _format_bytes(disk.free)},
        'issues': issues,
    }


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_sqlite(source, destination):
    source_db = sqlite3.connect(source)
    destination_db = sqlite3.connect(destination)
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
        source_db.close()


def _run_backup(app, job_id, actor, pre_upgrade):
    with app.app_context():
        job = _read_job(job_id, app)
        snapshot_path = os.path.join(_backup_root(app), f'{job_id}.database.tmp')
        zip_path = ''
        try:
            job.update(status='Checking data', progress=2)
            _write_job(job, app)
            health = _health_payload(app)
            backend = db.engine.url.get_backend_name()
            database_path = _sqlite_database_path()
            if backend == 'sqlite' and (not database_path or not os.path.isfile(database_path)):
                raise ValueError('The local SQLite database file could not be found.')
            if backend != 'sqlite' and not backend.startswith('postgresql'):
                raise ValueError(f'Automatic snapshots are not supported for {backend}.')
            upload_files, upload_bytes = _upload_inventory(app)
            database_bytes = os.path.getsize(database_path) if database_path else 0
            estimated = upload_bytes + database_bytes
            free = shutil.disk_usage(_backup_root(app)).free
            if estimated > free * .92:
                raise ValueError(f'Not enough free disk space. Approximately {_format_bytes(estimated)} is required.')

            job.update(status='Snapshotting database', progress=5)
            _write_job(job, app)
            if backend == 'sqlite':
                _snapshot_sqlite(database_path, snapshot_path)
                database_name = os.path.basename(database_path) or 'application.sqlite'
            else:
                _snapshot_postgresql(snapshot_path)
                database_name = 'application_postgresql.dump'
            timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
            kind = 'pre_upgrade' if pre_upgrade else 'manual'
            filename = f'drogo_{kind}_backup_{timestamp}_{job_id[:8]}.zip'
            zip_path = os.path.join(_backup_root(app), filename)
            manifest = {
                'format_version': 1, 'created_at': datetime.utcnow().isoformat() + 'Z',
                'created_by': actor, 'backup_type': kind,
                'database_backend': backend, 'migration_revision': health['database']['migration_revision'],
                'database_file': f'instance/{database_name}',
                'database_sha256': _sha256(snapshot_path),
                'upload_count': len(upload_files), 'upload_bytes': upload_bytes,
                'health_at_backup': health,
                'secrets_included': False,
                'integrity': 'Every ZIP entry includes a CRC32 value; test the archive before restoring.',
            }
            config_files = []
            for name in ('training_class_map.json',):
                path = os.path.join(app.instance_path, name)
                if os.path.isfile(path):
                    config_files.append((path, f'instance/{name}'))
            total_items = max(1, len(upload_files) + len(config_files) + 1)
            job.update(status='Writing backup', progress=10)
            _write_job(job, app)
            with zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                archive.write(snapshot_path, f'instance/{database_name}')
                completed = 1
                from storage_service import get_storage
                storage = get_storage()
                for _path, relative, _size in upload_files:
                    with storage.materialize(relative) as materialized:
                        archive.write(materialized, f'static/{relative}')
                    completed += 1
                    if completed % 25 == 0:
                        job['progress'] = 10 + int(completed / total_items * 82)
                        _write_job(job, app)
                for path, archive_name in config_files:
                    archive.write(path, archive_name)
                archive.writestr('backup_manifest.json', json.dumps(manifest, indent=2))
                archive.writestr('RESTORE_README.txt',
                    'DROGO application data backup.\n\n'
                    'Stop the application before restoring. Extract static/uploads into the new application.\n'
                    f'Database backend: {backend}. Database file: instance/{database_name}.\n'
                    'For PostgreSQL, restore the custom dump with pg_restore. For SQLite, restore the database file.\n'
                    'python -m flask --app app db upgrade\n\n'
                    'The .env file is intentionally not included. Copy it separately from a trusted location.\n')
            with zipfile.ZipFile(zip_path, 'r') as archive:
                corrupt_entry = archive.testzip()
            if corrupt_entry:
                raise ValueError(f'Backup integrity verification failed at {corrupt_entry}.')
            job.update(status='Completed', progress=100, finished_at=datetime.utcnow().isoformat() + 'Z',
                       file_name=filename, download_name=filename, file_size=os.path.getsize(zip_path),
                       file_size_label=_format_bytes(os.path.getsize(zip_path)),
                       summary={'upload_count': len(upload_files), 'upload_size': _format_bytes(upload_bytes),
                                'migration_revision': health['database']['migration_revision'],
                                'health_status': health['status']}, error='')
            ActivityLog.log(action='create_backup', entity_type='ApplicationBackup', entity_name=filename,
                            performed_by=actor, role='Admin',
                            details=f'{kind}; {len(upload_files)} uploads; {_format_bytes(upload_bytes)}')
            db.session.commit()
        except Exception as exc:
            app.logger.exception('Application backup %s failed', job_id)
            if zip_path and os.path.isfile(zip_path):
                os.remove(zip_path)
            job.update(status='Failed', finished_at=datetime.utcnow().isoformat() + 'Z', error=str(exc))
        finally:
            if os.path.isfile(snapshot_path):
                os.remove(snapshot_path)
        _write_job(job, app)


@backup_bp.route('/api/settings/data-health')
def data_health():
    guard = _admin_guard()
    if guard:
        return guard
    return jsonify(_health_payload(current_app))


@backup_bp.route('/api/settings/data-health/issues.csv')
def data_health_issues_csv():
    guard = _admin_guard()
    if guard:
        return guard
    payload = _health_payload(current_app)
    upload_files, _upload_bytes = _upload_inventory(current_app)
    actual = {relative for _path, relative, _size in upload_files}
    tracked = _tracked_upload_paths()
    missing_all = sorted(tracked - actual)
    untracked_all = sorted(actual - tracked)
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(['issue_type', 'category', 'stored_path', 'suggested_candidate'])
    suggestions = {row['missing']: row['candidate'] for row in payload['files']['relink_suggestions']}
    for key in missing_all:
        writer.writerow(['missing_database_linked_file', _file_category(key), key, suggestions.get(key, '')])
    for key in untracked_all:
        writer.writerow(['untracked_stored_file', _file_category(key), key, ''])
    writer.writerow(['missing_thumbnails', 'thumbnails', payload['files']['missing_thumbnail_count'], 'Regenerate in Settings'])
    return Response(output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': f'attachment; filename=data_health_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.csv'})


@backup_bp.route('/api/settings/data-health/relink', methods=['POST'])
def data_health_relink():
    """Apply one conservative relink where the exact basename has one candidate."""
    guard = _admin_guard()
    if guard:
        return guard
    payload = request.get_json(silent=True) or {}
    try:
        from storage_service import normalize_key
        missing_key = normalize_key(payload.get('missing'))
        candidate = normalize_key(payload.get('candidate'))
    except ValueError:
        return jsonify({'error': 'Both paths must be safe upload paths.'}), 400
    health = _health_payload(current_app)
    suggestion = next((row for row in health['files']['relink_suggestions']
                       if row['missing'] == missing_key and row['candidate'] == candidate), None)
    if not suggestion:
        return jsonify({'error': 'This is no longer a unique safe relink suggestion. Run Data Health again.'}), 409
    changed = []
    for table, row, field, key in _tracked_upload_references():
        if key != missing_key:
            continue
        setattr(row, field, candidate); changed.append({'table': table, 'id': row.id, 'field': field})
    if not changed:
        return jsonify({'error': 'No current database reference uses the missing path.'}), 409
    ActivityLog.log(action='relink', entity_type='StoredFile', entity_name=os.path.basename(candidate),
                    performed_by=session.get('user_name', ''), role=session.get('role', ''),
                    details=f'{missing_key} -> {candidate}; {len(changed)} reference(s)')
    db.session.commit()
    return jsonify({'ok': True, 'changed': changed, 'missing': missing_key, 'candidate': candidate})


@backup_bp.route('/api/settings/backups', methods=['POST'])
def create_backup():
    guard = _admin_guard()
    if guard:
        return guard
    for name in os.listdir(_backup_root()):
        if name.endswith('.json'):
            existing = _recover_interrupted_job(_read_job(name[:-5]))
            if existing and existing.get('status') not in {'Completed', 'Failed'}:
                return jsonify({'error': 'Another backup is already running.'}), 409
    payload = request.get_json(silent=True) or {}
    job_id = uuid.uuid4().hex
    pre_upgrade = bool(payload.get('pre_upgrade'))
    actor = session.get('user_name', 'Admin')
    from background_jobs import enqueue
    background = enqueue('application_backup', {
        'job_id': job_id, 'actor': actor, 'pre_upgrade': pre_upgrade,
    }, session.get('user_id'), actor)
    job = {'id': job_id, 'status': 'Queued', 'progress': 0,
           'created_at': datetime.utcnow().isoformat() + 'Z',
           'created_by': actor,
           'backup_type': 'Pre-upgrade' if pre_upgrade else 'Manual',
           'process_id': _process_id, 'background_job_id': background.id, 'error': ''}
    _write_job(job)
    return jsonify(job), 202


@backup_bp.route('/api/settings/backups')
def list_backups():
    guard = _admin_guard()
    if guard:
        return guard
    jobs = []
    for name in os.listdir(_backup_root()):
        if name.endswith('.json'):
            job = _recover_interrupted_job(_read_job(name[:-5]))
            if job:
                jobs.append(job)
    jobs.sort(key=lambda item: item.get('created_at', ''), reverse=True)
    return jsonify({'backups': jobs[:50]})


@backup_bp.route('/api/settings/backups/<job_id>')
def backup_status(job_id):
    guard = _admin_guard()
    if guard:
        return guard
    job = _read_job(job_id)
    return jsonify(job) if job else (jsonify({'error': 'Backup not found.'}), 404)


@backup_bp.route('/api/settings/backups/<job_id>/download')
def download_backup(job_id):
    guard = _admin_guard()
    if guard:
        return guard
    job = _read_job(job_id)
    if not job or job.get('status') != 'Completed' or not job.get('file_name'):
        return jsonify({'error': 'Backup is not ready.'}), 404
    root = os.path.abspath(_backup_root())
    path = os.path.abspath(os.path.join(root, job['file_name']))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        return jsonify({'error': 'Backup file is missing.'}), 404
    return send_file(path, as_attachment=True, download_name=job.get('download_name') or 'drogo_backup.zip')


@backup_bp.route('/api/settings/backups/<job_id>', methods=['DELETE'])
def delete_backup(job_id):
    guard = _admin_guard()
    if guard:
        return guard
    job = _read_job(job_id)
    if not job:
        return jsonify({'error': 'Backup not found.'}), 404
    if job.get('status') not in {'Completed', 'Failed'}:
        return jsonify({'error': 'Wait for the backup to finish before deleting it.'}), 409
    root = os.path.abspath(_backup_root())
    for name in (job.get('file_name'), f'{job_id}.json'):
        if not name:
            continue
        path = os.path.abspath(os.path.join(root, name))
        if os.path.commonpath([root, path]) == root and os.path.isfile(path):
            os.remove(path)
    if job.get('background_job_id'):
        background = db.session.get(BackgroundJob, job['background_job_id'])
        if background:
            db.session.delete(background)
            db.session.commit()
    return jsonify({'deleted': True})
