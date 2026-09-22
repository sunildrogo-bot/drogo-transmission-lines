import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


class Update28DuplicateAndReviewFlowTests(unittest.TestCase):
    def test_uploads_reject_exact_line_level_duplicates(self):
        routes = source('projects_routes.py')
        duplicate_service = source('duplicate_photos.py')
        self.assertIn('def _find_existing_photo_by_hash', routes)
        self.assertIn('duplicate_tower', routes)
        self.assertIn('hash_stored_photo(row)', routes)
        self.assertIn('def hash_stored_photo', duplicate_service)

    def test_duplicate_scan_and_cleanup_are_portal_managed(self):
        routes = source('settings_routes.py')
        jobs = source('background_jobs.py')
        settings = source('static/js/settings.js')
        self.assertIn("'/api/settings/duplicate-photos/scan'", routes)
        self.assertIn("'/api/settings/duplicate-photos/cleanup'", routes)
        self.assertIn("'duplicate_photo_scan': _run_duplicate_photo_scan", jobs)
        self.assertIn("'duplicate_photo_cleanup': _run_duplicate_photo_cleanup", jobs)
        self.assertIn('installDuplicatePhotoManager', settings)
        self.assertIn('Remove Safe Copies', settings)

    def test_cleanup_protects_evidence_and_cross_tower_matches(self):
        service = source('duplicate_photos.py')
        self.assertIn('def has_protected_evidence', service)
        self.assertIn('photo.defects', service)
        self.assertIn('photo.thermal_points', service)
        self.assertIn('len(tower_labels) != 1', service)
        self.assertIn('len(protected) > 1', service)

    def test_lightbox_exits_only_explicitly_and_notifies_at_end(self):
        page = source('templates/project_map.html')
        self.assertNotIn('onclick="if(event.target===this) closePhotoLightbox()"', page)
        self.assertIn('function notifyImageSequenceComplete()', page)
        self.assertIn('nextButton.disabled = currentPhotoIndex === currentFilteredPhotos.length - 1', page)
        self.assertNotIn('(currentPhotoIndex + 1) % currentFilteredPhotos.length', page)

    def test_defect_form_suppresses_credential_autofill(self):
        page = source('templates/project_map.html')
        self.assertIn('id="defect-details-form" autocomplete="off"', page)
        self.assertIn('name="inspection_component"', page)
        self.assertIn('name="inspection_defect_search"', page)
        self.assertIn('data-lpignore="true"', page)
        self.assertIn('data-1p-ignore="true"', page)


if __name__ == '__main__':
    unittest.main()
