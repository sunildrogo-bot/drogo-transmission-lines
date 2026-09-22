"""Update 31: stale image responses, map cache edits and tower re-release."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import zipfile

from PIL import Image
from models import db, Line, Project, TowerPhoto, TowerInspectionStatus, AiInspectionSummary
import test_workflow_end_to_end as workflow_fixture


class Update31Tests(unittest.TestCase):
    setUp = workflow_fixture.InspectionWorkflowEndToEndTests.setUp
    tearDown = workflow_fixture.InspectionWorkflowEndToEndTests.tearDown
    _role = workflow_fixture.InspectionWorkflowEndToEndTests._role
    _post = workflow_fixture.InspectionWorkflowEndToEndTests._post

    def test_new_upload_revokes_release_until_sme_completes_again(self):
        self._role('SME', self.ids['sme'])
        status_url = f"/api/lines/{self.ids['line']}/towers/T1/inspection-status"
        self.assertEqual(self._post(status_url, json={'inspection_done': True}).status_code, 200)
        with self.app.app_context():
            db.session.add(AiInspectionSummary(line_id=self.ids['line'], tower_label='T1', is_shared=True))
            db.session.commit()
        self._role('Admin', self.ids['admin'])
        image = io.BytesIO(); Image.new('RGB', (100, 100), 'red').save(image, 'JPEG'); image.seek(0)
        response = self._post(f"/api/lines/{self.ids['line']}/tower-photos", data={'tower': 'T1', 'image': (image, 'new.JPG')})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        new_id = response.json['id']
        with self.app.app_context():
            self.assertFalse(TowerInspectionStatus.query.one().inspection_done)
            self.assertFalse(AiInspectionSummary.query.one().is_shared)
        self._role('Client User', self.ids['client'])
        self.assertIn(self.http.get(f'/api/tower-photos/{new_id}/image').status_code, (403, 404))
        self._role('SME', self.ids['sme'])
        self.assertEqual(self._post(status_url, json={'inspection_done': True}).status_code, 200)
        self._role('Client User', self.ids['client'])
        response = self.http.get(f'/api/tower-photos/{new_id}/image')
        self.assertEqual(response.status_code, 200)
        response.close()

    def test_failed_upload_keeps_completed_tower(self):
        self._role('SME', self.ids['sme'])
        self._post(f"/api/lines/{self.ids['line']}/towers/T1/inspection-status", json={'inspection_done': True})
        self._role('Admin', self.ids['admin'])
        response = self._post(f"/api/lines/{self.ids['line']}/tower-photos", data={'tower':'T1', 'image':(io.BytesIO(b'bad'), 'bad.JPG')})
        self.assertGreaterEqual(response.status_code, 400)
        with self.app.app_context():
            self.assertTrue(TowerInspectionStatus.query.one().inspection_done)

    def _check_map_edit(self, kmz=False):
        self._role('Admin', self.ids['admin'])
        source = b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>T1</name><Point><coordinates>78,17,0</coordinates></Point></Placemark></Document></kml>'
        key = 'uploads/map.kmz' if kmz else 'uploads/map.kml'
        path = Path(self.app.static_folder) / key
        if kmz:
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('doc.kml', source); archive.writestr('icon.txt', b'preserve me')
        else: path.write_bytes(source)
        with self.app.app_context():
            db.session.get(Line, self.ids['line']).kml_path = key; db.session.commit()
        url = f"/api/lines/{self.ids['line']}/geojson"
        self.assertEqual(self.http.get(url).json['features'][0]['geometry']['coordinates'], [78,17])
        for lat, lng in [(18,79),(19,80)]:
            response = self._post(f"/api/lines/{self.ids['line']}/kml-edit", json={'points':[{'label':'T1','lat':lat,'lng':lng}], 'lines':[]})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            response = self.http.get(url)
            self.assertEqual(response.json['features'][0]['geometry']['coordinates'], [lng,lat])
            self.assertIn('no-store', response.headers['Cache-Control'])
        if kmz:
            with self.app.app_context():
                current_key = db.session.get(Line, self.ids['line']).kml_path
            with zipfile.ZipFile(Path(self.app.static_folder) / current_key) as archive:
                self.assertEqual(archive.read('icon.txt'), b'preserve me')

    def test_kml_edits_refresh_existing_geojson(self):
        self._check_map_edit()

    def test_kmz_edits_preserve_assets_and_refresh_geojson(self):
        self._check_map_edit(kmz=True)

    def test_preserved_original_used_by_summary_client_and_report(self):
        self._role('SME', self.ids['sme'])
        response = self._post(f"/api/tower-photos/{self.ids['photo']}/defects", json={
            'shape_type':'rect','shape_coords':[{'x':10,'y':10},{'x':40,'y':40}],
            'component_name':'Insulator','defect_type':'Damage','severity':'Major','location':'Top','status':'Damaged'})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        with self.app.app_context():
            photo = db.session.get(TowerPhoto, self.ids['photo'])
            self.assertTrue(photo.defect_copy_path)
            preserved = photo.defect_copy_path
            os.remove(os.path.join(self.app.static_folder, photo.image_path))
            photo.raw_deleted = True; db.session.commit()
        with self.app.test_request_context('/'):
            from flask import session
            from projects_routes import _build_project_defect_summary
            session.update(user_id=self.ids['admin'], role='Admin')
            summary = _build_project_defect_summary(db.session.get(Project, self.ids['project']))
            rows = summary['defects'] if 'defects' in summary else summary['defect_rows']
            self.assertEqual(rows[0]['image_path'], preserved)
            self.assertIn(preserved, rows[0]['image_url'])
        self._post(f"/api/lines/{self.ids['line']}/towers/T1/inspection-status", json={'inspection_done':True})
        report = self._post(f"/api/lines/{self.ids['line']}/tower-report", json={'tower':'T1'})
        self.assertEqual(report.status_code, 201, report.get_data(as_text=True))
        self._role('Client User', self.ids['client'])
        flat = self.http.get(f"/api/lines/{self.ids['line']}/tower-defects-flat?tower=T1")
        self.assertIn(preserved, flat.json['defects'][0]['image_url'])
        project_report = self.http.get(f"/projects/{self.ids['project']}/report")
        self.assertEqual(project_report.status_code, 200, project_report.get_data(as_text=True) if project_report.status_code != 200 else '')
        self.assertIn(b'/Subtype /Image', project_report.data)
        project_report.close()
        response = self.http.get(f"/api/tower-photos/{self.ids['photo']}/image")
        self.assertEqual(response.status_code, 200); response.close()

    @unittest.skipUnless(shutil.which('node'), 'Node is required for viewer race regression.')
    def test_delayed_image_responses_cannot_overwrite_active_photo(self):
        result = subprocess.run(['node', str(Path(__file__).with_name('update31_viewer_race.js'))], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
