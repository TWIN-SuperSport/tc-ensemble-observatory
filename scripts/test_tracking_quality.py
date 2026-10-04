#!/usr/bin/env python3
"""Offline regression tests for conservative GEFS tracking and publication.

Run with: python scripts/test_tracking_quality.py
Synthetic fields deliberately include incomplete/open centers and recycled
Invest identifiers. No network access or production-file writes are used.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np

import build_gefs_data as pipeline


INIT = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
INIT_KEY = "2026100312"
EPISODE = "2026-10-94W"
BOX = {"bottomlat": 10, "toplat": 20, "leftlon": 120, "rightlon": 130}


def field(latitudes=None, longitudes=None, pressure=None):
    latitudes = np.arange(10.0, 20.5, 0.5) if latitudes is None else latitudes
    longitudes = np.arange(120.0, 130.5, 0.5) if longitudes is None else longitudes
    lons, lats = np.meshgrid(longitudes, latitudes)
    values = (1000 + (lats - 15) ** 2 + (lons - 125) ** 2
              if pressure is None else pressure(lats, lons))
    return lats.ravel(), lons.ravel(), np.asarray(values, dtype=float).ravel()


def normalized(*args, **kwargs):
    return pipeline.normalize_domain(*field(*args, **kwargs), BOX)


def config():
    return {
        "storm": "WP94", "trackingTargetId": EPISODE,
        "stormInfo": {"id": "94W", "aliases": ["94W"]},
        "seed": {"lat": 15.0, "lon": 125.0}, "domain": dict(BOX),
        "initialSearchRadiusKm": 700, "stepSearchRadiusKm": 750,
        "clusterThresholdKm": 650,
        "_seedResolution": {"source": "JTWC ABPW"},
        "_trackingIdentity": {"status": "verified"},
        "_officialIdentity": {},
    }


def tracks():
    return {
        member: [pipeline.TrackPoint(h, 15.0, 125.0, 1000.0)
                 for h in pipeline.FORECAST_HOURS]
        for member in pipeline.MEMBERS
    }


def payload_with_prefix(prefix_length=4):
    cfg, member_tracks = config(), tracks()
    member = pipeline.MEMBERS[-1]
    member_tracks[member] = member_tracks[member][:prefix_length]
    cfg["_trackDiagnostics"] = {member: {
        "reason": "center_lost",
        "atForecastHour": pipeline.FORECAST_HOURS[prefix_length],
        "lastValidForecastHour": pipeline.FORECAST_HOURS[prefix_length - 1],
        "detail": "Synthetic center vanished",
    }}
    return pipeline.build_payload(INIT, cfg, member_tracks, {"disclaimer": {}})


class DomainNormalizationTests(unittest.TestCase):
    def test_shuffled_global_and_subset_are_identical(self):
        global_field = field(np.arange(0, 31, 0.5), np.arange(0, 360, 0.5))
        rng = np.random.default_rng(20261004)
        permutation = rng.permutation(len(global_field[0]))
        shuffled = tuple(a[permutation] for a in global_field)
        subset = field()
        reverse = tuple(a[::-1] for a in subset)
        full = pipeline.normalize_domain(*shuffled, BOX)
        cutout = pipeline.normalize_domain(*reverse, BOX)
        for a, b in zip(full, cutout):
            np.testing.assert_array_equal(a, b)
        self.assertEqual(pipeline.select_minimum(*full, (15, 125), 750),
                         pipeline.select_minimum(*cutout, (15, 125), 750))

    def test_negative_and_positive_longitudes_are_equivalent(self):
        lats, lons, values = field()
        expected = pipeline.normalize_domain(lats, lons, values, BOX)
        actual = pipeline.normalize_domain(lats, lons - 360, values, BOX)
        for a, b in zip(expected, actual):
            np.testing.assert_array_equal(a, b)

    def test_zero_meridian_crossing_keeps_spatial_order(self):
        box = {"bottomlat": 10, "toplat": 12, "leftlon": 358, "rightlon": 2}
        raw = field(np.arange(10, 12.5, 0.5), [0, 0.5, 1, 1.5, 2, 358, 358.5, 359, 359.5])
        _, lons, _ = pipeline.normalize_domain(*raw, box)
        self.assertEqual(lons[:9].tolist(), [358, 358.5, 359, 359.5, 0, 0.5, 1, 1.5, 2])

    def test_mismatched_array_lengths_rejected(self):
        lats, lons, values = field()
        with self.assertRaises(RuntimeError):
            pipeline.normalize_domain(lats[:-1], lons, values, BOX)

    def test_missing_single_cell_rejected(self):
        raw = field()
        with self.assertRaises(RuntimeError):
            pipeline.normalize_domain(*(np.delete(a, 100) for a in raw), BOX)

    def test_duplicate_cell_rejected(self):
        raw = field()
        with self.assertRaises(RuntimeError):
            pipeline.normalize_domain(*(np.append(a, a[100]) for a in raw), BOX)

    def test_missing_interior_row_is_not_a_valid_regular_grid(self):
        raw = field()
        keep = raw[0] != 15
        with self.assertRaises(RuntimeError):
            pipeline.normalize_domain(*(a[keep] for a in raw), BOX)

    def test_nonfinite_pressure_inside_domain_rejected(self):
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=bad):
                lats, lons, values = field()
                values[100] = bad
                with self.assertRaises(RuntimeError):
                    pipeline.normalize_domain(lats, lons, values, BOX)

    def test_outside_domain_values_do_not_change_subset(self):
        lats, lons, values = field()
        expanded = (np.append(lats, 80), np.append(lons, 300), np.append(values, np.nan))
        result = pipeline.normalize_domain(*expanded, BOX)
        for a, b in zip(result, (lats, lons, values)):
            np.testing.assert_array_equal(a, b)

    def test_no_overlap_and_too_small_grid_rejected(self):
        for raw in (field([60, 61, 62], [120, 121, 122]), field([10, 11], [120, 121, 122])):
            with self.subTest(points=len(raw[0])):
                with self.assertRaises(RuntimeError):
                    pipeline.normalize_domain(*raw, BOX)


class MinimumSelectionTests(unittest.TestCase):
    def assert_terminated(self, raw, previous=(15, 125), radius=750, reasons=None):
        with self.assertRaises(pipeline.TrackingTerminated) as caught:
            pipeline.select_minimum(*raw, previous, radius)
        if reasons:
            self.assertIn(caught.exception.reason, reasons)
        self.assertTrue(str(caught.exception))

    def test_interior_bowl_selected(self):
        self.assertEqual(pipeline.select_minimum(*normalized(), (15, 125), 750),
                         (15.0, 125.0, 1000.0))

    def test_shallow_but_real_minimum_retained(self):
        raw = normalized(pressure=lambda lat, lon: 1008 + 0.001 * ((lat - 15) ** 2 + (lon - 125) ** 2))
        lat, lon, pressure = pipeline.select_minimum(*raw, (15, 125), 750)
        self.assertEqual((lat, lon), (15.0, 125.0))
        self.assertAlmostEqual(pressure, 1008.0)

    def test_flat_field_is_center_loss(self):
        raw = normalized(pressure=lambda lat, lon: np.full_like(lat, 1005))
        self.assert_terminated(raw, reasons={"center_lost"})

    def test_center_just_outside_search_does_not_create_boundary_minimum(self):
        raw = normalized(pressure=lambda lat, lon: 1000 + (lat - 15) ** 2 + (lon - 127) ** 2)
        self.assert_terminated(raw, radius=150, reasons={"center_lost"})

    def test_center_just_inside_search_is_retained(self):
        raw = normalized(pressure=lambda lat, lon: 1000 + (lat - 15) ** 2 + (lon - 126) ** 2)
        self.assertEqual(pipeline.select_minimum(*raw, (15, 125), 150)[:2], (15.0, 126.0))

    def test_actual_previous_center_radius_is_not_expanded(self):
        raw = normalized(pressure=lambda lat, lon: 1000 + (lat - 15) ** 2 + (lon - 129) ** 2)
        self.assert_terminated(raw, radius=300, reasons={"center_lost"})

    def test_domain_edge_stops_tracking(self):
        raw = normalized(pressure=lambda lat, lon: 1000 + (lat - 15) ** 2 + (lon - 131) ** 2)
        self.assert_terminated(raw, previous=(15, 128), reasons={"domain_exit"})

    def test_no_points_near_previous_is_domain_exit(self):
        self.assert_terminated(normalized(), previous=(62, 140), radius=750, reasons={"domain_exit"})

    def test_boundary_low_does_not_switch_to_unrelated_interior_low(self):
        def pressure(lat, lon):
            values = 1000 + (lat - 15) ** 2 + (lon - 131) ** 2
            values[(lat == 15) & (lon == 125)] = 1003
            return values
        self.assert_terminated(normalized(pressure=pressure), previous=(15, 128), reasons={"domain_exit"})

    def test_closed_flat_bottom_plateau_retained(self):
        def pressure(lat, lon):
            return 1000 + np.maximum(np.abs(lat - 15) - 1, 0) ** 2 + np.maximum(np.abs(lon - 125) - 1, 0) ** 2
        lat, lon, p = pipeline.select_minimum(*normalized(pressure=pressure), (15, 125), 750)
        self.assertEqual((lat, lon), (15.0, 125.0))
        self.assertEqual(p, 1000)

    def test_open_plateau_connected_to_boundary_is_not_closed_center(self):
        raw = normalized(pressure=lambda lat, lon: 1000 + np.maximum(125 - lon, 0))
        self.assert_terminated(raw, reasons={"center_lost", "domain_exit"})

    def test_plateau_connected_to_lower_cell_outside_search_is_rejected(self):
        def pressure(lat, lon):
            values = np.full_like(lat, 1010)
            values[(lat == 15) & np.isin(lon, [125, 125.5])] = 1000
            values[(lat == 15) & (lon == 126)] = 999
            return values
        self.assert_terminated(normalized(pressure=pressure), radius=50, reasons={"center_lost"})


class PublicationQualityTests(unittest.TestCase):
    def test_pressure_threshold_unchanged(self):
        for delta in (-25, 25):
            points = [pipeline.TrackPoint(0, 15, 125, 1000), pipeline.TrackPoint(12, 15, 125, 1000 + delta)]
            self.assertNotIn("pressure_discontinuity", pipeline.noise_reasons(points))
        for delta in (-25.01, 25.01):
            points = [pipeline.TrackPoint(0, 15, 125, 1000), pipeline.TrackPoint(12, 15, 125, 1000 + delta)]
            self.assertIn("pressure_discontinuity", pipeline.noise_reasons(points))

    def test_translation_and_equator_guards_remain(self):
        slow = [pipeline.TrackPoint(0, 15, 125, 1000), pipeline.TrackPoint(12, 24, 125, 1000)]
        fast = [pipeline.TrackPoint(0, 15, 125, 1000), pipeline.TrackPoint(12, 25, 125, 1000)]
        cross = [pipeline.TrackPoint(0, 0.5, 125, 1000), pipeline.TrackPoint(12, -0.5, 125, 1000)]
        self.assertNotIn("translation_speed", pipeline.noise_reasons(slow))
        self.assertIn("translation_speed", pipeline.noise_reasons(fast))
        self.assertIn("equator_crossing", pipeline.noise_reasons(cross))

    def test_twenty_clean_pass_but_nineteen_fail(self):
        for noise_count in (11, 12):
            member_tracks = tracks()
            for member in pipeline.MEMBERS[:noise_count]:
                member_tracks[member][-1].mslp_hpa = 974
            if noise_count == 11:
                payload = pipeline.build_payload(INIT, config(), member_tracks, {})
                self.assertEqual(payload["summary"]["cleanMembers"], 20)
                pipeline.validate(payload, INIT_KEY)
            else:
                with self.assertRaisesRegex(RuntimeError, "clean=19"):
                    pipeline.build_payload(INIT, config(), member_tracks, {})

    def test_valid_single_and_multiple_point_noise_prefixes(self):
        for length in (1, 4, 20):
            with self.subTest(length=length):
                payload = payload_with_prefix(length)
                pipeline.validate(payload, INIT_KEY)
                self.assertEqual(payload["tracks"][-1]["cluster"], "NOISE")
                self.assertEqual(payload["summary"]["cleanMembers"], 30)
                self.assertTrue(all(len(c["medianTrack"]) == 21 for c in payload["clusters"]))

    def test_clean_track_cannot_be_truncated(self):
        payload = payload_with_prefix()
        payload["tracks"][-1]["cluster"] = "C1"
        with self.assertRaises(AssertionError):
            pipeline.validate(payload, INIT_KEY)

    def test_prefix_requires_termination_and_matching_reason(self):
        for mutation in ("termination", "reason"):
            payload = payload_with_prefix()
            if mutation == "termination":
                payload["tracks"][-1]["termination"] = None
            else:
                payload["tracks"][-1]["noiseReasons"] = []
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                pipeline.validate(payload, INIT_KEY)

    def test_empty_skipped_duplicate_and_nonfinite_points_rejected(self):
        for mutation in ("empty", "skip", "duplicate", "nan"):
            payload = payload_with_prefix()
            track = payload["tracks"][-1]
            if mutation == "empty":
                track["points"] = []
            elif mutation == "skip":
                track["points"][1]["fhour"] = 24
            elif mutation == "duplicate":
                track["points"][1]["fhour"] = 0
            else:
                track["points"][1]["lat"] = float("nan")
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                pipeline.validate(payload, INIT_KEY)

    def test_full_track_may_not_have_termination(self):
        payload = payload_with_prefix()
        payload["tracks"][0]["termination"] = {"reason": "center_lost"}
        with self.assertRaises(AssertionError):
            pipeline.validate(payload, INIT_KEY)

    def test_termination_hours_match_observed_prefix(self):
        for key in ("atForecastHour", "lastValidForecastHour"):
            payload = payload_with_prefix()
            payload["tracks"][-1]["termination"][key] = 240
            with self.subTest(key=key), self.assertRaises(AssertionError):
                pipeline.validate(payload, INIT_KEY)

    def test_summary_counts_must_match_track_classification(self):
        payload = payload_with_prefix()
        payload["summary"]["cleanMembers"] = 31
        payload["summary"]["noiseMembers"] = 0
        with self.assertRaises(AssertionError):
            pipeline.validate(payload, INIT_KEY)


class EpisodeIdentityTests(unittest.TestCase):
    def previous(self, episode=EPISODE):
        return {
            "meta": {
                "init": "2026100300", "storm": "WP94", "trackingTargetId": episode,
                "stormInfo": {"id": "94W", "aliases": ["94W", "22W", "KROVANH"]},
                "trackingIdentity": {"status": "verified"},
                "officialIdentity": {"jtwc": {"id": "22W", "name": "KROVANH"}},
            },
            "tracks": [{"cluster": "C1", "points": [{"fhour": 12, "lat": 15.5, "lon": 125.5}]}],
        }

    def test_recycled_invest_id_does_not_match_episode(self):
        previous = self.previous("2026-08-94W")
        self.assertFalse(pipeline.same_tracking_target(previous, config()))
        aliases = pipeline.official_identity_aliases(previous, config())
        self.assertNotIn("22W", aliases)
        self.assertNotIn("KROVANH", aliases)
        self.assertIsNone(pipeline.previous_forecast_seed(previous, INIT, {"94W"}, 12, EPISODE))

    def test_legacy_episode_missing_cannot_match_new_target(self):
        previous = self.previous()
        del previous["meta"]["trackingTargetId"]
        self.assertFalse(pipeline.same_tracking_target(previous, config()))
        self.assertIsNone(pipeline.previous_forecast_seed(previous, INIT, {"94W"}, 12, EPISODE))

    def test_same_episode_can_retain_alias_and_recent_verified_seed(self):
        previous = self.previous()
        self.assertTrue(pipeline.same_tracking_target(previous, config()))
        self.assertIn("22W", pipeline.official_identity_aliases(previous, config()))
        self.assertEqual(pipeline.previous_forecast_seed(previous, INIT, {"94W"}, 12, EPISODE), (15.5, 125.5))

    def test_old_or_unverified_seed_remains_ineligible(self):
        previous = self.previous()
        self.assertIsNone(pipeline.previous_forecast_seed(previous, INIT, {"94W"}, 11, EPISODE))
        previous["meta"]["trackingIdentity"]["status"] = "unverified"
        self.assertIsNone(pipeline.previous_forecast_seed(previous, INIT, {"94W"}, 12, EPISODE))

    def resolve_with_stale_warning(self, previous, active_invest=False):
        cfg = config()
        cfg["seedResolver"] = {"enabled": True, "requireResolvedSeed": True,
                               "previousForecastFallbackHours": 12}
        identity = {
            "stormInfo": cfg["stormInfo"],
            "invest": {"id": "94W", "status": "historical"},
            "jtwc": {"id": "22W", "name": "KROVANH", "lat": 31, "lon": 140,
                     "status": "stale", "warningUrl": "https://example.invalid/stale"},
        }
        if active_invest:
            identity["invest"].update({"status": "active", "lat": 14, "lon": 128,
                                       "sourceUrl": "https://example.invalid/current"})
        with patch.object(pipeline, "resolve_official_identity", return_value=identity), \
                patch.object(pipeline, "request", side_effect=AssertionError("Offline test attempted network")):
            return pipeline.resolve_tracking_seed(cfg, previous, INIT)

    def test_active_invest_wins_over_stale_warning_coordinates(self):
        resolved = self.resolve_with_stale_warning(self.previous(), active_invest=True)
        self.assertEqual(resolved["seed"], {"lat": 14, "lon": 128})
        self.assertEqual(resolved["_seedResolution"]["source"], "JTWC ABPW")

    def test_stale_warning_uses_only_bounded_verified_fallback(self):
        resolved = self.resolve_with_stale_warning(self.previous())
        self.assertEqual(resolved["seed"], {"lat": 15.5, "lon": 125.5})
        self.assertEqual(resolved["_seedResolution"]["source"], "previous GEFS ensemble median")

    def test_stale_warning_cannot_bypass_fallback_age_or_episode(self):
        old = self.previous()
        old["meta"]["init"] = "2026100200"
        for previous in (old, self.previous("2026-08-94W")):
            with self.subTest(meta=previous["meta"]), self.assertRaisesRegex(RuntimeError, "Tracking seed unresolved"):
                self.resolve_with_stale_warning(previous)


class TrackConstructionTests(unittest.TestCase):
    def run_mock_fields(self, selections):
        cfg = config()
        raw = normalized()

        def download(job):
            _, member, hour, _ = job
            return member, hour, b"synthetic", {"source": "fixture"}

        with patch.object(pipeline, "MEMBERS", ["c00"]), \
                patch.object(pipeline, "FORECAST_HOURS", [0, 12, 24]), \
                patch.object(pipeline, "download_one", side_effect=download) as acquisition, \
                patch.object(pipeline, "decode_prmsl", return_value=raw), \
                patch.object(pipeline, "select_minimum", side_effect=selections):
            result = pipeline.build_tracks(INIT, cfg)
            self.assertEqual(acquisition.call_count, 3)
        return cfg, result

    def test_later_loss_keeps_only_verified_prefix(self):
        cfg, result = self.run_mock_fields([
            (15, 125, 1000), pipeline.TrackingTerminated("center_lost", "synthetic loss")])
        self.assertEqual([p.fhour for p in result["c00"]], [0])
        self.assertEqual(cfg["_trackDiagnostics"]["c00"]["atForecastHour"], 12)
        self.assertEqual(cfg["_trackDiagnostics"]["c00"]["lastValidForecastHour"], 0)

    def test_unresolved_initial_center_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "Initial center unresolved"):
            self.run_mock_fields([pipeline.TrackingTerminated("center_lost", "initial loss")])


class BulletinValidityTests(unittest.TestCase):
    def test_missing_or_malformed_validity_is_rejected(self):
        now = datetime(2026, 10, 4, 3, tzinfo=timezone.utc)
        for text in ("", "upstream returned an HTML error", "031300Z-041300ZXXX2026", "039960Z-041300ZOCT2026"):
            with self.subTest(text=text), self.assertRaises((RuntimeError, ValueError)):
                pipeline.jtwc_bulletin_validity(text, now)

    def test_issue_expiry_and_retrieval_are_separate(self):
        now = datetime(2026, 10, 4, 3, tzinfo=timezone.utc)
        result = pipeline.jtwc_bulletin_validity("031300Z-041300ZOCT2026", now)
        self.assertEqual(result["issuedAt"], "2026-10-03T13:00:00Z")
        self.assertEqual(result["validUntil"], "2026-10-04T13:00:00Z")
        self.assertEqual(result["retrievedAt"], "2026-10-04T03:00:00Z")

    def test_expired_and_far_future_bulletins_rejected(self):
        for now in (datetime(2026, 10, 4, 14, tzinfo=timezone.utc),
                    datetime(2026, 10, 3, 10, tzinfo=timezone.utc)):
            with self.subTest(now=now), self.assertRaises(RuntimeError):
                pipeline.jtwc_bulletin_validity("031300Z-041300ZOCT2026", now)

    def test_month_and_year_rollover(self):
        for text, now, expected in (
            ("301300Z-011300ZOCT2026", datetime(2026, 10, 1, 0, tzinfo=timezone.utc), "2026-09-30T13:00:00Z"),
            ("311300Z-011300ZJAN2026", datetime(2026, 1, 1, 0, tzinfo=timezone.utc), "2025-12-31T13:00:00Z"),
        ):
            with self.subTest(text=text):
                self.assertEqual(pipeline.jtwc_bulletin_validity(text, now)["issuedAt"], expected)


class DiagnosticEvidenceTests(unittest.TestCase):
    def test_rejected_run_retains_signed_events_prefix_and_provenance(self):
        cfg = config()
        cfg["_fieldProvenance"] = {"c00-f000": {"source": "fixture", "sha256": "a" * 64, "bytes": 100}}
        cfg["_trackDiagnostics"] = {"p01": {"reason": "domain_exit", "atForecastHour": 12,
                                             "lastValidForecastHour": 0, "detail": "Synthetic boundary"}}
        member_tracks = {
            "c00": [pipeline.TrackPoint(0, 15, 125, 1000), pipeline.TrackPoint(12, 15, 125, 970)],
            "p01": [pipeline.TrackPoint(0, 15, 125, 1000)],
        }
        with tempfile.TemporaryDirectory(prefix="tc-tracking-diagnostics-") as directory:
            output = Path(directory) / "evidence.json"
            with patch.dict(pipeline.os.environ, {"GEFS_DIAGNOSTICS_PATH": str(output)}):
                pipeline.write_run_diagnostics(INIT, cfg, member_tracks, "rejected", RuntimeError("fixture failure"))
            document = json.loads(output.read_text())
        self.assertEqual(document["status"], "rejected")
        self.assertEqual(document["error"], "fixture failure")
        self.assertEqual(document["policy"]["minimumCompleteCleanMembers"], 20)
        self.assertEqual(document["policy"]["maxPressureChangeHpa"], 25)
        self.assertEqual(document["downloadedFields"], 1)
        self.assertEqual(document["fieldProvenance"], cfg["_fieldProvenance"])
        self.assertEqual(document["members"]["c00"]["events"][0]["pressureChangeHpa"], -30)
        self.assertEqual(len(document["members"]["p01"]["points"]), 1)
        self.assertEqual(document["members"]["p01"]["termination"]["reason"], "domain_exit")


class ExistingCycleTests(unittest.TestCase):
    def run_existing_cycle(self, mutation=None, force=False, diagnostic_error=None, build_error=None):
        cfg = config()
        member_tracks = tracks()
        previous = pipeline.build_payload(INIT, cfg, member_tracks, {})
        if mutation == "legacy_algorithm":
            previous["meta"].pop("trackingAlgorithm", None)
        elif mutation == "unverified_identity":
            previous["meta"]["trackingIdentity"] = {"status": "unverified"}
        with tempfile.TemporaryDirectory(prefix="tc-tracking-main-") as directory:
            root = Path(directory)
            config_path, data_path = root / "config.json", root / "data.json"
            config_path.write_text(json.dumps(cfg))
            data_path.write_text(json.dumps(previous))
            saved_bytes = data_path.read_bytes()
            argv = ["build_gefs_data.py"] + (["--force-init", INIT_KEY] if force else [])
            with patch.object(pipeline, "CONFIG_PATH", config_path), \
                    patch.object(pipeline, "DATA_PATH", data_path), \
                    patch.object(pipeline, "LATEST_PATH", root / "latest.json"), \
                    patch.object(pipeline.sys, "argv", argv), \
                    patch.object(pipeline, "latest_complete_cycle", return_value=INIT), \
                    patch.object(pipeline, "refresh_existing_identity", return_value=False), \
                    patch.object(pipeline, "resolve_tracking_seed", return_value=cfg), \
                    patch.object(pipeline, "build_tracks", return_value=member_tracks, side_effect=build_error) as build, \
                    patch.object(pipeline, "verify_tracking_identity", return_value={"status": "verified"}), \
                    patch.object(pipeline, "write_atomically") as publish, \
                    patch.object(pipeline, "write_run_diagnostics", side_effect=diagnostic_error):
                expected_error = build_error or diagnostic_error
                if expected_error is not None:
                    with self.assertRaises(type(expected_error)) as caught:
                        pipeline.main()
                    self.assertIs(caught.exception, expected_error)
                    publish.assert_not_called()
                    self.assertFalse((root / "latest.json").exists())
                    self.assertEqual(data_path.read_bytes(), saved_bytes)
                else:
                    self.assertEqual(pipeline.main(), 0)
                return build.call_count

    def test_valid_current_algorithm_cycle_avoids_redownload(self):
        self.assertEqual(self.run_existing_cycle(), 0)

    def test_same_cycle_old_algorithm_must_be_rebuilt(self):
        self.assertEqual(self.run_existing_cycle("legacy_algorithm"), 1)

    def test_same_cycle_unverified_payload_must_be_rebuilt(self):
        self.assertEqual(self.run_existing_cycle("unverified_identity"), 1)

    def test_explicit_force_init_rebuilds_current_valid_cycle(self):
        self.assertEqual(self.run_existing_cycle(force=True), 1)

    def test_diagnostic_failure_cannot_overwrite_published_payload(self):
        self.assertEqual(self.run_existing_cycle(force=True, diagnostic_error=OSError("fixture evidence disk full")), 1)

    def test_secondary_diagnostic_failure_cannot_mask_original_error(self):
        self.assertEqual(self.run_existing_cycle(force=True,
            diagnostic_error=OSError("fixture evidence disk full"),
            build_error=RuntimeError("fixture scientific rejection")), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
