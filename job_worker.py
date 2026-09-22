"""Run persistent application jobs: python job_worker.py"""
import argparse
import atexit
import json
import os
import signal
import socket
import time
from datetime import UTC, datetime

from app import create_app
from background_jobs import run_one
from models import AppSetting, ServiceHeartbeat, db



def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


_worker_started = _utcnow()
_worker_id = f'{socket.gethostname()}:{os.getpid()}'
_lease_path = None


def _process_identity(pid):
    """Return a Linux boot/process identity so PID reuse is not mistaken for ownership."""
    try:
        with open('/proc/sys/kernel/random/boot_id', encoding='utf-8') as handle:
            boot_id = handle.read().strip()
        with open(f'/proc/{int(pid)}/stat', encoding='utf-8') as handle:
            start_ticks = handle.read().split()[21]
        return f'{boot_id}:{start_ticks}'
    except (OSError, ValueError, IndexError):
        return ''


def _pid_is_running(pid, identity=''):
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    current_identity = _process_identity(pid)
    if identity and current_identity:
        return identity == current_identity
    return True


def acquire_sqlite_lease(app):
    """SQLite supports exactly one job worker; PostgreSQL uses row locks."""
    global _lease_path
    if db.engine.url.get_backend_name() != 'sqlite':
        return
    _lease_path = os.path.join(app.instance_path, 'background_worker.lock')
    os.makedirs(app.instance_path, exist_ok=True)
    try:
        descriptor = os.open(_lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            with open(_lease_path, encoding='utf-8') as handle:
                existing = json.load(handle)
        except (OSError, ValueError, TypeError):
            existing = {}
        if _pid_is_running(existing.get('pid'), existing.get('process_identity', '')):
            raise RuntimeError(f"Another SQLite background worker is already running (PID {existing.get('pid')}).")
        os.remove(_lease_path)
        descriptor = os.open(_lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump({
            'pid': os.getpid(),
            'worker_id': _worker_id,
            'started_at': _worker_started.isoformat() + 'Z',
            'process_identity': _process_identity(os.getpid()),
        }, handle)
    atexit.register(release_sqlite_lease)


def release_sqlite_lease():
    if not _lease_path:
        return
    try:
        with open(_lease_path, encoding='utf-8') as handle:
            owner = json.load(handle)
        if int(owner.get('pid') or 0) == os.getpid():
            os.remove(_lease_path)
    except (OSError, ValueError, TypeError):
        pass


def record_worker_heartbeat():
    row = AppSetting.query.filter_by(key='background_worker_heartbeat').first()
    if not row:
        row = AppSetting(key='background_worker_heartbeat')
        db.session.add(row)
    row.value = _utcnow().isoformat() + 'Z'
    service = ServiceHeartbeat.query.filter_by(service_id=_worker_id).first()
    if not service:
        service = ServiceHeartbeat(service_id=_worker_id, service_type='worker', hostname=socket.gethostname(),
                                   process_id=os.getpid(), started_at=_worker_started)
        db.session.add(service)
    service.status = 'Running'
    service.last_seen = _utcnow()
    db.session.commit()


def record_worker_stopped():
    service = ServiceHeartbeat.query.filter_by(service_id=_worker_id).first()
    if service:
        service.status = 'Stopped'
        service.current_job_id = None
        service.last_seen = _utcnow()
        db.session.commit()


def _request_shutdown(_signum, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process at most one job and exit.')
    parser.add_argument('--poll-seconds', type=float, default=2.0)
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        acquire_sqlite_lease(app)
        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)
        try:
            last_schedule_check = 0.0
            while True:
                record_worker_heartbeat()
                now = time.monotonic()
                if now - last_schedule_check >= 60:
                    try:
                        from offsite_backup import enqueue_if_due
                        enqueue_if_due()
                    except Exception as exc:
                        print(f'[worker] offsite backup schedule check failed: {exc}', flush=True)
                    last_schedule_check = now
                worked = run_one()
                if args.once:
                    break
                if not worked:
                    time.sleep(max(0.25, args.poll_seconds))
        finally:
            record_worker_stopped()
            release_sqlite_lease()


if __name__ == '__main__':
    main()
