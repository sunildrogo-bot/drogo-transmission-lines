"""Required-feature registry used by Settings and the safe release builder."""
from pathlib import Path

APPLICATION_VERSION = '1.27.0'

FEATURES = (
    ('admin_dashboard', 'Admin Dashboard', 'templates/admin.html', 'api/dashboard/summary'),
    ('user_management', 'User Management', 'templates/users.html', '/api/users'),
    ('module_management', 'Module Management', 'templates/admin.html', 'view=modules'),
    ('inspection_quality', 'Inspection Quality', 'templates/inspection_quality.html', '/api/inspection-quality'),
    ('settings', 'Settings', 'templates/settings.html', 'Application Settings'),
    ('project_navigation', 'Project, Division and Line navigation', 'templates/project_map.html', '/api/divisions/'),
    ('rgb_thermal', 'RGB and Thermal galleries', 'templates/project_map.html', 'thermal-points'),
    ('tower_filters', 'Tower filters', 'templates/project_map.html', 'Critical Defects'),
    ('defect_marking', 'Defect marking', 'projects_routes.py', 'api_create_tower_defect'),
    ('defect_location_status', 'Expanded defect location and status', 'templates/project_map.html', 'COMMON_DEFECT_STATUS_OPTIONS'),
    ('inspection_done', 'Inspection Done', 'projects_routes.py', 'inspection-done'),
    ('client_closure', 'Client defect closure', 'projects_routes.py', 'resolution_status'),
    ('pdf_reports', 'PDF reports', 'tower_report.py', 'build_tower_report_pdf'),
    ('training_export', 'Training Dataset Export', 'training_export_routes.py', '/api/training-datasets'),
    ('backup_recovery', 'Backup and Recovery', 'backup_routes.py', '/api/settings/backups'),
    ('notifications', 'Notifications', 'notification_routes.py', '/api/notifications'),
    ('ai_summaries', 'AI summaries', 'assistant_api.py', 'tool_generate_central_report'),
    ('background_worker', 'Background worker', 'background_jobs.py', 'HANDLERS'),
    ('private_storage', 'Private file access', 'access_control.py', 'can_access_upload'),
    ('release_history', 'Release compatibility', 'feature_registry.py', 'assert_source_compatible'),
    ('gallery_pagination', 'Complete gallery pagination', 'templates/project_map.html', 'next_after_id'),
    ('activity_pagination', 'Paginated Activity Log', 'settings_routes.py', "category_terms"),
    ('resilient_maps', 'Resilient configurable maps', 'templates/base.html', 'map_tile_url'),
    ('deployment_health', 'Production deployment health', 'settings_routes.py', "details['web']"),
    ('data_repair', 'Data integrity repair centre', 'backup_routes.py', '/api/settings/data-health/relink'),
    ('workflow_regression', 'End-to-end workflow regression', 'tests/test_workflow_end_to_end.py', 'test_upload_inspect_release_client_close_report_and_dataset_preview'),
    ('storage_control_centre', 'Uploads and Storage control centre', 'templates/settings.html', 'project-data-grid'),
    ('transmission_card_covers', 'Transmission project and division covers', 'static/js/projects.js', 'marketing_rgb.jpg'),
    ('defect_edit_search', 'Editable and searchable defect records', 'templates/project_map.html', 'startEditDefect'),
    ('active_defect_consistency', 'Soft-deleted defects excluded from active results', 'models.py', 'active_defects ='),
    ('image_viewer_controls', 'Scoped image zoom and pointer panning', 'templates/project_map.html', "wrap.addEventListener('pointerdown'"),
)


def source_compatibility(root):
    root = Path(root)
    results = []
    for key, name, relative, marker in FEATURES:
        path = root / relative
        available = path.is_file()
        message = ''
        if available:
            try:
                available = marker in path.read_text(encoding='utf-8', errors='replace')
            except OSError as exc:
                available, message = False, str(exc)
        if not available and not message:
            message = f'Missing {relative} or required marker.'
        results.append({'key': key, 'name': name, 'available': available,
                        'file': relative, 'message': message})
    passed = sum(row['available'] for row in results)
    return {'version': APPLICATION_VERSION, 'passed': passed, 'missing': len(results) - passed,
            'total': len(results), 'compatible': all(row['available'] for row in results),
            'features': results}


def assert_source_compatible(root):
    report = source_compatibility(root)
    if not report['compatible']:
        names = ', '.join(row['name'] for row in report['features'] if not row['available'])
        raise RuntimeError(f'Release blocked: required features are missing: {names}.')
    return report
