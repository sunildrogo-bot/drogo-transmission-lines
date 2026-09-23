"""Safe storage helpers for retaining tower findings while removing raw data."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import PurePosixPath

from flask import current_app

from storage_service import get_storage, normalize_key


def _evidence_key(image_path: str) -> str:
    """Return the protected evidence location for a tower image."""
    source = normalize_key(image_path)
    source_dir, filename = source.rsplit('/', 1)
    evidence_dir = re.sub(r'/raw$', '/defects', source_dir)
    if evidence_dir == source_dir:
        evidence_dir = source_dir + '_defects'
    return f'{evidence_dir}/{filename}'


def preserve_finding_photo(photo) -> bool:
    """Ensure a full-quality, independently stored copy exists for a finding.

    ``defect_copy_path`` is retained for schema compatibility, but the copy is
    also used for measured thermal photos. The bytes are unchanged, which
    preserves DJI radiometric data for later re-measurement.
    """
    storage = get_storage()
    if photo.defect_copy_path:
        try:
            if storage.exists(photo.defect_copy_path):
                return True
        except (OSError, RuntimeError, ValueError):
            pass

    source_key = photo.image_path or ''
    if not source_key:
        return False
    try:
        if not storage.exists(source_key):
            return False
        destination_key = _evidence_key(source_key)
        source_size = storage.size(source_key)

        if not storage.exists(destination_key):
            if storage.mode == 'local':
                source_path = storage.local_working_path(source_key)
                destination_path = storage.local_path(destination_key)
                os.makedirs(os.path.dirname(destination_path), exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(
                    prefix='.drogo_evidence_', dir=os.path.dirname(destination_path))
                os.close(descriptor)
                try:
                    shutil.copy2(source_path, temporary)
                    os.replace(temporary, destination_path)
                finally:
                    try:
                        os.remove(temporary)
                    except OSError:
                        pass
            else:
                with storage.materialize(source_key) as source_path:
                    storage.publish(destination_key, source_path)

        if not storage.exists(destination_key) or storage.size(destination_key) != source_size:
            return False
        photo.defect_copy_path = destination_key
        return True
    except (OSError, RuntimeError, ValueError):
        current_app.logger.exception(
            'Could not preserve finding evidence for tower photo %s', getattr(photo, 'id', '?'))
        return False


def safe_stored_size(storage, stored_path: str | None) -> int:
    if not stored_path:
        return 0
    try:
        return int(storage.size(stored_path)) if storage.exists(stored_path) else 0
    except (OSError, RuntimeError, ValueError):
        return 0


def format_bytes(value: int) -> str:
    amount = float(max(0, int(value or 0)))
    units = ('B', 'KB', 'MB', 'GB', 'TB')
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    precision = 0 if unit == 'B' else (1 if amount >= 10 else 2)
    return f'{amount:.{precision}f} {unit}'

