"""Runtime regression tests for Update 30 Phase 1 reliability safeguards."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from app import create_app
from models import BackgroundJob, User, UserNotification, db

ROOT = Path(__file__).resolve().parents[1]


class FakeS3Client:
    def __init__(self):
        self.objects = {}

    def upload_file(self, source, bucket, key, **_kwargs):
        self.objects[(bucket, key)] = Path(source).read_bytes()

    def download_file(self, bucket, key, destination):
        Path(destination).write_bytes(self.objects[(bucket, key)])

    def head_object(self, Bucket, Key):
        return {'ContentLength': len(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **_kwargs):
        self.objects[(Bucket, Key)] = bytes(Body)

    def list_objects_v2(self, Bucket, Prefix, Delimiter=None, **_kwargs):
        keys = sorted(key for bucket, key in self.objects if bucket == Bucket and key.startswith(Prefix))
        if Delimiter:
            prefixes = sorted({Prefix + key[len(Prefix):].split(Delimiter, 1)[0] + Delimiter
                               for key in keys if Delimiter in key[len(Prefix):]})
            return {'CommonPrefixes': [{'Prefix': value} for value in prefixes], 'IsTruncated': False}
        return {'Contents': [{'Key': key} for key in keys], 'IsTruncated': False}

    def delete_objects(self, Bucket, Delete):
        for item in Delete.get('Objects', []):
            self.objects.pop((Bucket, item['Key']), None)


class Update30Phase1Tests(unittest.TestCase):
    @staticmethod
    def _dispose_app(app):
        """Release pooled SQLite handles before Windows removes temp files."""
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

    def test_clean_database_migrates_to_head_with_named_reviewer_fk(self):
        with tempfile.TemporaryDirectory(prefix='drogo_migration_') as directory:
            database = Path(directory) / 'fresh.db'
            environment = os.environ.copy()
            environment.update({
                'DATABASE_URL': f'sqlite:///{database}',
                'DEPLOYMENT_ENV': 'development',
                'SQLITE_JOURNAL_MODE': 'WAL',
            })
            completed = subprocess.run(
                [sys.executable, '-m', 'flask', '--app', 'app:app', 'db', 'upgrade'],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            repeated = subprocess.run(
                [sys.executable, '-m', 'flask', '--app', 'app:app', 'db', 'upgrade'],
                cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120, check=False,
            )
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            with closing(sqlite3.connect(database)) as connection:
                revision = connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
                keys = connection.execute('PRAGMA foreign_key_list(tower_photos)').fetchall()
            self.assertEqual(revision, '20260901_0014')
            self.assertTrue(any(row[2] == 'users' and row[3] == 'reviewed_by_user_id' for row in keys))

    def test_sqlite_connections_enforce_integrity_wal_and_busy_timeout(self):
        with tempfile.TemporaryDirectory(prefix='drogo_sqlite_') as directory:
            app = create_app(f"sqlite:///{Path(directory) / 'runtime.db'}")
            try:
                with app.app_context():
                    db.create_all()
                    values = {
                        'foreign_keys': db.session.execute(db.text('PRAGMA foreign_keys')).scalar(),
                        'journal_mode': db.session.execute(db.text('PRAGMA journal_mode')).scalar(),
                        'busy_timeout': db.session.execute(db.text('PRAGMA busy_timeout')).scalar(),
                    }
                    self.assertEqual(values['foreign_keys'], 1)
                    self.assertEqual(str(values['journal_mode']).casefold(), 'wal')
                    self.assertGreaterEqual(int(values['busy_timeout']), 30000)
            finally:
                self._dispose_app(app)

    def test_database_cascade_and_set_null_are_effective(self):
        with tempfile.TemporaryDirectory(prefix='drogo_cascade_') as directory:
            app = create_app(f"sqlite:///{Path(directory) / 'cascade.db'}")
            try:
                with app.app_context():
                    db.create_all()
                    user = User(username='Integrity User', email='integrity@test.invalid',
                                password_hash='x', status='Active')
                    db.session.add(user); db.session.flush()
                    notification = UserNotification(user_id=user.id, title='Test', message='Test')
                    job = BackgroundJob(job_type='test', created_by_user_id=user.id, created_by_name=user.username)
                    db.session.add_all([notification, job]); db.session.commit()
                    user_id, job_id = user.id, job.id
                    db.session.execute(db.text('DELETE FROM users WHERE id = :id'), {'id': user_id})
                    db.session.commit(); db.session.expire_all()
                    self.assertEqual(UserNotification.query.filter_by(user_id=user_id).count(), 0)
                    self.assertIsNone(db.session.get(BackgroundJob, job_id).created_by_user_id)
            finally:
                self._dispose_app(app)

    def test_integrity_repair_backs_up_and_repairs_safe_orphans(self):
        from scripts.check_data_integrity import _backup_database, _repair, _violations

        with tempfile.TemporaryDirectory(prefix='drogo_integrity_') as directory:
            database = Path(directory) / 'integrity.db'
            with closing(sqlite3.connect(database)) as connection:
                connection.executescript('''
                    PRAGMA foreign_keys=OFF;
                    CREATE TABLE parent (id INTEGER PRIMARY KEY);
                    CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER,
                        FOREIGN KEY(parent_id) REFERENCES parent(id) ON DELETE SET NULL);
                    INSERT INTO child(id, parent_id) VALUES (1, 999);
                ''')
            app = create_app(f'sqlite:///{database}')
            try:
                with app.app_context():
                    with db.engine.connect() as connection:
                        before = _violations(connection)
                    self.assertEqual(len(before), 1)
                    backup, digest = _backup_database(database)
                    self.assertTrue(backup.is_file())
                    self.assertEqual(len(digest), 64)
                    with db.engine.begin() as connection:
                        repaired, manual = _repair(connection, before)
                    with db.engine.connect() as connection:
                        after = _violations(connection)
                    self.assertEqual((repaired, manual, len(after)), (1, 0, 0))
            finally:
                self._dispose_app(app)

    def test_production_rejects_development_defaults(self):
        unsafe = {
            'DEPLOYMENT_ENV': 'production',
            'SECRET_KEY': 'nova-plus-dev-secret-CHANGE-IN-PROD',
            'PUBLIC_BASE_URL': 'http://127.0.0.1:5000',
            'SESSION_COOKIE_SECURE': 'false',
        }
        with (
            mock.patch.dict(os.environ, unsafe, clear=False),
            self.assertRaisesRegex(RuntimeError, 'Unsafe production configuration'),
        ):
            create_app('sqlite:///:memory:')

    def test_production_accepts_explicit_secure_configuration(self):
        secure = {
            'DEPLOYMENT_ENV': 'production',
            'SECRET_KEY': 'phase1-test-secret-that-is-longer-than-thirty-two-characters',
            'PUBLIC_BASE_URL': 'https://portal.example.test',
            'SESSION_COOKIE_SECURE': 'true',
        }
        with mock.patch.dict(os.environ, secure, clear=False):
            app = create_app('sqlite:///:memory:')
        self.assertTrue(app.config['SESSION_COOKIE_SECURE'])

    def test_deployment_defines_web_and_persistent_worker(self):
        supervisor = (ROOT / 'production_start.py').read_text(encoding='utf-8')
        dockerfile = (ROOT / 'Dockerfile').read_text(encoding='utf-8')
        procfile = (ROOT / 'Procfile').read_text(encoding='utf-8')
        self.assertIn("_start('web'", supervisor)
        self.assertIn("_start('worker'", supervisor)
        self.assertIn('worker failed 5 times', supervisor)
        self.assertIn('CMD ["python", "production_start.py"]', dockerfile)
        self.assertIn('worker: python job_worker.py', procfile)

    @unittest.skipIf(os.name == 'nt', 'Production container signal behavior is verified on POSIX.')
    def test_production_supervisor_stops_both_children(self):
        sleeper = f'{sys.executable} -c "import time; time.sleep(30)"'
        environment = os.environ.copy()
        environment.update({
            'WEB_COMMAND': sleeper,
            'WORKER_COMMAND': sleeper,
            'RUN_MIGRATIONS_ON_START': 'false',
        })
        process = subprocess.Popen(
            [sys.executable, 'production_start.py'], cwd=ROOT, env=environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            time.sleep(0.8)
            process.terminate()
            output, _ = process.communicate(timeout=10)
        finally:
            if process.poll() is None:
                process.kill()
        self.assertEqual(process.returncode, 0, output)
        self.assertIn('[supervisor] stopping web', output)
        self.assertIn('[supervisor] stopping worker', output)

    def test_worker_once_releases_lease_and_marks_service_stopped(self):
        with tempfile.TemporaryDirectory(prefix='drogo_worker_') as directory:
            root = Path(directory)
            database = root / 'worker.db'
            instance = root / 'instance'
            environment = os.environ.copy()
            environment.update({
                'DATABASE_URL': f'sqlite:///{database}',
                'INSTANCE_PATH': str(instance),
                'DEPLOYMENT_ENV': 'development',
                'BACKUP_OFFSITE_ENABLED': 'false',
            })
            with mock.patch.dict(os.environ, environment, clear=True):
                app = create_app()
                try:
                    with app.app_context():
                        db.create_all()
                finally:
                    self._dispose_app(app)
            completed = subprocess.run(
                [sys.executable, 'job_worker.py', '--once'], cwd=ROOT, env=environment,
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertFalse((instance / 'background_worker.lock').exists())
            with closing(sqlite3.connect(database)) as connection:
                status = connection.execute(
                    "SELECT status FROM service_heartbeats WHERE service_type='worker'"
                ).fetchone()[0]
            self.assertEqual(status, 'Stopped')

    def test_offsite_checkpoint_streams_and_restore_verifies(self):
        from offsite_backup import run_checkpoint

        settings = {
            'BACKUP_OFFSITE_ENABLED': 'true',
            'BACKUP_OFFSITE_BUCKET': 'phase1-test-bucket',
            'BACKUP_OFFSITE_PREFIX': 'verified-backups',
            'BACKUP_OFFSITE_RETENTION_COUNT': '2',
            'BACKUP_OFFSITE_VERIFY_EVERY_FILE': 'true',
        }
        with tempfile.TemporaryDirectory(prefix='drogo_offsite_') as directory:
            root = Path(directory)
            app = create_app(f"sqlite:///{root / 'offsite.db'}")
            app.static_folder = str(root / 'static')
            app.instance_path = str(root / 'instance')
            (root / 'static' / 'uploads' / 'sample').mkdir(parents=True)
            (root / 'instance').mkdir(parents=True)
            (root / 'static' / 'uploads' / 'sample' / 'image.jpg').write_bytes(b'phase-1-image')
            fake = FakeS3Client()
            try:
                with mock.patch.dict(os.environ, settings, clear=False), app.app_context():
                    db.create_all()
                    job = BackgroundJob(job_type='offsite_backup', status='Processing', created_by_name='Admin')
                    db.session.add(job); db.session.commit()
                    result = run_checkpoint(job, client=fake)
            finally:
                self._dispose_app(app)
            self.assertTrue(result['restore_verified'])
            self.assertEqual(result['upload_count'], 1)
            self.assertEqual(result['upload_bytes'], len(b'phase-1-image'))
            self.assertTrue(any(key.endswith('/backup_manifest.json') for _bucket, key in fake.objects))

    def test_offsite_retention_deletes_only_old_checkpoint_prefix(self):
        from offsite_backup import prune_old_checkpoints

        fake = FakeS3Client()
        bucket = 'phase1-test-bucket'
        checkpoint_ids = (
            '20260901_010101_aaaaaaaa',
            '20260902_010101_bbbbbbbb',
            '20260903_010101_cccccccc',
        )
        for checkpoint_id in checkpoint_ids:
            fake.objects[(bucket, f'verified-backups/checkpoints/{checkpoint_id}/backup_manifest.json')] = b'{}'
        with mock.patch.dict(os.environ, {'BACKUP_OFFSITE_PREFIX': 'verified-backups'}, clear=False):
            result = prune_old_checkpoints(fake, bucket, keep=2)
        self.assertEqual(result, {'removed_checkpoints': 1, 'removed_objects': 1})
        remaining = {key for stored_bucket, key in fake.objects if stored_bucket == bucket}
        self.assertFalse(any(checkpoint_ids[0] in key for key in remaining))
        self.assertTrue(any(checkpoint_ids[1] in key for key in remaining))
        self.assertTrue(any(checkpoint_ids[2] in key for key in remaining))


if __name__ == '__main__':
    unittest.main()
