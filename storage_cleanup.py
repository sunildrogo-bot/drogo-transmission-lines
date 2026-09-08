"""Collect and safely remove files owned by deleted database resources."""
import os
from pathlib import PurePosixPath

from flask import current_app

from models import CorridorPhoto, DefectResolutionEvent, Division, Line, Project, TowerDefect, TowerPhoto, TowerReport


def _line_paths(line_ids):
    ids = [int(value) for value in line_ids]
    if not ids:
        return set()
    paths = set()
    for (path,) in Line.query.with_entities(Line.kml_path).filter(Line.id.in_(ids)).all():
        if path:
            paths.add(path)
    for row in TowerPhoto.query.filter(TowerPhoto.line_id.in_(ids)).all():
        paths.update(filter(None, (row.image_path, row.thumbnail_path, row.defect_copy_path)))
    for row in CorridorPhoto.query.filter(CorridorPhoto.line_id.in_(ids)).all():
        paths.update(filter(None, (row.image_path, row.thumbnail_path)))
    for (path,) in TowerReport.query.with_entities(TowerReport.report_path).filter(TowerReport.line_id.in_(ids)).all():
        if path:
            paths.add(path)
    for (path,) in (
        DefectResolutionEvent.query.with_entities(DefectResolutionEvent.evidence_image_path)
        .join(TowerDefect, DefectResolutionEvent.defect_id == TowerDefect.id)
        .join(TowerPhoto, TowerDefect.tower_photo_id == TowerPhoto.id)
        .filter(TowerPhoto.line_id.in_(ids)).all()
    ):
        if path:
            paths.add(path)
    return paths


def collect_line_files(line_id):
    return _line_paths([line_id])


def collect_division_files(division_id):
    line_ids = [row[0] for row in Line.query.with_entities(Line.id).filter_by(division_id=division_id).all()]
    return _line_paths(line_ids)


def collect_project_files(project_id):
    project = Project.query.get(project_id)
    paths = set(filter(None, (project.logo_path, project.legacy_banner))) if project else set()
    division_ids = [row[0] for row in Division.query.with_entities(Division.id).filter_by(project_id=project_id).all()]
    if division_ids:
        line_ids = [row[0] for row in Line.query.with_entities(Line.id).filter(Line.division_id.in_(division_ids)).all()]
        paths.update(_line_paths(line_ids))
    return paths


def delete_stored_files(stored_paths):
    """Delete exact tracked upload files and prune empty upload directories.

    Paths outside static/uploads are skipped, even if a corrupted database row
    contains an absolute path or traversal sequence.
    """
    from storage_service import get_storage
    storage = get_storage()
    static_root = os.path.realpath(current_app.static_folder)
    uploads_root = os.path.realpath(os.path.join(static_root, 'uploads'))
    result = {'removed': 0, 'missing': 0, 'skipped': 0, 'errors': []}

    for stored_path in sorted(set(filter(None, stored_paths))):
        raw = str(stored_path).replace('\\', '/').lstrip('/')
        pure = PurePosixPath(raw)
        if pure.is_absolute() or '..' in pure.parts or not raw.startswith('uploads/'):
            result['skipped'] += 1
            continue
        try:
            if storage.delete(raw):
                result['removed'] += 1
                if storage.mode == 'local':
                    absolute = storage.local_path(raw)
                    _prune_empty_parents(os.path.dirname(absolute), uploads_root)
            else:
                result['missing'] += 1
        except (OSError, ValueError, RuntimeError) as exc:
            result['errors'].append(f'{raw}: {exc}')
    return result


def _prune_empty_parents(directory, stop_at):
    current = os.path.realpath(directory)
    while current != stop_at:
        try:
            if os.path.commonpath([stop_at, current]) != stop_at:
                return
            os.rmdir(current)
        except (OSError, ValueError):
            return
        current = os.path.dirname(current)
