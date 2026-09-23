"""Update 33: completed-tower storage cleanup keeps RGB and thermal findings."""
import os
from pathlib import Path
import unittest

from PIL import Image

import settings as app_settings
from models import (
    AiInspectionSummary,
    ThermalPoint,
    TowerDefect,
    TowerInspectionStatus,
    TowerPhoto,
    TowerReport,
    db,
)
import test_workflow_end_to_end as workflow_fixture


class TowerStorageRetentionTests(unittest.TestCase):
    setUp = workflow_fixture.InspectionWorkflowEndToEndTests.setUp
    tearDown = workflow_fixture.InspectionWorkflowEndToEndTests.tearDown
    _role = workflow_fixture.InspectionWorkflowEndToEndTests._role

    def _delete(self, url, payload):
        return self.http.delete(url, json=payload, headers={'X-CSRF-Token': self.csrf})

    def _prepare_tower(self, completed=True):
        with self.app.app_context():
            raw_dir = Path(self.app.static_folder) / 'uploads/tower_photos/test/raw'
            thermal_key = 'uploads/tower_photos/test/raw/T1_T.JPG'
            clean_key = 'uploads/tower_photos/test/raw/T1_CLEAN.JPG'
            clean_thumb_key = 'uploads/tower_photos/test/raw/T1_CLEAN_thumb.JPG'
            Image.new('RGB', (320, 240), '#cc4422').save(Path(self.app.static_folder) / thermal_key, 'JPEG')
            Image.new('RGB', (320, 240), '#2266aa').save(Path(self.app.static_folder) / clean_key, 'JPEG')
            Image.new('RGB', (80, 60), '#2266aa').save(Path(self.app.static_folder) / clean_thumb_key, 'JPEG')

            rgb = db.session.get(TowerPhoto, self.ids['photo'])
            defect = TowerDefect(
                photo=rgb, shape_type='rect', shape_coords='[{"x":10,"y":10},{"x":30,"y":30}]',
                component_name='Insulator', defect_type='Damage', severity='Major', status='Damaged')
            thermal = TowerPhoto(
                line_id=self.ids['line'], tower_label='T1', image_path=thermal_key,
                media_type='thermal', uploaded_by='Admin Test')
            clean = TowerPhoto(
                line_id=self.ids['line'], tower_label='T1', image_path=clean_key,
                thumbnail_path=clean_thumb_key, media_type='rgb', uploaded_by='Admin Test')
            db.session.add_all([defect, thermal, clean]); db.session.flush()
            point = ThermalPoint(
                tower_photo_id=thermal.id, x_pct=50, y_pct=50, shape_type='point',
                shape_coords='[{"x":50,"y":50}]', temperature_c=42.5,
                min_c=42.5, max_c=42.5, avg_c=42.5, created_by='SME Test')
            status = TowerInspectionStatus(
                line_id=self.ids['line'], tower_label='T1', inspection_done=completed,
                marked_by='SME Test')
            db.session.add_all([point, status])
            app_settings.set_delete_password('test-delete-password')
            db.session.commit()
            return {'rgb': rgb.id, 'thermal': thermal.id, 'clean': clean.id,
                    'thermal_key': thermal_key, 'clean_key': clean_key,
                    'clean_thumb_key': clean_thumb_key}

    def test_raw_cleanup_requires_inspection_done(self):
        self._prepare_tower(completed=False)
        self._role('Admin', self.ids['admin'])
        response = self._delete(
            f"/api/settings/lines/{self.ids['line']}/towers/T1/raw-images",
            {'password': 'test-delete-password'})
        self.assertEqual(response.status_code, 409, response.get_data(as_text=True))
        with self.app.app_context():
            self.assertTrue((Path(self.app.static_folder) /
                             db.session.get(TowerPhoto, self.ids['photo']).image_path).is_file())

    def test_raw_cleanup_preserves_marked_rgb_and_measured_thermal(self):
        ids = self._prepare_tower(completed=True)
        self._role('Admin', self.ids['admin'])

        inventory = self.http.get(f"/api/settings/lines/{self.ids['line']}/towers")
        self.assertEqual(inventory.status_code, 200, inventory.get_data(as_text=True))
        tower = inventory.get_json()['towers'][0]
        self.assertTrue(tower['inspection_done'])
        self.assertEqual(tower['marked_rgb_count'], 1)
        self.assertEqual(tower['marked_thermal_count'], 1)
        self.assertGreater(tower['raw_bytes'], 0)

        response = self._delete(
            f"/api/settings/lines/{self.ids['line']}/towers/T1/raw-images",
            {'password': 'test-delete-password'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.get_json()
        self.assertEqual(result['preserved_rgb_images'], 1)
        self.assertEqual(result['preserved_thermal_images'], 1)
        self.assertEqual(result['warnings'], [])

        with self.app.app_context():
            rgb = db.session.get(TowerPhoto, ids['rgb'])
            thermal = db.session.get(TowerPhoto, ids['thermal'])
            clean = db.session.get(TowerPhoto, ids['clean'])
            for photo in (rgb, thermal, clean):
                self.assertTrue(photo.raw_deleted)
                self.assertFalse((Path(self.app.static_folder) / photo.image_path).exists())
            self.assertTrue(rgb.defect_copy_path)
            self.assertTrue(thermal.defect_copy_path)
            self.assertTrue((Path(self.app.static_folder) / rgb.defect_copy_path).is_file())
            self.assertTrue((Path(self.app.static_folder) / thermal.defect_copy_path).is_file())
            self.assertIsNone(clean.defect_copy_path)
            self.assertIsNone(clean.thumbnail_path)
            self.assertEqual(TowerDefect.query.filter_by(tower_photo_id=rgb.id).count(), 1)
            self.assertEqual(ThermalPoint.query.filter_by(tower_photo_id=thermal.id).count(), 1)

        for photo_id, expected in ((ids['rgb'], 200), (ids['thermal'], 200), (ids['clean'], 404)):
            image_response = self.http.get(f"/api/tower-photos/{photo_id}/image")
            self.assertEqual(image_response.status_code, expected)
            image_response.close()

    def test_data_health_ignores_intentionally_archived_raw_paths(self):
        ids = self._prepare_tower(completed=True)
        self._role('Admin', self.ids['admin'])
        with self.app.app_context():
            raw_paths = {
                db.session.get(TowerPhoto, photo_id).image_path
                for photo_id in (ids['rgb'], ids['thermal'], ids['clean'])
            }

        cleanup = self._delete(
            f"/api/settings/lines/{self.ids['line']}/towers/T1/raw-images",
            {'password': 'test-delete-password'})
        self.assertEqual(cleanup.status_code, 200, cleanup.get_data(as_text=True))

        response = self.http.get('/api/settings/data-health')
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        files = response.get_json()['files']
        self.assertEqual(files['archived_raw_reference_count'], 3)
        self.assertTrue(raw_paths.isdisjoint(files['missing_examples']))
        self.assertTrue(all(
            suggestion['missing'] not in raw_paths
            for suggestion in files['relink_suggestions']))

        csv_response = self.http.get('/api/settings/data-health/issues.csv')
        self.assertEqual(csv_response.status_code, 200, csv_response.get_data(as_text=True))
        csv_text = csv_response.get_data(as_text=True)
        for raw_path in raw_paths:
            self.assertNotIn(raw_path, csv_text)

    def test_findings_cleanup_is_separate_and_invalidates_generated_outputs(self):
        ids = self._prepare_tower(completed=True)
        self._role('Admin', self.ids['admin'])
        raw_response = self._delete(
            f"/api/settings/lines/{self.ids['line']}/towers/T1/raw-images",
            {'password': 'test-delete-password'})
        self.assertEqual(raw_response.status_code, 200, raw_response.get_data(as_text=True))

        with self.app.app_context():
            report_key = 'uploads/tower_reports/update33-test.pdf'
            report_path = Path(self.app.static_folder) / report_key
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_bytes(b'%PDF-1.4 test')
            db.session.add(TowerReport(
                line_id=self.ids['line'], tower_label='T1', report_path=report_key,
                generated_by='Admin Test'))
            db.session.add(AiInspectionSummary(
                line_id=self.ids['line'], tower_label='T1', findings_json='[]', sources_json='[]'))
            db.session.commit()

        response = self._delete(
            f"/api/settings/lines/{self.ids['line']}/towers/T1/findings",
            {'password': 'test-delete-password'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.get_json()
        self.assertEqual(result['deleted_defects'], 1)
        self.assertEqual(result['deleted_thermal_points'], 1)
        self.assertEqual(result['deleted_reports'], 1)
        self.assertEqual(result['deleted_ai_summaries'], 1)

        with self.app.app_context():
            self.assertEqual(TowerDefect.query.count(), 0)
            self.assertEqual(ThermalPoint.query.count(), 0)
            self.assertEqual(TowerReport.query.count(), 0)
            self.assertEqual(AiInspectionSummary.query.count(), 0)
            self.assertIsNone(db.session.get(TowerPhoto, ids['rgb']).defect_copy_path)
            self.assertIsNone(db.session.get(TowerPhoto, ids['thermal']).defect_copy_path)
            self.assertTrue(TowerInspectionStatus.query.one().inspection_done)


if __name__ == '__main__':
    unittest.main()
