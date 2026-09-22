"""Runtime safeguards shared by the web process, worker and migrations."""
from __future__ import annotations

import os
import sqlite3
from urllib.parse import urlparse

from sqlalchemy import event
from sqlalchemy.engine import Engine

DEFAULT_DEVELOPMENT_SECRET = 'nova-plus-dev-secret-CHANGE-IN-PROD'
PRODUCTION_ENVIRONMENTS = {'prod', 'production'}
_sqlite_listener_installed = False


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {'1', 'true', 'yes', 'on'}


def is_production(environment: str | None) -> bool:
    return str(environment or '').strip().casefold() in PRODUCTION_ENVIRONMENTS


def _sqlite_on_connect(dbapi_connection, _connection_record) -> None:
    """Enforce integrity and predictable write contention on every SQLite connection."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    try:
        timeout_ms = int(os.environ.get('SQLITE_BUSY_TIMEOUT_MS', '30000'))
    except ValueError:
        timeout_ms = 30000
    timeout_ms = max(1000, min(timeout_ms, 300000))
    journal_mode = os.environ.get('SQLITE_JOURNAL_MODE', 'WAL').strip().upper()
    if journal_mode not in {'DELETE', 'TRUNCATE', 'PERSIST', 'MEMORY', 'WAL', 'OFF'}:
        journal_mode = 'WAL'

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute(f'PRAGMA busy_timeout={timeout_ms}')
        cursor.execute(f'PRAGMA journal_mode={journal_mode}')
        if journal_mode == 'WAL':
            cursor.execute('PRAGMA synchronous=NORMAL')
    finally:
        cursor.close()


def install_sqlite_pragmas() -> None:
    """Register the SQLAlchemy connection hook once per Python process."""
    global _sqlite_listener_installed
    if _sqlite_listener_installed:
        return
    event.listen(Engine, 'connect', _sqlite_on_connect)
    _sqlite_listener_installed = True


def validate_runtime_config(app) -> None:
    """Refuse an unsafe production boot instead of silently using dev defaults."""
    if not is_production(app.config.get('DEPLOYMENT_ENV')):
        return

    errors = []
    secret = str(app.secret_key or '')
    if not secret or secret == DEFAULT_DEVELOPMENT_SECRET or len(secret) < 32:
        errors.append('SECRET_KEY must be a unique random value of at least 32 characters')

    public_url = str(app.config.get('PUBLIC_BASE_URL') or '').strip()
    parsed = urlparse(public_url)
    if parsed.scheme != 'https' or not parsed.netloc:
        errors.append('PUBLIC_BASE_URL must be the public https:// portal origin')

    if not app.config.get('SESSION_COOKIE_SECURE'):
        errors.append('SESSION_COOKIE_SECURE must be true')

    if errors:
        detail = '; '.join(errors)
        raise RuntimeError(f'Unsafe production configuration: {detail}.')
