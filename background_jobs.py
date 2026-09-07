"""Database-backed background jobs used by the production worker."""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from flask import current_app
from PIL import Image, ImageOps

from models import BackgroundJob, TowerPhoto, db


def enqueue(job_type, payload=None, user_id=None, user_name=''):
    job = BackgroundJob(
        job_type=job_type, payload_json=json.dumps(payload or {}),
        created_by_user_id=user_id, created_by_name=user_name or '', status='Queued')
    db.session.add(job)
    db.session.commit()
    return job


def recover_stale_jobs(minutes=10):
    cutoff = datetime.utcnow() - timedelta(minutes=minutes)
    stale = (BackgroundJob.query.filter(BackgroundJob.status == 'Processing')
             .filter(BackgroundJob.heartbeat_at < cutoff).all())
    for job in stale:
        if (job.attempts or 0) < (job.max_attempts or 3):
            job.status = 'Queued'
            job.error_message = 'Recovered after an interrupted worker.'
        else:
            job.status = 'Failed'
            job.finished_at = datetime.utcnow()
    if stale:
        db.session.commit()
    return len(stale)


def claim_next_job():
    # PostgreSQL uses SKIP LOCKED so several workers cannot claim the same
    # job. SQLite ignores the concurrency benefit but remains usable locally.
    query = (BackgroundJob.query.filter_by(status='Queued')
             .order_by(BackgroundJob.created_at.asc()))
    try:
        job = query.with_for_update(skip_locked=True).first()
    except Exception:
        db.session.rollback()
        job = query.first()
    if not job:
        return None
    job.status = 'Processing'
    job.started_at = job.started_at or datetime.utcnow()
    job.heartbeat_at = datetime.utcnow()
    job.attempts = (job.attempts or 0) + 1
    db.session.commit()
    return job


def _normalise_static_path(value):
    path = (value or '').replace('\\', '/').lstrip('/')
    return path[len('static/'):] if path.startswith('static/') else path


def thumbnail_exists(photo):
    from storage_service import get_storage
    rel = _normalise_static_path(photo.thumbnail_path)
    if not rel:
        return False
    return get_storage().exists(rel)


def _repair_thumbnail(photo):
    from storage_service import get_storage
    storage = get_storage()
    source_rel = _normalise_static_path(photo.display_image_path())
    if not source_rel:
        return False, 'Original image is unavailable.'
    if not storage.exists(source_rel):
        return False, 'Original image was not found in active storage.'
    source_key = PurePosixPath(source_rel)
    parent = source_key.parent
    thumb_parent = parent.parent / 'thumb' if parent.name in {'raw', 'defects'} else parent.parent / f'{parent.name}_thumb'
    thumb_key = (thumb_parent / (source_key.name + '.thumb.jpg')).as_posix()
    static_root = os.path.abspath(current_app.static_folder)
    destination = os.path.abspath(os.path.join(static_root, *PurePosixPath(thumb_key).parts))
    if os.path.commonpath([static_root, destination]) != static_root:
        return False, 'Thumbnail destination is unsafe.'
    thumb_dir = os.path.dirname(destination)
    os.makedirs(thumb_dir, exist_ok=True)
    temporary = destination + '.tmp'
    try:
        with storage.materialize(source_rel) as source:
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image).convert('RGB')
                image.thumbnail((800, 800), Image.Resampling.LANCZOS)
                image.save(temporary, 'JPEG', quality=90, optimize=True)
        os.replace(temporary, destination)
        storage.publish(thumb_key, destination)
        photo.thumbnail_path = thumb_key
        return True, ''
    except Exception as exc:
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
        return False, str(exc)[:300]


def _run_thumbnail_repair(job):
    payload = job.payload()
    query = TowerPhoto.query
    if payload.get('line_id'):
        query = query.filter(TowerPhoto.line_id == int(payload['line_id']))
    rows = [photo for photo in query.order_by(TowerPhoto.id).all() if not thumbnail_exists(photo)]
    job.progress_total = len(rows)
    db.session.commit()
    repaired, failed, failures = 0, 0, []
    for index, photo in enumerate(rows, 1):
        ok, error = _repair_thumbnail(photo)
        if ok:
            repaired += 1
        else:
            failed += 1
            if len(failures) < 100:
                failures.append({'photo_id': photo.id, 'error': error})
        job.progress_current = index
        job.heartbeat_at = datetime.utcnow()
        if index % 10 == 0:
            db.session.commit()
    job.result_json = json.dumps({'repaired': repaired, 'failed': failed, 'failures': failures})


def _run_application_backup(job):
    payload = job.payload()
    legacy_job_id = payload.get('job_id')
    if not legacy_job_id:
        raise ValueError('Backup job identifier is missing.')
    from backup_routes import _read_job, _run_backup
    _run_backup(current_app._get_current_object(), legacy_job_id,
                payload.get('actor') or job.created_by_name or 'Admin',
                bool(payload.get('pre_upgrade')))
    result = _read_job(legacy_job_id)
    if not result or result.get('status') != 'Completed':
        raise RuntimeError((result or {}).get('error') or 'Application backup failed.')
    job.progress_current = 100
    job.progress_total = 100
    job.result_json = json.dumps({'job_id': legacy_job_id, 'file_name': result.get('file_name', '')})


def _run_training_export(job):
    payload = job.payload()
    legacy_job_id = payload.get('job_id')
    selection = payload.get('selection')
    if not legacy_job_id or not isinstance(selection, dict):
        raise ValueError('Training export payload is incomplete.')
    from training_export_routes import _read_job, _run_export
    _run_export(current_app._get_current_object(), legacy_job_id, selection,
                payload.get('actor') or job.created_by_name or 'Admin')
    result = _read_job(legacy_job_id)
    if not result or result.get('status') != 'Completed':
        raise RuntimeError((result or {}).get('error') or 'Training dataset export failed.')
    job.progress_current = 100
    job.progress_total = 100
    job.result_json = json.dumps({'job_id': legacy_job_id, 'file_name': result.get('file_name', '')})


def _run_media_metadata_repair(job):
    from storage_service import get_storage
    storage = get_storage()
    payload = job.payload()
    query = TowerPhoto.query
    if payload.get('line_id'):
        query = query.filter(TowerPhoto.line_id == int(payload['line_id']))
    rows = query.order_by(TowerPhoto.id).all()
    job.progress_total = len(rows)
    db.session.commit()
    updated, missing, failed = 0, 0, 0
    for index, photo in enumerate(rows, 1):
        source_key = _normalise_static_path(photo.display_image_path())
        if not source_key or not storage.exists(source_key):
            photo.validation_status = 'Missing'
            photo.validation_warnings_json = json.dumps(['Original image is unavailable in active storage.'])
            missing += 1
        else:
            try:
                with storage.materialize(source_key) as source:
                    with Image.open(source) as image:
                        image = ImageOps.exif_transpose(image)
                        photo.image_width, photo.image_height = image.size
                        exif = image.getexif()
                        captured_raw = exif.get(36867) or exif.get(306)
                if captured_raw:
                    try:
                        photo.captured_at = datetime.strptime(str(captured_raw), '%Y:%m:%d %H:%M:%S')
                    except (TypeError, ValueError):
                        pass
                stem = os.path.splitext(PurePosixPath(source_key).name)[0]
                photo.media_type = 'thermal' if re.search(r'_T(_\S+)?$', stem, re.IGNORECASE) else 'rgb'
                warnings = []
                if not photo.captured_at:
                    warnings.append('Capture date/time metadata is missing.')
                if (photo.image_width or 0) < 640 or (photo.image_height or 0) < 480:
                    warnings.append(f'Low resolution ({photo.image_width}×{photo.image_height}).')
                if photo.media_type == 'thermal':
                    warnings.append('Thermal classification should be confirmed from radiometric measurement data.')
                photo.validation_status = 'Warning' if warnings else 'Ready'
                photo.validation_warnings_json = json.dumps(warnings)
                updated += 1
            except Exception:
                photo.validation_status = 'Invalid'
                photo.validation_warnings_json = json.dumps(['Image could not be decoded safely.'])
                failed += 1
        job.progress_current = index
        job.heartbeat_at = datetime.utcnow()
        if index % 20 == 0:
            db.session.commit()
    job.result_json = json.dumps({'updated': updated, 'missing': missing, 'failed': failed})


def _run_storage_migration(job):
    """Copy every database-tracked local upload into configured object storage."""
    from backup_routes import _tracked_upload_paths
    from storage_service import get_storage
    storage = get_storage()
    tracked = sorted(_tracked_upload_paths())
    job.progress_total = len(tracked); db.session.commit()
    if storage.mode == 'local':
        job.progress_current = len(tracked)
        job.result_json = json.dumps({'backend': 'local', 'copied': 0, 'already_present': len(tracked), 'missing_local': 0})
        return
    static_root = os.path.realpath(current_app.static_folder)
    copied = present = missing = 0
    failures = []
    for index, key in enumerate(tracked, 1):
        if storage.exists(key):
            present += 1
        else:
            local_path = os.path.realpath(os.path.join(static_root, *PurePosixPath(key).parts))
            if os.path.commonpath([static_root, local_path]) != static_root or not os.path.isfile(local_path):
                missing += 1
                if len(failures) < 100: failures.append({'key': key, 'error': 'Local source is missing.'})
            else:
                try:
                    storage.publish(key, local_path); copied += 1
                except Exception as exc:
                    missing += 1
                    if len(failures) < 100: failures.append({'key': key, 'error': str(exc)[:240]})
        job.progress_current = index; job.heartbeat_at = datetime.utcnow()
        if index % 10 == 0: db.session.commit()
    job.result_json = json.dumps({'backend': storage.mode, 'copied': copied,
                                  'already_present': present, 'missing_local': missing,
                                  'failures': failures})


HANDLERS = {
    'thumbnail_repair': _run_thumbnail_repair,
    'application_backup': _run_application_backup,
    'training_export': _run_training_export,
    'media_metadata_repair': _run_media_metadata_repair,
    'storage_migration': _run_storage_migration,
}


def run_one():
    recover_stale_jobs()
    job = claim_next_job()
    if not job:
        return False
    try:
        handler = HANDLERS.get(job.job_type)
        if not handler:
            raise RuntimeError(f'Unsupported job type: {job.job_type}')
        handler(job)
        job.status = 'Completed'
        job.finished_at = datetime.utcnow()
        job.heartbeat_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        job = db.session.get(BackgroundJob, job.id)
        job.error_message = str(exc)[:2000]
        job.heartbeat_at = datetime.utcnow()
        if (job.attempts or 0) < (job.max_attempts or 3):
            job.status = 'Queued'
        else:
            job.status = 'Failed'
            job.finished_at = datetime.utcnow()
        db.session.commit()
    return True
