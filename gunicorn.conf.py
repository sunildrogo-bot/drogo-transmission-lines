"""Production Gunicorn defaults; settings may be overridden by environment."""
import os

bind = os.environ.get('GUNICORN_BIND', '0.0.0.0:8000')
workers = max(1, int(os.environ.get('WEB_CONCURRENCY', '2')))
threads = max(1, int(os.environ.get('WEB_THREADS', '4')))
worker_class = 'gthread'
timeout = max(30, int(os.environ.get('WEB_TIMEOUT_SECONDS', '300')))
graceful_timeout = max(15, int(os.environ.get('WEB_GRACEFUL_TIMEOUT_SECONDS', '30')))
keepalive = max(1, int(os.environ.get('WEB_KEEPALIVE_SECONDS', '5')))
accesslog = '-'
errorlog = '-'
capture_output = True
preload_app = False
