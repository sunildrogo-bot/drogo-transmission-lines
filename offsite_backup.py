"""Streaming, verified offsite checkpoints for the database and upload store."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime, timedelta

from flask import current_app

from models import ActivityLog, db

CHECKPOINT_PATTERN = re.compile(r'^\d{8}_\d{6}_[0-9a-f]{8}$')


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().casefold() in {'1', 'true', 'yes', 'on'}


def _prefix() -> str:
    value = os.environ.get('BACKUP_OFFSITE_PREFIX', 'drogo-backups').strip().strip('/')
    if not value or '..' in value.split('/'):
        raise RuntimeError('BACKUP_OFFSITE_PREFIX must be a safe non-empty object prefix.')
    return value


def configuration() -> dict:
    bucket = os.environ.get('BACKUP_OFFSITE_BUCKET', '').strip()
    enabled = _flag('BACKUP_OFFSITE_ENABLED')
    try:
        retention = max(1, min(int(os.environ.get('BACKUP_OFFSITE_RETENTION_COUNT', '3')), 100))
    except ValueError:
        retention = 3
    try:
        interval_hours = max(0, min(float(os.environ.get('BACKUP_OFFSITE_INTERVAL_HOURS', '24')), 24 * 365))
    except ValueError:
        interval_hours = 24
    return {
        'enabled': enabled,
        'configured': enabled and bool(bucket),
        'bucket': bucket,
        'prefix': _prefix(),
        'region': os.environ.get('BACKUP_OFFSITE_REGION', '').strip(),
        'endpoint_configured': bool(os.environ.get('BACKUP_OFFSITE_ENDPOINT_URL', '').strip()),
        'retention_count': retention,
        'interval_hours': interval_hours,
        'verify_every_file': _flag('BACKUP_OFFSITE_VERIFY_EVERY_FILE', True),
    }


def enqueue_if_due() -> bool:
    """Queue the configured recurring checkpoint; called periodically by the worker."""
    config = configuration()
    if not config['configured'] or config['interval_hours'] <= 0:
        return False
    from background_jobs import enqueue
    from models import BackgroundJob

    active = BackgroundJob.query.filter(
        BackgroundJob.job_type == 'offsite_backup',
        BackgroundJob.status.in_(['Queued', 'Processing']),
    ).first()
    if active:
        return False
    latest = (BackgroundJob.query.filter_by(job_type='offsite_backup')
              .order_by(BackgroundJob.created_at.desc()).first())
    due_at = (latest.created_at + timedelta(hours=config['interval_hours'])) if latest else None
    if due_at and _utcnow() < due_at:
        return False
    enqueue('offsite_backup', {'scheduled': True}, None, 'System scheduler')
    return True


def _client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError('boto3 is required for offsite backups.') from exc
    return boto3.client(
        's3',
        endpoint_url=os.environ.get('BACKUP_OFFSITE_ENDPOINT_URL') or None,
        region_name=os.environ.get('BACKUP_OFFSITE_REGION') or None,
    )


def _extra_args() -> dict:
    encryption = os.environ.get('BACKUP_OFFSITE_SSE', 'AES256').strip()
    return {'ServerSideEncryption': encryption} if encryption else {}


def _object_key(checkpoint_id: str, relative: str) -> str:
    if not CHECKPOINT_PATTERN.fullmatch(checkpoint_id):
        raise ValueError('Unsafe checkpoint identifier.')
    clean = str(relative or '').replace('\\', '/').lstrip('/')
    if not clean or '..' in clean.split('/'):
        raise ValueError('Unsafe checkpoint object path.')
    return f'{_prefix()}/checkpoints/{checkpoint_id}/{clean}'


def _upload_file(client, bucket: str, source: str, key: str, expected_size: int,
                 verify_size: bool = True) -> None:
    extra = _extra_args()
    kwargs = {'ExtraArgs': extra} if extra else {}
    client.upload_file(source, bucket, key, **kwargs)
    if verify_size:
        uploaded_size = int(client.head_object(Bucket=bucket, Key=key)['ContentLength'])
        if uploaded_size != int(expected_size):
            raise RuntimeError(
                f'Offsite size verification failed for {key}: {uploaded_size} != {expected_size}.')


def _snapshot_database(destination: str) -> tuple[str, str]:
    from backup_routes import (
        _postgres_backup_available,
        _snapshot_postgresql,
        _snapshot_sqlite,
    )

    backend = db.engine.url.get_backend_name()
    if backend == 'sqlite':
        source = os.path.abspath(db.engine.url.database or '')
        if not source or not os.path.isfile(source):
            raise RuntimeError('The SQLite database file could not be found.')
        _snapshot_sqlite(source, destination)
        return backend, os.path.basename(source) or 'application.sqlite'
    if backend.startswith('postgresql'):
        if not _postgres_backup_available():
            raise RuntimeError('pg_dump is required for a PostgreSQL offsite backup.')
        _snapshot_postgresql(destination)
        return backend, 'application_postgresql.dump'
    raise RuntimeError(f'Offsite database snapshots are not supported for {backend}.')


def _verify_database_restore(client, bucket: str, database_key: str, backend: str) -> None:
    descriptor, path = tempfile.mkstemp(prefix='.drogo_offsite_verify_', dir=current_app.instance_path)
    os.close(descriptor)
    try:
        client.download_file(bucket, database_key, path)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise RuntimeError('Downloaded offsite database verification copy is empty.')
        if backend == 'sqlite':
            with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as connection:
                result = connection.execute('PRAGMA quick_check').fetchone()[0]
            if result != 'ok':
                raise RuntimeError(f'Offsite SQLite restore verification failed: {result}')
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _checkpoint_prefix(checkpoint_id: str) -> str:
    return f'{_prefix()}/checkpoints/{checkpoint_id}/'


def _delete_prefix(client, bucket: str, prefix: str) -> int:
    expected_root = f'{_prefix()}/checkpoints/'
    checkpoint_id = prefix[len(expected_root):].strip('/') if prefix.startswith(expected_root) else ''
    if not CHECKPOINT_PATTERN.fullmatch(checkpoint_id):
        raise RuntimeError(f'Refusing to prune unsafe offsite prefix: {prefix}')
    removed = 0
    token = None
    while True:
        kwargs = {'Bucket': bucket, 'Prefix': prefix, 'MaxKeys': 1000}
        if token:
            kwargs['ContinuationToken'] = token
        page = client.list_objects_v2(**kwargs)
        objects = [{'Key': row['Key']} for row in page.get('Contents', [])]
        if objects:
            client.delete_objects(Bucket=bucket, Delete={'Objects': objects, 'Quiet': True})
            removed += len(objects)
        if not page.get('IsTruncated'):
            break
        token = page.get('NextContinuationToken')
    return removed


def prune_old_checkpoints(client, bucket: str, keep: int) -> dict:
    root = f'{_prefix()}/checkpoints/'
    page = client.list_objects_v2(Bucket=bucket, Prefix=root, Delimiter='/')
    prefixes = sorted(
        row['Prefix'] for row in page.get('CommonPrefixes', [])
        if CHECKPOINT_PATTERN.fullmatch(row['Prefix'][len(root):].strip('/'))
    )
    removed_checkpoints = removed_objects = 0
    for prefix in prefixes[:-max(1, keep)]:
        removed_objects += _delete_prefix(client, bucket, prefix)
        removed_checkpoints += 1
    return {'removed_checkpoints': removed_checkpoints, 'removed_objects': removed_objects}


def run_checkpoint(job, client=None) -> dict:
    """Create one independently restorable checkpoint without a local 47 GB archive."""
    config = configuration()
    if not config['configured']:
        raise RuntimeError('Offsite backup is not configured. Set BACKUP_OFFSITE_ENABLED and BACKUP_OFFSITE_BUCKET.')
    client = client or _client()
    bucket = config['bucket']
    checkpoint_id = _utcnow().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]

    from backup_routes import _schema_revision
    from storage_service import get_storage

    storage = get_storage()
    inventory = storage.inventory()
    job.progress_total = len(inventory) + 2
    job.progress_current = 0
    job.result_json = json.dumps({'phase': 'Snapshotting database', 'checkpoint_id': checkpoint_id})
    db.session.commit()

    backup_dir = os.path.join(current_app.instance_path, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    descriptor, snapshot_path = tempfile.mkstemp(prefix='.drogo_offsite_', suffix='.database', dir=backup_dir)
    os.close(descriptor)
    try:
        backend, database_name = _snapshot_database(snapshot_path)
        database_size = os.path.getsize(snapshot_path)
        database_key = _object_key(checkpoint_id, f'instance/{database_name}')
        _upload_file(client, bucket, snapshot_path, database_key, database_size, True)
        job.progress_current = 1
        job.result_json = json.dumps({'phase': 'Uploading application files', 'checkpoint_id': checkpoint_id})
        db.session.commit()

        files = []
        uploaded_bytes = 0
        for index, item in enumerate(inventory, 1):
            key = item['key']
            size = int(item['size'])
            remote_key = _object_key(checkpoint_id, f'static/{key}')
            with storage.materialize(key) as source:
                _upload_file(client, bucket, source, remote_key, size, config['verify_every_file'])
            files.append({'path': f'static/{key}', 'size': size})
            uploaded_bytes += size
            job.progress_current = index + 1
            job.heartbeat_at = _utcnow()
            if index % 10 == 0:
                db.session.commit()

        manifest = {
            'format_version': 2,
            'checkpoint_id': checkpoint_id,
            'created_at': _utcnow().isoformat() + 'Z',
            'created_by': job.created_by_name or 'Admin',
            'database': {
                'backend': backend,
                'path': f'instance/{database_name}',
                'size': database_size,
                'migration_revision': _schema_revision(),
            },
            'uploads': {'count': len(files), 'bytes': uploaded_bytes, 'files': files},
            'secrets_included': False,
        }
        manifest_body = json.dumps(manifest, indent=2).encode('utf-8')
        manifest_key = _object_key(checkpoint_id, 'backup_manifest.json')
        put_args = {'Bucket': bucket, 'Key': manifest_key, 'Body': manifest_body,
                    'ContentType': 'application/json'}
        put_args.update(_extra_args())
        client.put_object(**put_args)

        job.result_json = json.dumps({'phase': 'Verifying restore checkpoint', 'checkpoint_id': checkpoint_id})
        db.session.commit()
        _verify_database_restore(client, bucket, database_key, backend)
        manifest_size = int(client.head_object(Bucket=bucket, Key=manifest_key)['ContentLength'])
        if manifest_size != len(manifest_body):
            raise RuntimeError('Offsite manifest verification failed.')

        retention = prune_old_checkpoints(client, bucket, config['retention_count'])
        result = {
            'phase': 'Completed and restore-verified',
            'checkpoint_id': checkpoint_id,
            'bucket': bucket,
            'prefix': _checkpoint_prefix(checkpoint_id),
            'upload_count': len(files),
            'upload_bytes': uploaded_bytes,
            'database_bytes': database_size,
            'migration_revision': manifest['database']['migration_revision'],
            'restore_verified': True,
            'retention': retention,
        }
        job.progress_current = job.progress_total
        ActivityLog.log(
            action='create_offsite_backup', entity_type='OffsiteBackup',
            entity_name=checkpoint_id, performed_by=job.created_by_name or 'Admin', role='Admin',
            details=f"{len(files)} uploads; {uploaded_bytes} bytes; restore verified",
        )
        db.session.commit()
        return result
    finally:
        try:
            os.remove(snapshot_path)
        except OSError:
            pass
