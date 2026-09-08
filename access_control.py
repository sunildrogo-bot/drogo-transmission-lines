"""Central authorization rules for projects, lines, towers, and uploads.

Every route should resolve the resource it is handling and delegate the
permission decision here.  Keeping the rules in one place prevents page,
API, media, Pilot, SME, and assistant access from drifting apart.
"""
from pathlib import PurePosixPath

from flask import session

from models import (
    Announcement,
    CorridorPhoto,
    DefectResolutionEvent,
    Division,
    Line,
    PilotAssignment,
    Project,
    SmeAssignment,
    TowerInspectionStatus,
    TowerPhoto,
    TowerReport,
    User,
)


def current_user():
    user_id = session.get('user_id')
    return User.query.get(user_id) if user_id else None


def current_role():
    return session.get('role', '')


def _active_role_is_valid(user):
    return bool(user and current_role() in user.role_names())


def visible_line_ids():
    """Line IDs visible to the active role; ``None`` means Admin/all."""
    user = current_user()
    role = current_role()
    if not _active_role_is_valid(user):
        return set()
    if role == 'Admin':
        return None
    if role == 'Pilot':
        return {
            row[0] for row in
            PilotAssignment.query.with_entities(PilotAssignment.line_id)
            .filter_by(pilot_user_id=user.id).all()
        }
    if role == 'SME':
        return {
            row[0] for row in
            SmeAssignment.query.with_entities(SmeAssignment.line_id)
            .filter_by(sme_user_id=user.id).all()
        }
    if role == 'Client User':
        assigned_modules = set(user.module_names())
        project_ids = {p.id for p in user.allowed_projects if p.module in assigned_modules}
        if not project_ids:
            return set()
        return {
            row[0] for row in
            Line.query.with_entities(Line.id)
            .join(Division, Line.division_id == Division.id)
            .filter(Division.project_id.in_(project_ids)).all()
        }
    return set()


def visible_project_ids(module_name=None):
    """Project IDs visible to the active role; ``None`` means Admin/all.

    A Client with no selected projects has no project access.  Pilot and
    SME visibility is derived from their line assignments, never from a
    guessed project ID or a broad module grant.
    """
    user = current_user()
    role = current_role()
    if not _active_role_is_valid(user):
        return set()
    if role == 'Admin':
        return None

    if role == 'Client User':
        if module_name and module_name not in user.module_names():
            return set()
        assigned_modules = set(user.module_names())
        return {
            p.id for p in user.allowed_projects
            if p.module in assigned_modules and (not module_name or p.module == module_name)
        }

    line_ids = visible_line_ids()
    if not line_ids:
        return set()
    query = (
        Project.query.with_entities(Project.id)
        .join(Division, Division.project_id == Project.id)
        .join(Line, Line.division_id == Division.id)
        .filter(Line.id.in_(line_ids))
    )
    if module_name:
        query = query.filter(Project.module == module_name)
    return {row[0] for row in query.distinct().all()}


def has_module_access(module_name):
    if current_role() == 'Admin' and _active_role_is_valid(current_user()):
        return True
    ids = visible_project_ids(module_name)
    return bool(ids)


def can_access_project(project):
    if not project:
        return False
    allowed = visible_project_ids(project.module)
    return allowed is None or project.id in allowed


def can_access_division(division):
    if not division:
        return False
    if current_role() in ('Pilot', 'SME'):
        allowed_lines = visible_line_ids()
        return allowed_lines is None or any(line.id in allowed_lines for line in division.lines)
    return can_access_project(division.project)


def can_access_line(line):
    if not line:
        return False
    allowed_lines = visible_line_ids()
    return allowed_lines is None or line.id in allowed_lines


def can_access_photo(photo):
    return bool(photo and can_access_line(Line.query.get(photo.line_id)))


def tower_is_released(line_id, tower_label):
    status = TowerInspectionStatus.query.filter_by(
        line_id=line_id,
        tower_label=(tower_label or '').strip(),
    ).first()
    return bool(status and status.inspection_done)


def client_can_access_tower(line_id, tower_label):
    if current_role() != 'Client User':
        return True
    return tower_is_released(line_id, tower_label)


def _normalise_upload_path(stored_path):
    raw = (stored_path or '').replace('\\', '/').lstrip('/')
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or '..' in path.parts:
        return ''
    return path.as_posix()


def can_access_upload(stored_path):
    """Authorize an exact path stored relative to Flask's static folder."""
    path = _normalise_upload_path(stored_path)
    user = current_user()
    if not path or not _active_role_is_valid(user):
        return False
    if current_role() == 'Admin':
        return True

    if path.startswith('uploads/announcement_images/'):
        return Announcement.query.filter_by(image_path=path).first() is not None

    if path.startswith('uploads/logos/'):
        project = Project.query.filter_by(logo_path=path).first()
        return can_access_project(project) if project else False

    if path.startswith('uploads/kml/'):
        line = Line.query.filter_by(kml_path=path).first()
        return can_access_line(line) if line else False

    if path.startswith('uploads/corridor_photos/'):
        corridor = CorridorPhoto.query.filter(
            (CorridorPhoto.image_path == path) | (CorridorPhoto.thumbnail_path == path)
        ).first()
        return can_access_line(Line.query.get(corridor.line_id)) if corridor else False

    if path.startswith('uploads/tower_photos/'):
        photo = TowerPhoto.query.filter(
            (TowerPhoto.image_path == path)
            | (TowerPhoto.thumbnail_path == path)
            | (TowerPhoto.defect_copy_path == path)
        ).first()
        return bool(
            photo
            and can_access_photo(photo)
            and client_can_access_tower(photo.line_id, photo.tower_label)
        )

    if path.startswith('uploads/tower_reports/'):
        report = TowerReport.query.filter_by(report_path=path).first()
        return bool(
            report
            and can_access_line(Line.query.get(report.line_id))
            and client_can_access_tower(report.line_id, report.tower_label)
        )

    if path.startswith('uploads/defect_rectifications/'):
        event = DefectResolutionEvent.query.filter_by(evidence_image_path=path).first()
        photo = event.defect.photo if event and event.defect else None
        return bool(
            photo and can_access_photo(photo)
            and client_can_access_tower(photo.line_id, photo.tower_label)
        )

    if path.startswith('uploads/user_photos/'):
        profile_owner = User.query.filter_by(photo_path=path).first()
        return bool(profile_owner and profile_owner.id == user.id)

    # Legacy project banners may live outside the standard folders.
    project = Project.query.filter_by(legacy_banner=path).first()
    if project:
        return can_access_project(project)

    # Generated central reports and untracked legacy upload paths are never
    # exposed to non-Admin sessions.  They must be served by a mapped record
    # or a purpose-built authorized endpoint.
    return False
