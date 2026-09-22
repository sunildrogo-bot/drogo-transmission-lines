import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Update25StorageAndCleanupTests(unittest.TestCase):
    def text(self, relative):
        return (ROOT / relative).read_text(encoding='utf-8')

    def test_storage_control_centre_keeps_every_core_function(self):
        page = self.text('templates/settings.html')
        script = self.text('static/js/settings.js')
        for marker in ('uploads-overview-grid', 'all-projects-grid', 'storage-tool-grid',
                       'storage-health-title', 'storage-meter-fill'):
            self.assertIn(marker, page)
        for marker in ('Backup & recovery', 'Object storage', 'Thumbnail repair',
                       'Image metadata', 'Version & compatibility', 'Deployment health'):
            self.assertIn(marker, script)

    def test_project_management_cards_include_real_counts(self):
        routes = self.text('settings_routes.py')
        script = self.text('static/js/settings.js')
        for marker in ("'division_count'", "'line_count'", "'tower_count'"):
            self.assertIn(marker, routes)
        self.assertIn('project-data-stats', script)
        self.assertIn('openTowerBrowser', script)
        self.assertIn('requestDelete', script)

    def test_transmission_cover_is_local_and_consistent(self):
        projects = self.text('static/js/projects.js')
        divisions = self.text('templates/project_divisions.html')
        self.assertIn('/static/images/marketing_rgb.jpg', projects)
        self.assertIn("filename='images/marketing_rgb.jpg'", divisions)
        self.assertNotIn('landsurvey_Cover.png', projects)
        self.assertNotIn('landsurvey_Cover.png', divisions)

    def test_retired_chimney_source_assets_are_absent(self):
        retired = (
            'static/js/chimney.js',
            'static/js/chimney_viewer.js',
            'static/images/chimney_cover_default.png',
            'migrate_add_defect_columns.py',
            'USE drogo_aerospace;.sql',
            'templates/USE drogo_aerospace;.sql',
        )
        for relative in retired:
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_upgrade_removes_only_retired_source_not_uploaded_data(self):
        upgrade = self.text('scripts/upgrade_windows.ps1')
        self.assertIn('$obsoleteSourceFiles', upgrade)
        self.assertIn("'static\\js\\chimney.js'", upgrade)
        self.assertNotIn("'static\\uploads\\chimney", upgrade)

    def test_database_migrations_remain_untouched(self):
        migrations = list((ROOT / 'migrations' / 'versions').glob('*.py'))
        self.assertTrue(migrations)
        self.assertTrue(any('20260901_0014' in path.name for path in migrations))


if __name__ == '__main__':
    unittest.main()
