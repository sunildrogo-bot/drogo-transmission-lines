import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


class Update26DefectLocationStatusTests(unittest.TestCase):
    def test_location_options_exist_in_form_and_server(self):
        template = source('templates/project_map.html')
        routes = source('projects_routes.py')
        for location in ('Top', 'Middle', 'Bottom', 'Left', 'Right'):
            self.assertIn(f'<option value="{location}">{location}</option>', template)
            self.assertIn(repr(location), routes)
        self.assertIn('Top, Middle, Bottom, Left, Right', routes)

    def test_common_statuses_are_available_for_every_defect(self):
        template = source('templates/project_map.html')
        routes = source('projects_routes.py')
        self.assertIn('COMMON_DEFECT_STATUS_OPTIONS', template)
        self.assertIn('...COMMON_DEFECT_STATUS_OPTIONS', template)
        for status in (
            'OK', 'Missing', 'Damaged', 'Loose', 'Broken', 'Cracked',
            'Corroded', 'Bent', 'Displaced', 'Deformed', 'Burnt',
            'Contaminated', 'Not Connected', 'Needs Attention',
            'Not Applicable',
        ):
            self.assertIn(repr(status), routes)
            self.assertIn(f'<option value="{status}">{status}</option>', template)

    def test_defect_specific_statuses_remain_available(self):
        template = source('templates/project_map.html')
        for status in ('Good', 'Fair', 'Poor', 'Within Limit',
                       'Not Within Limit', 'Required', 'Done'):
            self.assertIn(repr(status), template)

    def test_server_rejects_unknown_status(self):
        routes = source('projects_routes.py')
        self.assertIn('if status not in VALID_DEFECT_STATUSES:', routes)
        self.assertIn('Select a valid defect condition status.', routes)


if __name__ == '__main__':
    unittest.main()
