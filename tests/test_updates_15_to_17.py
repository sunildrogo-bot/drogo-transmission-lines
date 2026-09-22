import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Updates15To17ContractTests(unittest.TestCase):
    def test_release_registry_protects_every_required_feature(self):
        from feature_registry import source_compatibility
        report = source_compatibility(ROOT)
        self.assertTrue(report['compatible'])
        self.assertEqual(report['missing'], 0)
        self.assertGreaterEqual(report['total'], 19)

    def test_reliability_migration_follows_production_foundation(self):
        migration = (ROOT / 'migrations/versions/20260901_0014_release_storage_worker_reliability.py').read_text()
        self.assertIn("down_revision = '20260831_0013'", migration)
        self.assertIn("'service_heartbeats'", migration)
        self.assertIn("'request_metric_buckets'", migration)
        self.assertIn("'application_releases'", migration)

    def test_object_storage_migration_is_worker_backed(self):
        jobs = (ROOT / 'background_jobs.py').read_text()
        routes = (ROOT / 'settings_routes.py').read_text()
        self.assertIn("'storage_migration': _run_storage_migration", jobs)
        self.assertIn("/api/settings/storage-migration", routes)

    def test_monitor_combines_all_web_workers(self):
        monitor = (ROOT / 'load_monitor_shared.py').read_text()
        app = (ROOT / 'app.py').read_text()
        self.assertIn("'scope': 'all_web_workers'", monitor)
        self.assertIn('from load_monitor_shared import record_request', app)


if __name__ == '__main__':
    unittest.main()
