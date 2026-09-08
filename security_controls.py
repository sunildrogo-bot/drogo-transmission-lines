"""Authentication throttling, one-time tokens, and trusted public URLs."""
import hashlib
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, request, url_for

from models import AuthRateLimit, db


LOGIN_ACCOUNT_LIMIT = 5
LOGIN_IP_LIMIT = 20
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_BLOCK_SECONDS = 15 * 60

RESET_ACCOUNT_LIMIT = 3
RESET_IP_LIMIT = 10
RESET_WINDOW_SECONDS = 60 * 60
RESET_BLOCK_SECONDS = 60 * 60


def one_time_token():
    """Return a high-entropy URL token and the SHA-256 digest stored in DB."""
    raw = secrets.token_urlsafe(32)
    return raw, token_digest(raw)


def token_digest(raw_token):
    return hashlib.sha256((raw_token or '').encode('utf-8')).hexdigest()


def public_url(endpoint, **values):
    """Build an external URL without trusting the incoming Host header."""
    configured = (current_app.config.get('PUBLIC_BASE_URL') or '').strip()
    parsed = urlsplit(configured)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        # Safe local default. Public deployments must set PUBLIC_BASE_URL.
        parsed = urlsplit('http://127.0.0.1:5000')
    base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', ''))
    return f"{base}{url_for(endpoint, _external=False, **values)}"


def _request_ip():
    # Do not trust X-Forwarded-For unless the deployment explicitly installs
    # ProxyFix for a known reverse proxy. A client can forge that header.
    return request.remote_addr or 'unknown'


def _key_hash(scope, identity):
    secret = str(current_app.secret_key or '')
    normalized = (identity or '').strip().casefold()
    return hashlib.sha256(f'{secret}|{scope}|{normalized}'.encode('utf-8')).hexdigest()


def _check(scope, identity, window_seconds):
    if not identity:
        return False, 0
    row = AuthRateLimit.query.filter_by(
        scope=scope,
        key_hash=_key_hash(scope, identity),
    ).first()
    if not row:
        return False, 0
    now = datetime.utcnow()
    if row.blocked_until and row.blocked_until > now:
        return True, max(1, int((row.blocked_until - now).total_seconds()))
    if row.window_started_at + timedelta(seconds=window_seconds) <= now:
        return False, 0
    return False, 0


def _record(scope, identity, limit, window_seconds, block_seconds):
    if not identity:
        return False, 0
    now = datetime.utcnow()
    key_hash = _key_hash(scope, identity)
    row = AuthRateLimit.query.filter_by(scope=scope, key_hash=key_hash).first()
    if not row:
        row = AuthRateLimit(
            scope=scope,
            key_hash=key_hash,
            attempts=0,
            window_started_at=now,
            updated_at=now,
        )
        db.session.add(row)
    elif row.window_started_at + timedelta(seconds=window_seconds) <= now:
        row.attempts = 0
        row.window_started_at = now
        row.blocked_until = None

    row.attempts = int(row.attempts or 0) + 1
    row.updated_at = now
    if row.attempts >= limit:
        row.blocked_until = now + timedelta(seconds=block_seconds)

    # Keep the table bounded without retaining identifiers indefinitely.
    cutoff = now - timedelta(days=7)
    AuthRateLimit.query.filter(AuthRateLimit.updated_at < cutoff).delete(synchronize_session=False)
    db.session.commit()

    if row.blocked_until and row.blocked_until > now:
        return True, max(1, int((row.blocked_until - now).total_seconds()))
    return False, 0


def _clear(scope, identity):
    if not identity:
        return
    AuthRateLimit.query.filter_by(
        scope=scope,
        key_hash=_key_hash(scope, identity),
    ).delete(synchronize_session=False)
    db.session.commit()


def login_limit(email):
    checks = [
        _check('login_account', email, LOGIN_WINDOW_SECONDS),
        _check('login_ip', _request_ip(), LOGIN_WINDOW_SECONDS),
    ]
    retry = max((seconds for limited, seconds in checks if limited), default=0)
    return retry > 0, retry


def record_login_failure(email):
    results = [
        _record('login_account', email, LOGIN_ACCOUNT_LIMIT, LOGIN_WINDOW_SECONDS, LOGIN_BLOCK_SECONDS),
        _record('login_ip', _request_ip(), LOGIN_IP_LIMIT, LOGIN_WINDOW_SECONDS, LOGIN_BLOCK_SECONDS),
    ]
    retry = max((seconds for limited, seconds in results if limited), default=0)
    return retry > 0, retry


def clear_login_failures(email):
    _clear('login_account', email)


def consume_password_reset(email):
    """Return False when the account or IP reset-request budget is exhausted."""
    checks = [
        _check('reset_account', email, RESET_WINDOW_SECONDS),
        _check('reset_ip', _request_ip(), RESET_WINDOW_SECONDS),
    ]
    if any(limited for limited, _ in checks):
        return False
    _record('reset_account', email, RESET_ACCOUNT_LIMIT, RESET_WINDOW_SECONDS, RESET_BLOCK_SECONDS)
    _record('reset_ip', _request_ip(), RESET_IP_LIMIT, RESET_WINDOW_SECONDS, RESET_BLOCK_SECONDS)
    return True
