import json
import tempfile
import unittest
from pathlib import Path
from write_update_status import make_status

class UpdateStatusTests(unittest.TestCase):
    def test_failed_analysis_keeps_published_cycle_and_hides_raw_error(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.diagnostics').mkdir()
            (root/'latest_run.json').write_text(json.dumps({'init':'2026101700'}))
            (root/'.diagnostics/gefs-analysis.json').write_text(json.dumps({'init':'2026101812','downloadedFields':651,'error':'token=secret'}))
            result=make_status(root,'failure','2026-10-18T21:00:00Z')
            self.assertEqual(result['publishedInit'],'2026101700')
            self.assertEqual(result['init'],'2026101812')
            self.assertEqual(result['failureStage'],'analysis')
            self.assertNotIn('secret',json.dumps(result))
            self.assertEqual(result['status'],'failed')
    def test_failed_validation_uses_published_snapshot_not_uncommitted_candidate(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.diagnostics').mkdir()
            (root/'latest_run.json').write_text('{"init":"2026101812"}')
            (root/'.diagnostics/published-latest.json').write_text('{"init":"2026101700"}')
            self.assertEqual(make_status(root,'failure')['publishedInit'],'2026101700')
            self.assertEqual(make_status(root,'success')['publishedInit'],'2026101812')
    def test_missing_diagnostics_does_not_claim_download(self):
        with tempfile.TemporaryDirectory() as d:
            result=make_status(Path(d),'failure')
            self.assertIsNone(result['downloadedFields'])
            self.assertEqual(result['failureStage'],'acquisition_or_validation')
    def test_success_and_noop_can_publish_health_without_new_forecast(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'latest_run.json').write_text('{"init":"2026101812"}')
            result=make_status(root,'success')
            self.assertEqual(result['status'],'success')
            self.assertEqual(result['publishedInit'],'2026101812')
            self.assertIsNone(result['init'])
    def test_failed_self_test_is_not_reported_as_acquisition_failure(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.diagnostics').mkdir()
            (root/'.diagnostics/published-latest.json').write_text('{"init":"2026102200"}')
            result=make_status(root,'skipped',self_test_outcome='failure')
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['failureStage'],'self_test')
            self.assertEqual(result['publishedInit'],'2026102200')
            self.assertIsNone(result['init'])
            self.assertIsNone(result['downloadedFields'])
            self.assertIn('取得は実行していません',result['message'])
    def test_skipped_self_test_does_not_claim_self_test_failure(self):
        with tempfile.TemporaryDirectory() as d:
            result=make_status(Path(d),'skipped',self_test_outcome='skipped')
            self.assertEqual(result['failureStage'],'acquisition_or_validation')
    def test_successful_self_test_preserves_analysis_failure_stage(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.diagnostics').mkdir()
            (root/'.diagnostics/gefs-analysis.json').write_text('{"downloadedFields":651}')
            result=make_status(root,'failure',self_test_outcome='success')
            self.assertEqual(result['failureStage'],'analysis')
    def test_workflow_passes_self_test_outcome_to_status_writer(self):
        workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/update-latest-gefs.yml').read_text()
        self.assertIn('name: Run offline pipeline self-test\n        id: self_test',workflow)
        self.assertIn('--self-test-outcome "${{ steps.self_test.outcome }}"',workflow)
if __name__=='__main__':unittest.main()
