"""Exact-byte duplicate detection and conservative tower-photo cleanup.

The application deliberately does not use filenames as proof that two drone
images are identical.  A SHA-256 digest is calculated from the stored original
and cleanup is allowed only when every duplicate belongs to the same line and
tower.  Any photo carrying inspection/audit evidence is retained; groups with
more than one evidence-bearing copy are left for manual review.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime

from models import Line, TowerPhoto, db
from storage_service import get_storage, normalize_key


HASH_CHUNK_SIZE = 1024 * 1024


def _publish_scan_progress(job, phase, current, total, hashed=0, missing=0,
                           failed=0, commit=False):
    """Publish useful live scan state without adding a database column."""
    if job is None:
        return
    job.progress_current = current
    job.progress_total = total
    job.heartbeat_at = datetime.utcnow()
    job.result_json = json.dumps({
        'phase': phase,
        'photos_scanned': current,
        'photos_total': total,
        'hashes_added': hashed,
        'missing_files': missing,
        'hash_failures': failed,
    })
    if commit:
        db.session.commit()


def hash_stored_photo(photo):
    """Return the exact SHA-256 digest of a photo's surviving original."""
    candidates = [photo.display_image_path() if hasattr(photo, 'display_image_path')
                  else (photo.image_path or '')]
    if getattr(photo, 'defect_copy_path', None):
        candidates.append(photo.defect_copy_path)
    for stored_path in dict.fromkeys(filter(None, candidates)):
        try:
            key = normalize_key(stored_path)
            storage = get_storage()
            if not storage.exists(key):
                continue
            digest = hashlib.sha256()
            with storage.materialize(key) as local_path:
                with open(local_path, 'rb') as source:
                    for block in iter(lambda: source.read(HASH_CHUNK_SIZE), b''):
                        digest.update(block)
            return digest.hexdigest()
        except (OSError, RuntimeError, ValueError):
            continue
    return None


def stored_photo_exists(photo):
    """Whether a full-quality copy still exists for duplicate blocking."""
    candidates = [getattr(photo, 'image_path', '')]
    if getattr(photo, 'defect_copy_path', None):
        candidates.append(photo.defect_copy_path)
    storage = get_storage()
    for stored_path in dict.fromkeys(filter(None, candidates)):
        try:
            if storage.exists(normalize_key(stored_path)):
                return True
        except (OSError, RuntimeError, ValueError):
            continue
    return False


def has_protected_evidence(photo):
    """True when deleting this row could discard review or audit evidence."""
    return bool(
        photo.defects
        or photo.thermal_points
        or photo.defect_copy_path
        or photo.raw_deleted
        or photo.reviewed_at
        or (photo.review_outcome or 'Pending') != 'Pending'
    )


def photo_stored_paths(photo):
    """All stored files owned exclusively by a TowerPhoto row."""
    paths = {photo.image_path, photo.thumbnail_path, photo.defect_copy_path}
    paths.update(
        event.evidence_image_path
        for defect in photo.defects
        for event in defect.resolution_events
        if event.evidence_image_path
    )
    return set(filter(None, paths))


def photo_metadata_score(photo):
    """Prefer the clean copy carrying the most useful upload metadata."""
    fields = ('media_type', 'image_width', 'image_height', 'captured_at',
              'gps_lat', 'gps_lng', 'validation_status', 'thumbnail_path')
    return sum(getattr(photo, field, None) not in (None, '') for field in fields)


def analyse_duplicate_photos(photos):
    """Classify exact duplicate rows without mutating them.

    Safe groups contain one or zero evidence-bearing rows and use one canonical
    keeper.  Cross-tower matches and groups with evidence on multiple copies are
    never auto-deleted.
    """
    by_line_hash = defaultdict(list)
    for photo in photos:
        digest = (photo.content_hash or '').strip().lower()
        if len(digest) == 64:
            by_line_hash[(photo.line_id, digest)].append(photo)

    line_ids = {line_id for line_id, _digest in by_line_hash}
    lines = {row.id: row for row in Line.query.filter(Line.id.in_(line_ids)).all()} if line_ids else {}
    safe_groups = []
    protected_groups = []
    tower_conflicts = []

    for (line_id, digest), group in sorted(by_line_hash.items(), key=lambda item: (item[0][0], item[0][1])):
        if len(group) < 2:
            continue
        group = sorted(group, key=lambda photo: photo.id)
        line = lines.get(line_id)
        base = {
            'line_id': line_id,
            'line_name': line.name if line else f'Line {line_id}',
            'project_name': (
                line.division.project.name
                if line and line.division and line.division.project else ''
            ),
            'hash_prefix': digest[:12],
            'photo_ids': [photo.id for photo in group],
        }
        tower_labels = sorted({photo.tower_label for photo in group})
        if len(tower_labels) != 1:
            tower_conflicts.append({**base, 'tower_labels': tower_labels,
                                    'reason': 'Identical file is assigned to different towers.'})
            continue

        protected = [photo for photo in group if has_protected_evidence(photo)]
        if len(protected) > 1:
            protected_groups.append({
                **base,
                'tower_label': tower_labels[0],
                'protected_photo_ids': [photo.id for photo in protected],
                'reason': 'More than one copy contains review, defect, thermal, or audit evidence.',
            })
            continue

        keeper = protected[0] if protected else max(
            group, key=lambda photo: (photo_metadata_score(photo), -photo.id))
        delete_ids = [photo.id for photo in group if photo.id != keeper.id]
        safe_groups.append({
            **base,
            'tower_label': tower_labels[0],
            'keeper_photo_id': keeper.id,
            'delete_photo_ids': delete_ids,
            'protected_keeper': bool(protected),
        })

    return {
        'duplicate_groups': len(safe_groups) + len(protected_groups) + len(tower_conflicts),
        'safe_groups': safe_groups,
        'safe_to_remove': sum(len(group['delete_photo_ids']) for group in safe_groups),
        'protected_groups': protected_groups,
        'tower_conflicts': tower_conflicts,
    }


def backfill_missing_hashes(job=None):
    """Hash legacy photo rows and return a complete conservative scan result."""
    _publish_scan_progress(job, 'Preparing image inventory', 0, 0, commit=True)
    photos = TowerPhoto.query.order_by(TowerPhoto.id).all()
    total = len(photos)
    _publish_scan_progress(job, 'Fingerprinting stored originals', 0, total, commit=True)

    hashed = missing = failed = 0
    for index, photo in enumerate(photos, 1):
        digest = (photo.content_hash or '').strip().lower()
        if len(digest) != 64:
            digest = hash_stored_photo(photo)
            if digest:
                photo.content_hash = digest
                hashed += 1
            else:
                missing += 1
        _publish_scan_progress(
            job, 'Fingerprinting stored originals', index, total,
            hashed=hashed, missing=missing, failed=failed,
        )
        if index % 5 == 0:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                failed += 1
    db.session.commit()

    _publish_scan_progress(
        job, 'Analysing exact duplicate groups', total, total,
        hashed=hashed, missing=missing, failed=failed, commit=True,
    )
    result = analyse_duplicate_photos(photos)
    result.update({
        'phase': 'Completed',
        'photos_scanned': len(photos),
        'photos_total': len(photos),
        'hashes_added': hashed,
        'missing_files': missing,
        'hash_failures': failed,
    })
    return result
