"""Database-shared portal load tracking for multi-worker Gunicorn deployments."""
import json
import os
import socket
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from flask import has_app_context

_lock = threading.Lock()
_request_log = deque(maxlen=3000)
_active_users = {}
_pending = {}
_last_flush = 0.0
ACTIVE_WINDOW_SECONDS = 180
RETENTION_SECONDS = 10 * 3600
FLUSH_INTERVAL_SECONDS = 10


def _source_id():
    return f'{socket.gethostname()}:{os.getpid()}'


def _minute_start(timestamp=None):
    return datetime.utcfromtimestamp(timestamp or time.time()).replace(second=0, microsecond=0)


def record_request(duration_ms, user_id=None, count_toward_load=True):
    global _last_flush
    now = time.time(); bucket = _minute_start(now)
    with _lock:
        if count_toward_load:
            _request_log.append((now, float(duration_ms)))
        if user_id is not None:
            _active_users[str(user_id)] = now
        item = _pending.setdefault(bucket, {'count': 0, 'total': 0.0, 'max': 0.0, 'users': set()})
        if count_toward_load:
            item['count'] += 1; item['total'] += float(duration_ms)
            item['max'] = max(item['max'], float(duration_ms))
        if user_id is not None:
            item['users'].add(str(user_id))
        should_flush = has_app_context() and now - _last_flush >= FLUSH_INTERVAL_SECONDS
    if should_flush:
        flush_metrics()


def flush_metrics():
    global _last_flush
    if not has_app_context():
        return False
    from models import RequestMetricBucket, db
    with _lock:
        pending = dict(_pending); _pending.clear(); _last_flush = time.time()
    try:
        source = _source_id()
        for bucket_start, values in pending.items():
            row = RequestMetricBucket.query.filter_by(bucket_start=bucket_start, source_id=source).first()
            if not row:
                row = RequestMetricBucket(bucket_start=bucket_start, source_id=source)
                db.session.add(row)
            row.request_count = (row.request_count or 0) + values['count']
            row.total_response_ms = (row.total_response_ms or 0) + values['total']
            row.max_response_ms = max(row.max_response_ms or 0, values['max'])
            try: users = set(json.loads(row.active_users_json or '[]'))
            except (TypeError, ValueError): users = set()
            users.update(values['users']); row.active_users_json = json.dumps(sorted(users))
            row.updated_at = datetime.utcnow()
        cutoff = datetime.utcnow() - timedelta(seconds=RETENTION_SECONDS)
        RequestMetricBucket.query.filter(RequestMetricBucket.bucket_start < cutoff).delete(synchronize_session=False)
        db.session.commit(); return True
    except Exception:
        db.session.rollback()
        with _lock:
            for bucket_start, values in pending.items():
                item = _pending.setdefault(bucket_start, {'count': 0, 'total': 0.0, 'max': 0.0, 'users': set()})
                item['count'] += values['count']; item['total'] += values['total']
                item['max'] = max(item['max'], values['max']); item['users'].update(values['users'])
        return False


def _rows():
    if not has_app_context(): return []
    from models import RequestMetricBucket
    cutoff = datetime.utcnow() - timedelta(seconds=RETENTION_SECONDS)
    try:
        return RequestMetricBucket.query.filter(RequestMetricBucket.bucket_start >= cutoff).order_by(
            RequestMetricBucket.bucket_start.asc()).all()
    except Exception:
        return []


def _combined(rows):
    result = {}
    for row in rows:
        item = result.setdefault(row.bucket_start, {'count': 0, 'total': 0.0, 'max': 0.0, 'users': set()})
        item['count'] += row.request_count or 0; item['total'] += row.total_response_ms or 0
        item['max'] = max(item['max'], row.max_response_ms or 0)
        try: item['users'].update(json.loads(row.active_users_json or '[]'))
        except (TypeError, ValueError): pass
    return result


def _level(count, average):
    return 'high' if average > 800 or count > 120 else ('moderate' if average > 300 or count > 40 else 'low')


def get_live_stats():
    flush_metrics(); rows = _rows(); now = time.time()
    if rows:
        cutoff = datetime.utcnow().replace(second=0, microsecond=0) - timedelta(minutes=1)
        items = [value for stamp, value in _combined(rows).items() if stamp >= cutoff]
        count = sum(item['count'] for item in items); total = sum(item['total'] for item in items)
        users = set().union(*(item['users'] for item in items)) if items else set()
        average = round(total / count, 1) if count else 0.0
        return {'active_users_now': len(users), 'requests_per_minute': count,
                'avg_response_ms': average, 'max_response_ms': round(max((i['max'] for i in items), default=0), 1),
                'level': _level(count, average), 'timestamp': now, 'scope': 'all_web_workers'}
    with _lock:
        recent = [duration for stamp, duration in _request_log if now - stamp <= 60]
        users = [stamp for stamp in _active_users.values() if now - stamp <= ACTIVE_WINDOW_SECONDS]
    average = round(sum(recent) / len(recent), 1) if recent else 0.0
    return {'active_users_now': len(users), 'requests_per_minute': len(recent),
            'avg_response_ms': average, 'max_response_ms': round(max(recent), 1) if recent else 0.0,
            'level': _level(len(recent), average), 'timestamp': now, 'scope': 'current_process_fallback'}


def get_snapshot_history(max_points=400):
    flush_metrics(); combined = _combined(_rows()); history = []
    for stamp, item in combined.items():
        count = item['count']; average = round(item['total'] / count, 1) if count else 0.0
        history.append({'timestamp': stamp.timestamp(), 'active_users_now': len(item['users']),
                        'requests_per_minute': count, 'avg_response_ms': average,
                        'max_response_ms': round(item['max'], 1), 'level': _level(count, average)})
    if len(history) <= max_points: return history
    stride = len(history) / max_points
    return [history[int(index * stride)] for index in range(max_points)]
