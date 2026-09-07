"""Isolated workflow smoke test. It never connects to the configured database."""
import os
import tempfile
import unittest

try:
    from PIL import Image
    from app import create_app
    from models import (db, Division, Line, Module, Project, Role, SmeAssignment,
                        TowerDefect, TowerInspectionStatus, TowerPhoto, User)
    DEPENDENCIES_AVAILABLE = True
except ImportError:
    DEPENDENCIES_AVAILABLE = False


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, 'Application runtime dependencies are not installed.')
class InspectionWorkflowEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='drogo_workflow_test_')
        root = self.temp.name
        self.app = create_app(f"sqlite:///{os.path.join(root, 'workflow.db')}")
        self.app.config.update(TESTING=True, STORAGE_BACKEND='local', SERVER_NAME='localhost')
        self.app.static_folder = os.path.join(root, 'static')
        self.app.instance_path = os.path.join(root, 'instance')
        os.makedirs(os.path.join(self.app.static_folder, 'uploads', 'tower_photos', 'test', 'raw'), exist_ok=True)
        os.makedirs(self.app.instance_path, exist_ok=True)
        with self.app.app_context():
            db.create_all()
            roles = {name: Role(name=name) for name in ('Admin', 'SME', 'Client User')}
            module = Module(name='TRANS', route='projects')
            db.session.add_all([*roles.values(), module]); db.session.flush()
            self.admin = User(username='Admin Test', email='admin@test.invalid', password_hash='x', status='Active')
            self.sme = User(username='SME Test', email='sme@test.invalid', password_hash='x', status='Active')
            self.client_user = User(username='Client Test', email='client@test.invalid', password_hash='x', status='Active')
            self.admin.roles.append(roles['Admin']); self.sme.roles.append(roles['SME']); self.client_user.roles.append(roles['Client User'])
            self.admin.modules.append(module); self.sme.modules.append(module); self.client_user.modules.append(module)
            project = Project(module='TRANS', name='Workflow Test', inspection_types='["rgb"]')
            division = Division(project=project, name='Test Division', latitude=17.0, longitude=78.0)
            line = Line(division=division, name='Test Line', start_lat=17.0, start_lng=78.0,
                        end_lat=17.1, end_lng=78.1, tower_count=1)
            self.client_user.allowed_projects.append(project)
            db.session.add_all([self.admin, self.sme, self.client_user, project]); db.session.flush()
            db.session.add(SmeAssignment(line_id=line.id, sme_user_id=self.sme.id, assigned_by='Admin Test'))
            image_key = 'uploads/tower_photos/test/raw/T1.JPG'
            Image.new('RGB', (320, 240), '#888888').save(os.path.join(self.app.static_folder, image_key), 'JPEG')
            photo = TowerPhoto(line_id=line.id, tower_label='T1', image_path=image_key,
                               media_type='rgb', uploaded_by='Admin Test')
            db.session.add(photo); db.session.commit()
            self.ids = {'admin': self.admin.id, 'sme': self.sme.id, 'client': self.client_user.id,
                        'project': project.id, 'line': line.id, 'photo': photo.id}
        self.http = self.app.test_client(); self.csrf = 'workflow-test-token'

    def tearDown(self):
        with self.app.app_context(): db.session.remove(); db.drop_all()
        self.temp.cleanup()

    def _role(self, role, user_id):
        with self.http.session_transaction() as session:
            session.clear(); session.update({'user_id': user_id, 'role': role, 'session_version': 1,
                                             '_csrf_token': self.csrf})

    def _post(self, url, **kwargs):
        headers = kwargs.pop('headers', {}); headers['X-CSRF-Token'] = self.csrf
        return self.http.post(url, headers=headers, **kwargs)

    def test_upload_inspect_release_client_close_report_and_dataset_preview(self):
        self._role('SME', self.ids['sme'])
        response = self._post(f"/api/tower-photos/{self.ids['photo']}/defects", json={
            'shape_type': 'rect', 'shape_coords': [{'x': 10, 'y': 10}, {'x': 40, 'y': 40}],
            'component_name': 'Insulator', 'defect_type': 'Damage', 'severity': 'Major',
            'location': 'Top', 'status': 'Damaged', 'comments': 'Workflow regression test'})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        defect_id = response.get_json()['id']

        response = self._post(f"/api/lines/{self.ids['line']}/towers/T1/inspection-status",
                              json={'inspection_done': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(response.get_json()['inspection_done'])

        response = self._post(f"/api/lines/{self.ids['line']}/tower-report",
                              json={'tower': 'T1', 'tower_lat': 17.0, 'tower_lng': 78.0})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertTrue(response.get_json().get('report_url'))

        self._role('Client User', self.ids['client'])
        response = self.http.get(f"/api/tower-photos/{self.ids['photo']}/defects")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(len(response.get_json()['defects']), 1)
        response = self._post(f'/api/tower-defects/{defect_id}/resolve',
                              json={'action': 'close', 'comment': 'Rectification verified by client.'})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(response.get_json()['resolution_status'], 'Closed')

        self._role('Admin', self.ids['admin'])
        response = self._post('/api/training-datasets/preview', json={
            'project_id': self.ids['project'], 'line_ids': [self.ids['line']],
            'format': 'yolo-detection', 'inspection_done_only': True,
            'include_negatives': False, 'labels_only': True})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertGreaterEqual(response.get_json().get('annotation_count', 0), 1)

        with self.app.app_context():
            self.assertTrue(TowerInspectionStatus.query.filter_by(line_id=self.ids['line'], tower_label='T1').one().inspection_done)
            self.assertEqual(db.session.get(TowerDefect, defect_id).resolution_status, 'Closed')


if __name__ == '__main__':
    unittest.main()
