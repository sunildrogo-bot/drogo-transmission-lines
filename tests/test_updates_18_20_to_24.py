import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Updates18And20To24ContractTests(unittest.TestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def test_gallery_follows_server_cursor(self):
        route = self.text('projects_routes.py'); page = self.text('templates/project_map.html')
        self.assertIn("int(request.args.get('per_page') or 120)", route)
        self.assertIn('while (hasMore && afterId)', page)
        self.assertIn('next_after_id', page)

    def test_activity_log_is_filtered_and_paginated(self):
        routes = self.text('settings_routes.py'); script = self.text('static/js/settings.js')
        self.assertIn('category_terms', routes); self.assertIn("per_page') or 20", routes)
        self.assertIn('loadMoreActivity', script)

    def test_maps_use_local_libraries_and_configurable_tiles(self):
        base = self.text('templates/base.html'); app = self.text('app.py')
        self.assertIn("vendor/leaflet/leaflet.js", base); self.assertIn("vendor/pmtiles/pmtiles.js", base)
        self.assertIn("app.config['MAP_TILE_URL']", app); self.assertNotIn('fonts.googleapis.com', base)

    def test_production_health_covers_required_services(self):
        routes = self.text('settings_routes.py')
        for marker in ("details['web']", "details['database']", "details['storage']",
                       "details['worker']", "details['jobs']", "details['map']"):
            self.assertIn(marker, routes)

    def test_repair_centre_is_conservative_and_audited(self):
        routes = self.text('backup_routes.py')
        self.assertIn("len(matches) == 1", routes)
        self.assertIn("action='relink'", routes)
        self.assertIn('/api/settings/data-health/issues.csv', routes)

    def test_release_registry_includes_every_approved_update(self):
        from feature_registry import source_compatibility
        report = source_compatibility(ROOT)
        self.assertEqual(report['version'], '1.28.0')
        self.assertTrue(report['compatible'])
        self.assertEqual(report['missing'], 0)

    def test_upgrade_can_package_pre_registry_rollback_source(self):
        builder = self.text('scripts/build_release.py'); upgrade = self.text('scripts/upgrade_windows.ps1')
        self.assertIn('--allow-incomplete-source', builder)
        self.assertIn('--allow-incomplete-source', upgrade)

    def test_existing_release_manifest_is_excluded_from_repackaging(self):
        from scripts.build_release import is_excluded
        self.assertTrue(is_excluded('RELEASE_MANIFEST.json'))
        self.assertTrue(is_excluded('nested/RELEASE_MANIFEST.json'))


if __name__ == '__main__':
    unittest.main()
