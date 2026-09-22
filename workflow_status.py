"""Canonical workflow labels shared by APIs, maps, dashboards and reports.

Database fields stay backward-compatible; these helpers prevent individual
pages from inventing conflicting display labels for the same state.
"""

TOWER_NOT_STARTED = 'Not Started'
TOWER_IMAGES_UPLOADED = 'Images Uploaded'
TOWER_ASSIGNED = 'Assigned'
TOWER_REVIEW_IN_PROGRESS = 'Review In Progress'
TOWER_INSPECTION_DONE = 'Inspection Done'

DEFECT_OPEN = 'Open'
DEFECT_RECTIFICATION_IN_PROGRESS = 'Rectification In Progress'
DEFECT_RECTIFIED = 'Rectified'
DEFECT_CLOSED = 'Closed'

JOB_QUEUED = 'Queued'
JOB_PROCESSING = 'Processing'
JOB_COMPLETED = 'Completed'
JOB_COMPLETED_WITH_WARNINGS = 'Completed with Warnings'
JOB_FAILED = 'Failed'


def tower_status(*, inspection_done=False, photo_count=0, reviewed_count=0, assigned=False):
    if inspection_done:
        return TOWER_INSPECTION_DONE
    if reviewed_count:
        return TOWER_REVIEW_IN_PROGRESS
    if assigned:
        return TOWER_ASSIGNED
    if photo_count:
        return TOWER_IMAGES_UPLOADED
    return TOWER_NOT_STARTED


def normalize_defect_status(value):
    key = (value or DEFECT_OPEN).strip().casefold()
    return {
        'open': DEFECT_OPEN, 'in progress': DEFECT_RECTIFICATION_IN_PROGRESS,
        'rectification in progress': DEFECT_RECTIFICATION_IN_PROGRESS,
        'rectified': DEFECT_RECTIFIED, 'closed': DEFECT_CLOSED,
    }.get(key, value or DEFECT_OPEN)
