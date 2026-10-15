#!/usr/bin/env python3
"""Offline regressions for the 29W target's display and episode isolation."""
import copy
import unittest
from datetime import datetime, timezone
from pathlib import Path

import build_gefs_data as pipeline
from history_index import target_label


TARGET = "2026-10-jtwc-29w"
CONFIG = {
    "storm": "WP29",
    "trackingTargetId": TARGET,
    "stormInfo": {
        "id": "96W", "jtwcNumber": "29W", "status": "tropical_depression",
        "number": None, "candidateNumber": None,
        "aliases": ["29W", "96W", "TWENTYNINE"],
    },
}


class TargetLabelTests(unittest.TestCase):
    def test_warning_serial_preferred_without_inventing_jma_number(self):
        self.assertEqual(target_label(CONFIG), "29W")

    def test_existing_candidate_remains_explicit(self):
        meta = copy.deepcopy(CONFIG)
        meta["stormInfo"].update(status="candidate", candidateNumber=30)
        self.assertEqual(target_label(meta), "29W（台風30号候補）")

    def test_jma_official_identity_takes_priority(self):
        meta = copy.deepcopy(CONFIG)
        meta["stormInfo"].update(status="typhoon", number=30, nameJa="テスト", name="TEST")
        self.assertEqual(target_label(meta), "台風30号（テスト）")

    def test_old_jma_29_is_still_koguma_not_jtwc_29w(self):
        meta = {"stormInfo": {"id": "94W", "jtwcNumber": "27W", "status": "typhoon", "number": 29, "nameJa": "コグマ"}}
        self.assertEqual(target_label(meta), "台風29号（コグマ）")

    def test_invest_without_warning_keeps_invest_label(self):
        self.assertEqual(target_label({"stormInfo": {"id": "96W"}}), "96W")


class TargetIsolationTests(unittest.TestCase):
    def test_old_episodes_cannot_supply_aliases_or_previous_seed(self):
        init = datetime(2026, 10, 15, tzinfo=timezone.utc)
        for episode, storm_id, aliases in [
            ("2026-10-invest-95w", "95W", ["95W", "28W", "TWENTYEIGHT"]),
            ("2026-10-invest-94w", "94W", ["94W", "27W", "KOGUMA"]),
            ("2026-08-invest-96w", "96W", ["96W", "14W"]),
        ]:
            previous = {"meta": {
                "trackingTargetId": episode, "init": "2026101412",
                "stormInfo": {"id": storm_id, "aliases": aliases},
                "trackingIdentity": {"status": "verified"},
            }, "tracks": [{"cluster": "C1", "points": [{"fhour": 12, "lat": 15, "lon": 140}]}]}
            with self.subTest(episode=episode):
                self.assertFalse(pipeline.same_tracking_target(previous, CONFIG))
                self.assertEqual(pipeline.official_identity_aliases(previous, CONFIG), {"WP29", "29W", "96W", "TWENTYNINE"})
                self.assertIsNone(pipeline.previous_forecast_seed(previous, init, {"96W", *aliases}, 12, TARGET))


class NamedWarningBoundaryTests(unittest.TestCase):
    old = "TROPICAL DEPRESSION 28W (TWENTYEIGHT) WAS LOCATED NEAR 18.0N 135.0E"
    current = "TROPICAL DEPRESSION 29W (TWENTYNINE) WARNING POSITION: 141800Z --- NEAR 10.4N 160.5E"
    missing_current = "TROPICAL DEPRESSION 29W (TWENTYNINE) WARNING CANCELLED."

    def test_saved_real_warning_handles_repeated_headings_and_other_storm_reference(self):
        path = Path(__file__).resolve().parents[1] / "analysis/materials/2026-10-14-2100z-jtwc-29w-warning.txt"
        bulletin = path.read_text()
        self.assertEqual(pipeline.parse_jtwc_named_seed(bulletin, {"29W", "96W", "TWENTYNINE"}), (10.4, 160.5, "29W"))
        self.assertEqual(pipeline.parse_jtwc_named_systems(bulletin), [{"id": "29W", "name": "TWENTYNINE", "lat": 10.4, "lon": 160.5}])

    def test_mixed_complete_sections_keep_each_position(self):
        for bulletin in (self.old + "\n" + self.current, self.current + "\n" + self.old):
            with self.subTest(bulletin=bulletin):
                self.assertEqual(pipeline.parse_jtwc_named_seed(bulletin, {"29W"}), (10.4, 160.5, "29W"))
                self.assertEqual(pipeline.parse_jtwc_named_seed(bulletin, {"28W"}), (18.0, 135.0, "28W"))

    def test_missing_current_position_never_uses_old_storm_position(self):
        bulletin = self.missing_current + "\n" + self.old
        self.assertIsNone(pipeline.parse_jtwc_named_seed(bulletin, {"29W", "TWENTYNINE"}))
        self.assertEqual([item["id"] for item in pipeline.parse_jtwc_named_systems(bulletin)], ["28W"])

    def test_missing_old_position_does_not_swallow_current_section(self):
        bulletin = "TYPHOON 27W (KOGUMA) WARNING CANCELLED.\n" + self.current
        self.assertIsNone(pipeline.parse_jtwc_named_seed(bulletin, {"27W", "KOGUMA"}))
        self.assertEqual(pipeline.parse_jtwc_named_seed(bulletin, {"29W"}), (10.4, 160.5, "29W"))

    def test_old_only_sections_do_not_resolve_new_target(self):
        self.assertIsNone(pipeline.parse_jtwc_named_seed(self.old, {"29W", "96W", "TWENTYNINE"}))


if __name__ == "__main__":
    unittest.main()
