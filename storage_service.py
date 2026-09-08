"""Unified private storage for local files and S3-compatible object storage."""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import PurePosixPath

from flask import current_app, redirect, send_file


def normalize_key(value):
    raw = str(value or '').replace('\\', '/').lstrip('/')
    if raw.startswith('static/'):
        raw = raw[len('static/'):]
    pure = PurePosixPath(raw)
    if pure.is_absolute() or '..' in pure.parts or not raw.startswith('uploads/'):
        raise ValueError('Storage key must be a safe uploads/... path.')
    return pure.as_posix()


class LocalStorage:
    mode = 'local'

    @property
    def root(self):
        return os.path.realpath(current_app.static_folder)

    def local_path(self, key):
        key = normalize_key(key)
        candidate = os.path.realpath(os.path.join(self.root, *PurePosixPath(key).parts))
        if os.path.commonpath([self.root, candidate]) != self.root:
            raise ValueError('Unsafe storage path.')
        return candidate

    def local_working_path(self, key):
        path = self.local_path(key)
        if not os.path.isfile(path):
            raise FileNotFoundError(key)
        return path

    def exists(self, key):
        try:
            return os.path.isfile(self.local_path(key))
        except (OSError, ValueError):
            return False

    def size(self, key):
        return os.path.getsize(self.local_path(key))

    def publish(self, key, source_path):
        # Local writers already created the authoritative file in static/.
        if os.path.realpath(source_path) != self.local_path(key):
            raise ValueError('Local publish source does not match its storage key.')

    def delete(self, key):
        path = self.local_path(key)
        if not os.path.isfile(path) and not os.path.islink(path):
            return False
        os.remove(path)
        return True

    def inventory(self):
        uploads = os.path.join(self.root, 'uploads')
        if not os.path.isdir(uploads):
            return []
        rows = []
        for folder, dirs, names in os.walk(uploads):
            dirs.sort(); names.sort()
            for name in names:
                path = os.path.join(folder, name)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    continue
                key = 'uploads/' + os.path.relpath(path, uploads).replace(os.sep, '/')
                rows.append({'key': key, 'size': size})
        return rows

    def health(self):
        os.makedirs(os.path.join(self.root, 'uploads'), exist_ok=True)
        return {'status': 'ok', 'backend': self.mode, 'root': self.root}

    @contextlib.contextmanager
    def materialize(self, key):
        path = self.local_path(key)
        if not os.path.isfile(path):
            raise FileNotFoundError(key)
        yield path

    def response(self, key, download_name=None, max_age=86400):
        return send_file(self.local_path(key), conditional=True, max_age=max_age,
                         as_attachment=bool(download_name), download_name=download_name)


class S3Storage:
    mode = 's3'

    def __init__(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError('boto3 is required when STORAGE_BACKEND=s3.') from exc
        self.bucket = current_app.config.get('STORAGE_BUCKET') or os.environ.get('STORAGE_BUCKET', '')
        if not self.bucket:
            raise RuntimeError('STORAGE_BUCKET is required when STORAGE_BACKEND=s3.')
        self.prefix = (current_app.config.get('STORAGE_PREFIX') or os.environ.get('STORAGE_PREFIX', '')).strip('/')
        self.client = boto3.client(
            's3',
            endpoint_url=current_app.config.get('STORAGE_ENDPOINT_URL') or os.environ.get('STORAGE_ENDPOINT_URL') or None,
            region_name=current_app.config.get('STORAGE_REGION') or os.environ.get('STORAGE_REGION') or None,
        )

    def object_key(self, key):
        normalized = normalize_key(key)
        return f'{self.prefix}/{normalized}' if self.prefix else normalized

    def local_working_path(self, key):
        """Hydrate a private local cache for native SDK and PDF tools."""
        normalized = normalize_key(key)
        static_root = os.path.realpath(current_app.static_folder)
        destination = os.path.realpath(os.path.join(static_root, *PurePosixPath(normalized).parts))
        if os.path.commonpath([static_root, destination]) != static_root:
            raise ValueError('Unsafe storage cache path.')
        if os.path.isfile(destination):
            return destination
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.drogo_download_', dir=os.path.dirname(destination))
        os.close(descriptor)
        try:
            self.client.download_file(self.bucket, self.object_key(normalized), temporary)
            os.replace(temporary, destination)
        finally:
            try:
                os.remove(temporary)
            except OSError:
                pass
        return destination

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self.object_key(key))
            return True
        except Exception:
            return False

    def size(self, key):
        result = self.client.head_object(Bucket=self.bucket, Key=self.object_key(key))
        return int(result.get('ContentLength') or 0)

    def publish(self, key, source_path):
        self.client.upload_file(source_path, self.bucket, self.object_key(key))

    def delete(self, key):
        existed = self.exists(key)
        self.client.delete_object(Bucket=self.bucket, Key=self.object_key(key))
        return existed

    def inventory(self):
        prefix = f'{self.prefix}/uploads/' if self.prefix else 'uploads/'
        rows = []
        paginator = self.client.get_paginator('list_objects_v2')
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get('Contents') or []:
                object_key = item['Key']
                key = object_key[len(self.prefix) + 1:] if self.prefix else object_key
                rows.append({'key': key, 'size': int(item.get('Size') or 0)})
        return rows

    def health(self):
        self.client.head_bucket(Bucket=self.bucket)
        return {'status': 'ok', 'backend': self.mode, 'bucket': self.bucket,
                'prefix': self.prefix}

    @contextlib.contextmanager
    def materialize(self, key):
        suffix = PurePosixPath(normalize_key(key)).suffix
        descriptor, path = tempfile.mkstemp(prefix='drogo_storage_', suffix=suffix)
        os.close(descriptor)
        try:
            self.client.download_file(self.bucket, self.object_key(key), path)
            yield path
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def response(self, key, download_name=None, max_age=86400):
        params = {'Bucket': self.bucket, 'Key': self.object_key(key)}
        if download_name:
            params['ResponseContentDisposition'] = f'attachment; filename="{download_name}"'
        url = self.client.generate_presigned_url('get_object', Params=params, ExpiresIn=min(max_age, 3600))
        return redirect(url, code=302)


def get_storage():
    mode = (current_app.config.get('STORAGE_BACKEND') or os.environ.get('STORAGE_BACKEND') or 'local').casefold()
    extension = current_app.extensions.get('drogo_storage')
    if extension and extension.mode == mode:
        return extension
    storage = S3Storage() if mode in {'s3', 'object', 'object-storage'} else LocalStorage()
    current_app.extensions['drogo_storage'] = storage
    return storage


def stored_url(value):
    try:
        key = normalize_key(value)
    except ValueError:
        return f'/static/{str(value or "").lstrip("/")}' if value else ''
    return '/uploads/' + key[len('uploads/'):]
