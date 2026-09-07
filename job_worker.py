"""Run persistent application jobs: python job_worker.py"""
import argparse
import atexit
import json
import os
import socket
import time
from datetime import datetime

from app import create_app
from background_jobs import run_one
from models import AppSetting, ServiceHeartbeat, db

_worker_started = datetime.utcnow()
_worker_id = f'{socket.gethostname()}:{os.getpid()}'
_lease_path = None


def _pid_is_running(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


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
        if _pid_is_running(existing.get('pid')):
            raise RuntimeError(f"Another SQLite background worker is already running (PID {existing.get('pid')}).")
        os.remove(_lease_path)
        descriptor = os.open(_lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump({'pid': os.getpid(), 'worker_id': _worker_id, 'started_at': _worker_started.isoformat() + 'Z'}, handle)
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
    row.value = datetime.utcnow().isoformat() + 'Z'
    service = ServiceHeartbeat.query.filter_by(service_id=_worker_id).first()
    if not service:
        service = ServiceHeartbeat(service_id=_worker_id, service_type='worker', hostname=socket.gethostname(),
                                   process_id=os.getpid(), started_at=_worker_started)
        db.session.add(service)
    service.status = 'Running'
    service.last_seen = datetime.utcnow()
    db.session.commit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true', help='Process at most one job and exit.')
    parser.add_argument('--poll-seconds', type=float, default=2.0)
    args = parser.parse_args()
    app = create_app()
    with app.app_context():
        acquire_sqlite_lease(app)
        while True:
            record_worker_heartbeat()
            worked = run_one()
            if args.once:
                break
            if not worked:
                time.sleep(max(0.25, args.poll_seconds))


if __name__ == '__main__':
    main()
