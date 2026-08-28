"""Dependency-free regression checks for the critical security wiring.

These complement request-level Flask tests in a deployed environment and can
run before the application's third-party dependencies are installed.
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / name).read_text(encoding='utf-8')


class SecurityWiringTests(unittest.TestCase):
    def test_backup_manager_is_admin_only_integrity_checked_and_excludes_secrets(self):
        app = source('app.py')
        routes = source('backup_routes.py')
        script = source('static/js/settings.js')
        self.assertIn('app.register_blueprint(backup_bp)', app)
        self.assertIn("session.get('role') != 'Admin'", routes)
        self.assertIn("compression=zipfile.ZIP_STORED", routes)
        self.assertIn('allowZip64=True', routes)
        self.assertIn('source_db.backup(destination_db)', routes)
        self.assertIn("archive.testzip()", routes)
        self.assertIn("'secrets_included': False", routes)
        self.assertIn("'database_sha256': _sha256(snapshot_path)", routes)
        self.assertIn('and not photo.raw_deleted', routes)
        self.assertIn('missing_thumbnail_paths = sorted(thumbnails - actual)', routes)
        self.assertIn("os.path.commonpath", routes)
        self.assertIn("Automatic database snapshots currently require the local SQLite database", routes)
        self.assertIn("fetch('/api/settings/data-health')", script)
        self.assertIn("fetch('/api/settings/backups'", script)
        self.assertIn('Create Pre-Upgrade Backup', script)

    def test_training_dataset_exports_are_admin_only_reproducible_and_private(self):
        app = source('app.py')
        routes = source('training_export_routes.py')
        template = source('templates/training_datasets.html')
        script = source('static/js/training_datasets.js')
        settings = source('templates/settings.html')
        self.assertIn('app.register_blueprint(training_export_bp)', app)
        self.assertIn("session.get('role') != 'Admin'", routes)
        self.assertIn("{'yolo-detection', 'coco-detection'}", routes)
        self.assertIn("if photo.is_thermal_image():", routes)
        self.assertIn("TowerInspectionStatus.inspection_done.is_(True)", routes)
        self.assertIn("_split_towers", routes)
        self.assertIn("defect.shape_type == 'circle'", routes)
        self.assertIn("compression=zipfile.ZIP_STORED", routes)
        self.assertIn("allowZip64=True", routes)
        self.assertIn("os.path.commonpath", routes)
        self.assertIn("return send_file(path, as_attachment=True", routes)
        self.assertIn("methods=['DELETE']", routes)
        self.assertIn("dataset_manifest.json", routes)
        self.assertIn("training_class_map.json", routes)
        self.assertIn("def _mapped_label(", routes)
        self.assertIn("@training_export_bp.route('/api/training-datasets/classes'", routes)
        self.assertIn("class_mapping.json", routes)
        self.assertIn("manifest.csv", routes)
        self.assertIn("data.yaml", routes)
        self.assertIn("f'annotations/instances_{split}.json'", routes)
        self.assertIn('id="ds-preview-btn"', template)
        self.assertIn('id="ds-class-manager"', template)
        self.assertIn("fetch('/api/training-datasets/preview'", script)
        self.assertIn("fetch('/api/training-datasets/classes'", script)
        self.assertIn('Training Dataset Export', settings)

    def test_settings_control_center_preserves_real_admin_workflows(self):
        template = source('templates/settings.html')
        script = source('static/js/settings.js')
        routes = source('settings_routes.py')
        for view_id in (
            'view-workflow', 'view-inspection', 'view-uploads',
            'view-assistant', 'view-notifications', 'view-security',
        ):
            self.assertIn(f'id="{view_id}"', template)
        for target_id in (
            'all-projects-body', 'activity-log-body', 'ann-list',
            'help-tab-list', 'dp-new', 'storage-total',
        ):
            self.assertIn(f'id="{target_id}"', template)
        self.assertIn("fetch('/api/settings/storage-summary')", script)
        self.assertIn("@settings_bp.route('/api/settings/storage-summary'", routes)
        self.assertIn("os.path.join(current_app.static_folder, 'uploads')", routes)

    def test_state_changing_session_routes_are_post_only(self):
        app = source('app.py')
        for route in ('/logout', '/switch-to-admin', '/switch-to-client'):
            self.assertRegex(
                app,
                rf"@app\.route\('{re.escape(route)}', methods=\['POST'\]\)",
            )

    def test_csrf_and_private_upload_guards_are_registered(self):
        app = source('app.py')
        self.assertIn('def _protect_state_changes()', app)
        self.assertIn("request.headers.get('X-CSRF-Token')", app)
        self.assertIn('def _protect_uploaded_media()', app)
        self.assertIn('can_access_upload(stored_path)', app)
        self.assertNotIn("Cache-Control'] = 'public", app)

    def test_client_project_access_is_strict(self):
        models = source('models.py')
        access = source('access_control.py')
        self.assertNotIn('return ids if ids else None', models)
        self.assertIn('if role == \'Client User\':', access)
        self.assertIn('p.id for p in user.allowed_projects', access)

    def test_pilot_writes_require_assignment_and_server_kml_coordinates(self):
        routes = source('projects_routes.py')
        self.assertIn('def _pilot_or_admin_guard(line_id):', routes)
        self.assertGreaterEqual(routes.count('_pilot_or_admin_guard(line_id)'), 3)
        self.assertIn("return jsonify({'error': \"This line isn't assigned to you.\"}), 403", routes)
        self.assertIn('tower_coords = _kml_tower_coordinates(line, tower_label)', routes)

    def test_client_release_guards_cover_flat_and_photo_endpoints(self):
        routes = source('projects_routes.py')
        self.assertGreaterEqual(routes.count('_client_tower_release_guard('), 5)
        self.assertGreaterEqual(routes.count('_client_photo_release_guard('), 4)

    def test_tower_delete_is_admin_only(self):
        settings = source('settings_routes.py')
        start = settings.index('def api_settings_delete_tower')
        body = settings[start:start + 800]
        self.assertIn('guard = _require_admin()', body)
        self.assertNotIn('guard = _login_guard()', body)

    def test_database_backed_session_revocation_is_global(self):
        app = source('app.py')
        models = source('models.py')
        users = source('users.py')
        self.assertIn('def _validate_authenticated_session():', app)
        self.assertIn("user.status != 'Active'", app)
        self.assertIn("session.get('session_version')", app)
        self.assertIn('session_version   = db.Column', models)
        self.assertIn('user.session_version = int(user.session_version or 1) + 1', users)

    def test_help_ticket_access_uses_immutable_user_id(self):
        models = source('models.py')
        routes = source('help_routes.py')
        self.assertIn('submitted_by_user_id = db.Column', models)
        self.assertIn('q.filter_by(submitted_by_user_id=session.get(\'user_id\'))', routes)
        self.assertIn('if not is_admin and not is_owner:', routes)
        self.assertNotIn("q.filter_by(submitted_by=session.get('user_name'", routes)

    def test_open_defects_use_resolution_status(self):
        settings = source('settings_routes.py')
        assistant = source('assistant_api.py')
        self.assertIn("filter_by(severity='Critical', resolution_status='Open')", settings)
        self.assertIn('TowerDefect.resolution_status == status', assistant)
        self.assertNotIn('TowerDefect.status == status', assistant)

    def test_startup_schema_mutation_was_replaced_by_versioned_migrations(self):
        app = source('app.py')
        self.assertNotIn('ensure_defect_columns(app)', app)
        self.assertNotIn('db.create_all()', app)
        versions = ROOT / 'migrations' / 'versions'
        self.assertTrue((versions / '20260825_0001_adopt_existing_schema.py').is_file())
        migration = source('migrations/versions/20260825_0002_session_ticket_defect_integrity.py')
        self.assertIn("revision = '20260825_0002'", migration)
        self.assertIn("UPDATE tower_defects SET status = 'OK' WHERE status = 'Open'", migration)

    def test_login_and_password_reset_are_database_throttled(self):
        app = source('app.py')
        auth_api = source('auth_api.py')
        controls = source('security_controls.py')
        models = source('models.py')
        migration = source('migrations/versions/20260825_0003_auth_hardening_and_creator_fk.py')
        self.assertIn('login_limit(email)', app)
        self.assertIn('record_login_failure(email)', app)
        self.assertIn('consume_password_reset(email)', app)
        self.assertIn('login_limit(email)', auth_api)
        self.assertIn("_record('login_ip'", controls)
        self.assertIn("_record('reset_ip'", controls)
        self.assertIn('class AuthRateLimit(db.Model):', models)
        self.assertIn("'auth_rate_limits'", migration)

    def test_security_links_use_trusted_origin_and_tokens_are_not_logged(self):
        app = source('app.py')
        controls = source('security_controls.py')
        users_routes = source('users_routes.py')
        self.assertIn("app.config['PUBLIC_BASE_URL']", app)
        self.assertIn("configured = (current_app.config.get('PUBLIC_BASE_URL')", controls)
        self.assertIn("reset_url = public_url('reset_password'", app)
        self.assertIn("setup_url = public_url('reset_password'", users_routes)
        self.assertNotIn('print(reset_url)', app)
        self.assertNotIn('print(setup_url)', users_routes)

    def test_resource_deletion_removes_tracked_upload_files_safely(self):
        routes = source('projects_routes.py')
        settings = source('settings_routes.py')
        cleanup = source('storage_cleanup.py')
        self.assertIn('stored_paths = collect_project_files(project_id)', routes)
        self.assertIn('stored_paths = collect_division_files(division_id)', routes)
        self.assertIn('stored_paths = collect_line_files(line_id)', routes)
        self.assertGreaterEqual(routes.count('_cleanup_deleted_files('), 6)
        self.assertIn('delete_stored_files(stored_paths)', settings)
        self.assertIn("not raw.startswith('uploads/')", cleanup)
        self.assertIn("os.path.commonpath([uploads_root, absolute])", cleanup)

    def test_project_creator_is_preserved_when_user_is_deleted(self):
        models = source('models.py')
        users = source('users.py')
        migration = source('migrations/versions/20260825_0003_auth_hardening_and_creator_fk.py')
        self.assertIn("db.ForeignKey('users.id', ondelete='SET NULL')", models)
        self.assertIn('Project.query.filter_by(created_by=user.id).update(', users)
        self.assertIn("_replace_project_creator_fk(ondelete='SET NULL')", migration)

    def test_new_users_receive_one_time_password_setup_link(self):
        users = source('users.py')
        routes = source('users_routes.py')
        template = source('templates/users.html')
        mailer = source('mailer.py')
        self.assertIn('reset_token_expires = datetime.utcnow() + timedelta(hours=24)', users)
        self.assertIn("new_user.pop('_setup_token')", routes)
        self.assertIn("new_user['_setup_url'] = setup_url", routes)
        self.assertNotIn('_generated_password', routes)
        self.assertNotIn('_generated_password', template)
        self.assertIn('one-time password setup link', template)
        self.assertIn('No temporary password has been created or sent.', mailer)

    def test_raw_cleanup_preserves_thermal_and_unprotected_images(self):
        routes = source('projects_routes.py')
        models = source('models.py')
        assistant = source('assistant_api.py')
        self.assertIn('def display_image_path(self):', models)
        self.assertIn('def is_thermal_image(self):', models)
        self.assertIn('if photo.is_thermal_image():', routes)
        self.assertIn('skipped_no_preserved_copy', routes)
        self.assertIn('photo.display_image_path()', routes)
        self.assertIn('photo.display_image_path()', assistant)

    def test_multi_gigabyte_bulk_upload_uses_safe_per_file_queue(self):
        template = source('templates/project_map.html')
        self.assertIn('function uploadTimeoutMs(file)', template)
        self.assertIn('async function uploadWithRetry(', template)
        self.assertIn("attempts = 3", template)
        self.assertIn('function cancelBulkUpload()', template)
        self.assertIn('/already uploaded|duplicate/i', template)
        self.assertIn('Already uploaded files were kept.', template)
        self.assertNotIn('setTimeout(() => controller.abort(), 45000)', template)
        routes = source('projects_routes.py')
        models = source('models.py')
        migration = source('migrations/versions/20260825_0004_corridor_upload_deduplication.py')
        self.assertIn('CorridorPhoto.query.filter_by(line_id=line_id, content_hash=content_hash)', routes)
        self.assertIn('content_hash = db.Column(db.String(64)', models)
        self.assertIn("revision = '20260825_0004'", migration)

    def test_gallery_uses_safe_thumbnails_and_original_lightbox(self):
        routes = source('projects_routes.py')
        models = source('models.py')
        template = source('templates/project_map.html')
        backfill = source('generate_missing_thumbnails.py')
        self.assertIn("im.thumbnail((800, 800)", routes)
        self.assertIn("quality=90", routes)
        self.assertIn("secure_filename(filename) + '.thumb.jpg'", routes)
        self.assertIn("os.path.dirname(raw_dir), 'thumb'", routes)
        self.assertIn('self.thumbnail_path or display_path', models)
        self.assertIn('p.thumbnail_url || p.image_url', template)
        self.assertIn("document.getElementById('lightbox-img').src = photo.image_url", template)
        self.assertIn("typeof photoOrUrl.is_thermal === 'boolean'", template)
        self.assertIn('photos.filter(p => isThermalPhoto(p)', template)
        self.assertIn('CorridorPhoto.query.filter(', backfill)

    def test_admin_progress_dashboard_matches_operational_workflow(self):
        routes = source('settings_routes.py')
        template = source('templates/admin.html')
        self.assertIn("'project_progress': project_progress", routes)
        self.assertIn("'admin_uploaded_towers': photographed", routes)
        self.assertIn("'inspection_not_done_uploaded': inspection_not_done_uploaded", routes)
        self.assertIn("'client_visible': inspected", routes)
        self.assertNotIn("admin_approval", routes)
        self.assertIn('Admin upload → SME inspects images → Inspection Done → visible to Client', template)
        self.assertIn('function renderProjectProgress(rows)', template)
        self.assertIn('Inspection Done / Client Visible', template)

    def test_sme_dashboard_is_a_pending_review_queue(self):
        routes = source('projects_routes.py')
        dashboard = source('templates/sme_dashboard.html')
        project_map = source('templates/project_map.html')
        self.assertIn("entry['review_pending']", routes)
        self.assertIn("entry['pending_tower_labels']", routes)
        self.assertIn("entry['thermal_images']", routes)
        self.assertIn("entry['client_visible_towers']", routes)
        self.assertIn("assignmentFilter = 'pending'", dashboard)
        self.assertIn('Continue Review', dashboard)
        self.assertIn('Thermal Images', dashboard)
        self.assertIn('Total Images', dashboard)
        self.assertIn('Inspection Done releases the tower directly to Client', dashboard)
        self.assertIn('will become visible to the Client immediately', project_map)
        self.assertNotIn('Admin approval', dashboard)

    def test_client_defect_closure_has_history_and_optional_evidence(self):
        models = source('models.py')
        routes = source('projects_routes.py')
        access = source('access_control.py')
        template = source('templates/project_map.html')
        migration = source('migrations/versions/20260825_0005_defect_resolution_history.py')
        self.assertIn('class DefectResolutionEvent(db.Model):', models)
        self.assertIn("'resolution_history':", models)
        self.assertIn("request.files.get('evidence_image')", routes)
        self.assertIn('changed_by_user_id=session.get', routes)
        self.assertIn("uploads/defect_rectifications/", access)
        self.assertIn('function promptForDefectAction(action)', template)
        self.assertIn('Rectification image (optional)', template)
        self.assertIn('function showDefectHistory(id)', template)
        self.assertIn("revision = '20260825_0005'", migration)
        self.assertNotIn('verification pending', template.casefold())

    def test_admin_bulk_upload_monitoring_is_persistent(self):
        models = source('models.py')
        routes = source('projects_routes.py')
        template = source('templates/project_map.html')
        migration = source('migrations/versions/20260825_0006_upload_batch_monitoring.py')
        self.assertIn('class UploadBatch(db.Model):', models)
        self.assertIn('class UploadBatchItem(db.Model):', models)
        self.assertIn("'/api/lines/<int:line_id>/upload-batches'", routes)
        self.assertIn("'/api/upload-batches/<int:batch_id>/errors.csv'", routes)
        self.assertIn('function recordUploadOutcome(', template)
        self.assertIn('Upload Status &amp; History', template)
        self.assertIn('Tower-wise image counts', template)
        self.assertIn("revision = '20260825_0006'", migration)

    def test_sme_review_uses_tower_level_completion_without_image_marks(self):
        models = source('models.py')
        routes = source('projects_routes.py')
        template = source('templates/project_map.html')
        migration = source('migrations/versions/20260825_0007_photo_review_progress.py')
        self.assertIn('review_outcome = db.Column', models)
        self.assertIn("'/api/tower-photos/<int:photo_id>/review'", routes)
        self.assertNotIn("Cannot mark Inspection Done:", routes)
        self.assertNotIn('toggleCurrentPhotoReviewed()', template)
        self.assertNotIn('Mark Reviewed', template)
        self.assertNotIn("data.get('confirm_pending')", routes)
        self.assertNotIn('Continue pending', template)
        self.assertIn('Reviewed - No Defect', routes)
        self.assertNotIn('Reviewed – No thermal issue', template)
        self.assertNotIn('tp-photo-review-badge', template[template.index('function photoItemHtml'):])
        self.assertIn('Inspection Done releases the tower directly to Client', source('templates/sme_dashboard.html'))
        self.assertIn("revision = '20260825_0007'", migration)
        corridor_body = models[models.index('class CorridorPhoto'):models.index('class TowerPhoto')]
        tower_body = models[models.index('class TowerPhoto'):models.index('class UploadBatch')]
        self.assertNotIn('review_outcome = db.Column', corridor_body)
        self.assertIn('review_outcome = db.Column', tower_body)
        self.assertIn("thermal = self.is_thermal_image()", tower_body)
        self.assertIn("'sme_email':", models)

    def test_global_notifications_are_targeted_and_database_only(self):
        models = source('models.py')
        routes = source('projects_routes.py')
        app = source('app.py')
        api = source('notification_routes.py')
        service = source('notification_service.py')
        base = source('templates/base.html')
        migration = source('migrations/versions/20260825_0008_in_app_notifications.py')
        self.assertIn('class UserNotification(db.Model):', models)
        self.assertIn("app.register_blueprint(notification_bp)", app)
        self.assertIn("'/api/notifications'", api)
        self.assertIn("'/api/notifications/read'", api)
        self.assertIn('def notify_user(', service)
        self.assertIn("New line assigned", routes)
        self.assertIn("Images ready for review", routes)
        self.assertIn("Critical defect", routes)
        self.assertIn('openGlobalNotifications()', base)
        self.assertIn('Mark all read', base)
        self.assertIn("revision = '20260825_0008'", migration)
        self.assertNotIn('threading', service)

    def test_ai_inspection_summaries_require_valid_sources_and_admin_release(self):
        models = source('models.py')
        routes = source('projects_routes.py')
        template = source('templates/defects_by_tower.html')
        project_map = source('templates/project_map.html')
        migration = source('migrations/versions/20260825_0009_ai_inspection_summaries.py')
        self.assertIn('class AiInspectionSummary(db.Model):', models)
        self.assertIn("if value in allowed_ids", routes)
        self.assertIn("without a valid source reference", routes)
        self.assertIn("guard = _admin_guard()", routes)
        self.assertIn("summary.is_shared = False", routes)
        self.assertIn("session.get('role') == 'Client User' and not summary.is_shared", routes)
        self.assertIn('Share with Client', template)
        self.assertIn('View source', template)
        self.assertIn('Optional — it does not affect Inspection Done', template)
        self.assertNotIn('AI Inspection Summary', project_map)
        self.assertIn("revision = '20260825_0009'", migration)
        self.assertNotIn('genai.Client(api_key=api_key).models.generate_content', routes)
        self.assertIn('client = genai.Client(api_key=api_key)', routes)
        self.assertIn('client.close()', routes)
        self.assertIn('client.close()', source('assistant_api.py'))

    def test_project_navigation_is_always_division_first(self):
        routes = source('projects_routes.py')
        projects_js = source('static/js/projects.js')
        divisions = source('templates/project_divisions.html')
        project_map = source('templates/project_map.html')
        app = source('app.py')
        self.assertIn("`/projects/${p.id}/divisions`", projects_js)
        self.assertIn('return redirect(url_for(\'projects_bp.project_divisions\'', routes)
        self.assertIn("selected_division=selected_division", routes)
        self.assertIn("?division=${d.id}", divisions)
        self.assertIn('const SELECTED_DIVISION_ID =', project_map)
        self.assertIn('find(d => String(d.id) === String(SELECTED_DIVISION_ID))', project_map)
        self.assertIn('openAddLineModal({{ selected_division.id }})', project_map)
        self.assertNotIn('onclick="openAddDivisionModal()">+ Division', project_map)
        self.assertIn("'map_url': url_for('projects_bp.project_divisions'", app)

    def test_approved_navigation_and_state_foundation_is_shared(self):
        base = source('templates/base.html')
        project_map = source('templates/project_map.html')
        self.assertIn('app-breadcrumb', base)
        self.assertIn('drogo-map-state:', project_map)
        self.assertIn('goToAdjacentTower(', project_map)
        self.assertIn('pm-tower-status-filter', project_map)

    def test_annotation_deletion_is_recoverable_and_audited(self):
        models = source('models.py')
        routes = source('projects_routes.py')
        assistant = source('assistant_api.py')
        migration = source('migrations/versions/20260827_0011_inspection_workflow_foundation.py')
        self.assertIn('class DefectAnnotationEvent', models)
        self.assertIn('deleted_at     = db.Column', models)
        self.assertIn("_annotation_event(defect, 'delete'", routes)
        self.assertIn("'/api/tower-defects/<int:defect_id>/restore'", routes)
        self.assertIn('Restore this annotation before changing its resolution.', routes)
        self.assertGreaterEqual(assistant.count('TowerDefect.deleted_at.is_(None)'), 3)
        self.assertIn("revision = '20260827_0011'", migration)

    def test_annotation_updates_use_optimistic_versioning(self):
        routes = source('projects_routes.py')
        self.assertIn("methods=['PATCH']", routes)
        self.assertIn('This annotation was changed by another user', routes)
        self.assertIn('version must be an integer.', routes)
        self.assertIn('_taxonomy_selection_error(defect.component_name, defect.defect_type)', routes)
        self.assertIn("_annotation_event(defect, 'update'", routes)

    def test_seed_script_does_not_embed_demo_credentials(self):
        seed = source('seed_db.py')
        self.assertNotIn("'password':", seed)
        self.assertNotIn('Demo credentials', seed)
        self.assertNotIn('admin123', seed)

    def test_legacy_photo_rows_are_counted_until_explicitly_deleted(self):
        routes = source('projects_routes.py')
        self.assertIn('TowerPhoto.raw_deleted.is_not(True)', routes)

    def test_taxonomy_is_admin_managed_and_applied_to_new_annotations(self):
        settings = source('settings_routes.py')
        settings_js = source('static/js/settings.js')
        project_map = source('templates/project_map.html')
        self.assertIn("'/api/settings/inspection-taxonomy'", settings)
        self.assertIn('InspectionComponent.query', settings)
        self.assertIn('installTaxonomyManager()', settings_js)
        self.assertIn('loadInspectionTaxonomyOptions()', project_map)

    def test_inspection_done_and_quality_dashboard_are_tower_level(self):
        routes = source('projects_routes.py')
        project_map = source('templates/project_map.html')
        settings = source('settings_routes.py')
        quality = source('templates/inspection_quality.html')
        self.assertNotIn('pending_photo_ids', routes)
        self.assertNotIn('Cannot mark Inspection Done:', routes)
        self.assertNotIn('Mark Reviewed', project_map)
        self.assertIn("'/api/inspection-quality'", settings)
        self.assertIn("'image_review_required': False", settings)
        self.assertIn('Normal images do not require a separate review mark.', quality)
        self.assertIn('Inspection Not Done', quality)


if __name__ == '__main__':
    unittest.main()
