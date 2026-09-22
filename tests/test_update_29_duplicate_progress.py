import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative):
    return (ROOT / relative).read_text(encoding='utf-8')


class Update29DuplicateProgressTests(unittest.TestCase):
    def test_job_api_exposes_worker_heartbeat(self):
        model = source('models.py')
        self.assertIn("'heartbeat_at': self.heartbeat_at.isoformat()", model)

    def test_scan_publishes_phase_and_progress_frequently(self):
        service = source('duplicate_photos.py')
        self.assertIn('def _publish_scan_progress', service)
        self.assertIn("'Preparing image inventory'", service)
        self.assertIn("'Fingerprinting stored originals'", service)
        self.assertIn("'Analysing exact duplicate groups'", service)
        self.assertIn('if index % 5 == 0:', service)

    def test_cleanup_publishes_current_phase(self):
        jobs = source('background_jobs.py')
        self.assertIn("'Classifying safe duplicate copies'", jobs)
        self.assertIn("'Removing safe redundant records'", jobs)
        self.assertIn("'Deleting unreferenced stored files'", jobs)

    def test_admin_sees_full_live_statistics(self):
        settings = source('static/js/settings.js')
        page = source('templates/settings.html')
        self.assertIn('function duplicateProgressView(job)', settings)
        self.assertIn('percentage.toFixed(1)', settings)
        self.assertIn('<span>Completed</span>', settings)
        self.assertIn('<span>Total images</span>', settings)
        self.assertIn('<span>Remaining</span>', settings)
        self.assertIn('<span>Estimated wait</span>', settings)
        self.assertIn('images/min', settings)
        self.assertIn('Worker active', settings)
        self.assertIn('duplicate-progress-track', page)

    def test_queued_and_stalled_states_are_explicit(self):
        settings = source('static/js/settings.js')
        self.assertIn("job.status === 'Queued'", settings)
        self.assertIn("heartbeatAge > 90", settings)
        self.assertIn('Waiting for background worker', settings)
        self.assertIn('stopped reporting progress', settings)


if __name__ == '__main__':
    unittest.main()
