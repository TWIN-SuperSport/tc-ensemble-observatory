"""Identity and archive isolation for JMA 31 / JTWC 30W."""
import json
import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path
import build_gefs_data as pipeline
from history_index import target_label

ROOT = Path(__file__).resolve().parents[1]
# Historical fixture: future monitoring switches must not rewrite this regression.
CONFIG = {'storm': 'WP30', 'stormInfo': {'id': '30W', 'trackingTargetId': '2026-10-jtwc-30w', 'status': 'typhoon', 'name': 'IN-FA', 'nameJa': 'インファ', 'number': 31, 'candidateNumber': None, 'jtwcNumber': '30W', 'originBasin': 'WPAC', 'currentBasin': 'WPAC', 'aliases': ['30W', 'IN-FA', 'INFA'], 'continuity': 'JMA Typhoon 31 (TC2637, IN-FA) is JTWC 30W / WP302026. Separate from JMA Typhoon 30 (CHAMPI), JTWC 29W / former Invest 96W. Never inherit prior target seeds, tracks, or identity; retain this trackingTargetId across future status changes.'}, 'trackingTargetId': '2026-10-jtwc-30w'}

class InFaSwitchTests(unittest.TestCase):
    def setUp(self):
        self.config = copy.deepcopy(CONFIG)

    def test_saved_target_mapping(self):
        self.assertEqual(self.config['trackingTargetId'], '2026-10-jtwc-30w')
        self.assertEqual(self.config['stormInfo']['jtwcNumber'], '30W')
        self.assertEqual(self.config['stormInfo']['number'], 31)
        self.assertEqual(target_label(self.config), '台風31号（インファ）')

    def test_saved_official_warning(self):
        text = (ROOT / 'analysis/materials/2026-10-25-1500z-jtwc-30w-warning.txt').read_text()
        self.assertEqual(pipeline.parse_jtwc_named_seed(text, {'30W', 'IN-FA'}), (12.8, 164.5, '30W'))
        self.assertIsNone(pipeline.parse_jtwc_named_seed(text, {'29W', 'CHAMPI'}))

    def test_jma_number_is_not_jtwc_number(self):
        text = (ROOT / 'analysis/materials/2026-10-25-1310z-jma-TC2637-forecast.json').read_text()
        identity = pipeline.parse_jma_forecast_identity('TC2637', text, category='STS')
        self.assertEqual(identity['number'], 31)
        self.assertEqual(identity['nameEn'].upper(), 'IN-FA')

    def test_champi_cannot_supply_seed_or_alias(self):
        previous = json.loads((ROOT / 'history/2026102506.json').read_text())
        self.assertFalse(pipeline.same_tracking_target(previous, self.config))
        aliases = pipeline.official_identity_aliases(previous, self.config)
        self.assertNotIn('29W', aliases)
        self.assertNotIn('CHAMPI', aliases)
        init = datetime(2026, 10, 25, 6, tzinfo=timezone.utc)
        self.assertIsNone(pipeline.previous_forecast_seed(previous, init, aliases, 12, self.config['trackingTargetId']))
        payload = {'meta': {'trackingTargetId': self.config['trackingTargetId']}}
        self.assertNotEqual(pipeline.archive_path_for_payload(payload, init).name, '2026102506.json')

if __name__ == '__main__':
    unittest.main()
