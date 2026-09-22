import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


class Update27DefectReviewViewerTests(unittest.TestCase):
    def test_grading_ring_is_available_with_or_without_managed_taxonomy(self):
        page = source('templates/project_map.html')
        routes = source('projects_routes.py')
        self.assertIn('<option>Grading Ring</option>', page)
        self.assertIn("const UNIVERSAL_DEFECT_TYPES = ['Grading Ring'];", page)
        self.assertIn("SYSTEM_DEFECT_TYPES = {'Grading Ring'}", routes)
        self.assertIn('def _defect_taxonomy_error', routes)

    def test_defect_type_list_is_searchable(self):
        page = source('templates/project_map.html')
        self.assertIn('id="df-defect-type-search"', page)
        self.assertIn('filterDefectTypeOptions(this.value)', page)
        self.assertIn('function renderDefectTypeOptions', page)

    def test_active_defect_can_be_edited_through_versioned_patch(self):
        page = source('templates/project_map.html')
        routes = source('projects_routes.py')
        self.assertIn('onclick="startEditDefect(${d.id})"', page)
        self.assertIn('function startEditDefect(id)', page)
        self.assertIn("method: editId ? 'PATCH' : 'POST'", page)
        self.assertIn("@projects_bp.route('/api/tower-defects/<int:defect_id>', methods=['PATCH'])", routes)
        self.assertIn("expected_version = data.get('version')", routes)
        self.assertIn('_defect_taxonomy_error(component_name, defect_type)', routes)

    def test_deleted_defects_are_not_active_counts_or_markers(self):
        model = source('models.py')
        routes = source('projects_routes.py')
        settings = source('settings_routes.py')
        assistant = source('assistant_api.py')
        page = source('templates/project_map.html')
        self.assertIn('active_defects = [defect for defect in self.defects if not defect.deleted_at]', model)
        self.assertIn("'defect_count': len(active_defects)", model)
        self.assertIn('TowerPhoto.line_id == line_id, TowerDefect.deleted_at.is_(None)', routes)
        self.assertIn('if not defect.deleted_at', settings)
        self.assertGreaterEqual(assistant.count('TowerDefect.deleted_at.is_(None)'), 3)
        self.assertIn('currentPhotoDefects = currentPhotoDefects.filter(item => item.id !== id)', page)

    def test_expanded_statuses_are_validated_in_ui_and_api(self):
        page = source('templates/project_map.html')
        routes = source('projects_routes.py')
        for status in ('Partially Missing', 'Rusted', 'Disconnected', 'Overheated',
                       'Needs Repair', 'Needs Replacement', 'Serviceable',
                       'Unserviceable', 'Unable to Verify'):
            self.assertIn(repr(status), page)
            self.assertIn(repr(status), routes)
        self.assertIn('if status not in VALID_DEFECT_STATUSES:', routes)

    def test_viewer_zoom_pan_and_double_click_are_scoped(self):
        page = source('templates/project_map.html')
        self.assertIn("wrap.addEventListener('wheel'", page)
        self.assertNotIn("lightbox.addEventListener('wheel'", page)
        self.assertIn("wrap.addEventListener('pointerdown'", page)
        self.assertIn("wrap.addEventListener('pointermove'", page)
        self.assertIn("wrap.addEventListener('dblclick'", page)
        self.assertIn('function clampImgPan()', page)
        self.assertIn('draggable="false"', page)
        self.assertIn('user-select:none', page)


if __name__ == '__main__':
    unittest.main()
