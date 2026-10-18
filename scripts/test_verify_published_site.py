#!/usr/bin/env python3
"""Stdlib-only regression fixtures for published prefix-track verification."""
import copy
import unittest

from verify_published_site import HOURS, MEMBERS, PREFIX_ALGORITHM, validate_payload


def prefix_fixture(lengths=None, noisy_members=()):
    lengths = lengths or ([21] * 6 + [11] * 25)
    tracks = []
    for member, length in zip(sorted(MEMBERS), lengths):
        points = [{"fhour": hour, "lat": 10, "lon": 160, "mslp_hpa": 1000} for hour in HOURS[:length]]
        flags = []
        if member in noisy_members:
            points[-1]["mslp_hpa"] = 1026
            flags = ["pressure_discontinuity"]
        termination = None if length == 21 else {"reason": "center_lost", "lastValidForecastHour": HOURS[length - 1], "atForecastHour": HOURS[length]}
        tracks.append({"member": member, "cluster": "NOISE" if flags else "TERMINATED" if termination else "UNCLASSIFIED", "noiseReasons": flags, "termination": termination, "points": points})
    valid = [track for track in tracks if not track["noiseReasons"]]
    availability = [{"fhour": hour, "count": sum(track["points"][-1]["fhour"] >= hour for track in valid), "total": 31} for hour in HOURS]
    horizon = max((row["fhour"] for row in availability if row["count"] >= 20), default=0)
    eligible = [track["member"] for track in valid if track["points"][-1]["fhour"] >= horizon] if horizon else []
    for track in tracks:
        if track["member"] in eligible:
            track["scenarioCluster"] = "C1"
            if not track["termination"]:
                track["cluster"] = "C1"
    clusters = [{"id": "C1", "label": "test", "members": eligible, "count": len(eligible), "share": round(len(eligible) / 31 * 100, 1), "conditionalShare": 100.0, "denominator": 31, "conditionalDenominator": len(eligible), "forecastHour": horizon, "medianTrack": [{"fhour": hour, "lat": 10, "lon": 160} for hour in HOURS if hour <= horizon]}] if eligible else []
    summary = {
        "members": 31,
        "cleanMembers": sum(not track["termination"] for track in valid),
        "terminatedMembers": sum(bool(track["termination"]) for track in valid),
        "noiseMembers": len(tracks) - len(valid),
        "validMembers": len(valid),
        "clusteredMembers": len(eligible),
        "availableMembersByLead": availability,
        "clusterCount": len(clusters),
    }
    data = {"meta": {"init": "2026101806", "trackingTargetId": "2026-10-test", "trackingAlgorithm": PREFIX_ALGORITHM, "trackingIdentity": {"status": "verified"}, "clusterForecastHour": horizon}, "summary": summary, "tracks": tracks, "disclaimer": {"ja": "test", "en": "test"}, "clusters": clusters}
    entry = {"path": "2026101806.json", "init": "2026101806", "trackingTargetId": "2026-10-test", **summary, "clusterForecastHour": horizon}
    return {
        "data": data,
        "latest": {"init": "2026101806", "status": "analysis_complete", "members": 31},
        "monitor": {"trackingTargetId": "2026-10-test"},
        "history": {"latest": "2026101806", "latestPath": entry["path"], "runCount": 1, "runs": [entry]},
        "expected_target": "2026-10-test",
        "minimum_init": "2026101800",
    }


class PrefixPayloadTests(unittest.TestCase):
    def setUp(self):
        self.case = prefix_fixture()

    def reject_change(self, keys, value):
        case = copy.deepcopy(self.case)
        parent = case
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
        with self.assertRaises((AssertionError, KeyError, TypeError, ValueError)):
            validate_payload(**case)

    def test_six_complete_twenty_five_terminated(self):
        before = copy.deepcopy(self.case)
        report = validate_payload(**self.case)
        self.assertEqual(report["summary"]["cleanMembers"], 6)
        self.assertEqual(report["summary"]["terminatedMembers"], 25)
        self.assertEqual(report["summary"]["validMembers"], 31)
        self.assertEqual(self.case["data"]["meta"]["clusterForecastHour"], 120)
        self.assertEqual(report["summary"]["availableMembersByLead"][-1], {"fhour": 240, "count": 6, "total": 31})
        self.assertEqual(validate_payload(**self.case), report)
        self.assertEqual(self.case, before)

    def test_optional_scenario_cluster(self):
        for track in self.case["data"]["tracks"]:
            track.pop("scenarioCluster", None)
        validate_payload(**self.case)

    def test_full_horizon_remains_supported(self):
        case = prefix_fixture([21] * 31)
        validate_payload(**case)
        self.assertEqual(case["data"]["meta"]["clusterForecastHour"], 240)

    def test_horizon_uses_twentieth_member_not_six_survivors(self):
        case = prefix_fixture([21] * 6 + [11] * 14 + [5] * 11)
        validate_payload(**case)
        self.assertEqual(case["data"]["summary"]["clusteredMembers"], 20)
        self.assertEqual(case["data"]["clusters"][0]["share"], 64.5)
        self.assertEqual(case["data"]["clusters"][0]["conditionalShare"], 100.0)
        self.assertTrue(all(track.get("scenarioCluster") is None for track in case["data"]["tracks"][20:]))

    def test_zero_horizon_has_no_scenarios(self):
        case = prefix_fixture([21] * 6 + [1] * 25)
        validate_payload(**case)
        self.assertEqual(case["data"]["meta"]["clusterForecastHour"], 0)
        self.assertEqual(case["data"]["clusters"], [])
        self.assertEqual(case["data"]["summary"]["clusteredMembers"], 0)

    def test_real_noise_is_excluded_even_with_valid_termination(self):
        case = prefix_fixture(noisy_members=sorted(MEMBERS)[-5:])
        validate_payload(**case)
        self.assertEqual(case["data"]["summary"]["noiseMembers"], 5)
        self.assertEqual(case["data"]["summary"]["terminatedMembers"], 20)
        self.assertEqual(case["data"]["summary"]["availableMembersByLead"][0]["count"], 26)

    def test_raw_pressure_flag_survives_published_rounding(self):
        case = prefix_fixture(noisy_members=sorted(MEMBERS)[-1:])
        # Raw pressure 1025.01 rounds to 1025.0, retaining the raw >25 flag.
        case["data"]["tracks"][-1]["points"][-1]["mslp_hpa"] = 1025.0
        validate_payload(**case)
        self.assertEqual(case["data"]["summary"]["noiseMembers"], 1)

    def test_exactly_twenty_valid_prefixes(self):
        case = prefix_fixture(noisy_members=sorted(MEMBERS)[-11:])
        validate_payload(**case)
        self.assertEqual(case["data"]["summary"]["validMembers"], 20)

    def test_nineteen_valid_prefixes_rejected_even_with_matching_counts(self):
        case = prefix_fixture(noisy_members=sorted(MEMBERS)[-12:])
        with self.assertRaisesRegex(AssertionError, "Insufficient valid track prefixes"):
            validate_payload(**case)

    def test_all_valid_prefixes_may_terminate(self):
        case = prefix_fixture([11] * 31)
        validate_payload(**case)
        self.assertEqual(case["data"]["summary"]["cleanMembers"], 0)

    def test_multiple_clusters_share_the_same_horizon_and_denominator(self):
        data = self.case["data"]
        first = data["clusters"][0]
        second = copy.deepcopy(first)
        first["members"], second["members"] = first["members"][:16], first["members"][16:]
        second["id"] = "C2"
        data["clusters"].append(second)
        for cluster in data["clusters"]:
            cluster["count"] = len(cluster["members"])
            cluster["share"] = cluster["conditionalShare"] = round(cluster["count"] / 31 * 100, 1)
        for track in data["tracks"]:
            if track["member"] in second["members"]:
                track["scenarioCluster"] = "C2"
        data["summary"]["clusterCount"] = 2
        self.case["history"]["runs"][0]["clusterCount"] = 2
        validate_payload(**self.case)

    def test_summary_counts_are_strict(self):
        for key in ("members", "cleanMembers", "terminatedMembers", "noiseMembers", "validMembers", "clusteredMembers", "clusterCount"):
            for value in (True, "31", -1, 32, self.case["data"]["summary"][key] + 1):
                with self.subTest(key=key, value=value):
                    self.reject_change(("data", "summary", key), value)

    def test_missing_v3_fields_cannot_fall_back_to_legacy(self):
        for key in ("terminatedMembers", "validMembers", "availableMembersByLead", "clusteredMembers"):
            case = copy.deepcopy(self.case)
            del case["data"]["summary"][key]
            with self.subTest(key=key), self.assertRaises(KeyError):
                validate_payload(**case)

    def test_malformed_availability(self):
        for key, value in (("count", 7), ("count", True), ("total", 6), ("total", "31"), ("fhour", 228)):
            with self.subTest(key=key, value=value):
                self.reject_change(("data", "summary", "availableMembersByLead", 20, key), value)
        self.reject_change(("data", "summary", "availableMembersByLead"), self.case["data"]["summary"]["availableMembersByLead"][:-1])

    def test_unsupported_or_earlier_cluster_horizon(self):
        for hour in (0, 108, 132, 240, "120", True):
            with self.subTest(hour=hour):
                self.reject_change(("data", "meta", "clusterForecastHour"), hour)

    def test_no_extrapolated_median(self):
        median = copy.deepcopy(self.case["data"]["clusters"][0]["medianTrack"])
        median.append({"fhour": 132, "lat": 10, "lon": 160})
        self.reject_change(("data", "clusters", 0, "medianTrack"), median)
        self.reject_change(("data", "clusters", 0, "medianTrack", 0, "lat"), float("nan"))

    def test_short_prefix_cannot_be_extended_without_termination_update(self):
        points = copy.deepcopy(self.case["data"]["tracks"][6]["points"])
        points.append({"fhour": 132, "lat": 10, "lon": 160, "mslp_hpa": 1000})
        self.reject_change(("data", "tracks", 6, "points"), points)

    def test_short_prefix_cannot_claim_full_member_points(self):
        self.reject_change(("data", "tracks", 6, "points"), copy.deepcopy(self.case["data"]["tracks"][0]["points"]))

    def test_termination_is_required_and_precise(self):
        for value in (None, {}, {"reason": "center_lost"}):
            with self.subTest(termination=value):
                self.reject_change(("data", "tracks", 6, "termination"), value)
        for key, value in (("reason", "dissipated"), ("lastValidForecastHour", 108), ("atForecastHour", 144), ("atForecastHour", "132")):
            with self.subTest(key=key):
                self.reject_change(("data", "tracks", 6, "termination", key), value)
        self.reject_change(("data", "tracks", 0, "termination"), self.case["data"]["tracks"][6]["termination"])

    def test_missing_skipped_duplicate_and_nonfinite_points(self):
        self.reject_change(("data", "tracks", 6, "points"), [])
        for value in (0, 24, "12", True):
            with self.subTest(hour=value):
                self.reject_change(("data", "tracks", 6, "points", 1, "fhour"), value)
        for key in ("lat", "lon", "mslp_hpa"):
            with self.subTest(key=key):
                self.reject_change(("data", "tracks", 6, "points", 1, key), float("nan"))

    def test_short_track_status_and_noise_flags(self):
        for status in ("C1", "NOISE", "UNCLASSIFIED"):
            with self.subTest(status=status):
                self.reject_change(("data", "tracks", 6, "cluster"), status)
        for reasons in (["center_lost"], ["domain_exit"], ["invented"], ["pressure_discontinuity"]):
            with self.subTest(reasons=reasons):
                self.reject_change(("data", "tracks", 6, "noiseReasons"), reasons)

    def test_unreported_quality_flags_rejected(self):
        for key, value in (("lat", 25), ("lat", -0.5), ("mslp_hpa", 1026)):
            with self.subTest(key=key, value=value):
                self.reject_change(("data", "tracks", 6, "points", 1, key), value)

    def test_malformed_cluster_accounting(self):
        for key, value in (("denominator", 6), ("conditionalDenominator", 6), ("share", 19.4), ("conditionalShare", 19.4), ("forecastHour", 240), ("count", 30), ("count", True), ("id", "TERMINATED")):
            with self.subTest(key=key):
                self.reject_change(("data", "clusters", 0, key), value)
        self.reject_change(("data", "tracks", 6, "scenarioCluster"), "C2")
        self.reject_change(("data", "tracks", 0, "cluster"), "C2")

    def test_duplicate_or_missing_cluster_member(self):
        members = self.case["data"]["clusters"][0]["members"]
        self.reject_change(("data", "clusters", 0, "members"), members[:-1] + [members[0]])
        self.reject_change(("data", "clusters", 0, "members"), members[:-1])

    def test_earlier_terminated_member_cannot_join_scenario(self):
        self.case = prefix_fixture([21] * 6 + [11] * 14 + [5] * 11)
        members = self.case["data"]["clusters"][0]["members"]
        self.reject_change(("data", "clusters", 0, "members"), members[:-1] + [self.case["data"]["tracks"][-1]["member"]])
        self.reject_change(("data", "tracks", 30, "scenarioCluster"), "C1")

    def test_new_history_fields_match_payload(self):
        for key, value in (("terminatedMembers", 24), ("validMembers", 30), ("clusterForecastHour", 240)):
            with self.subTest(key=key):
                self.reject_change(("history", "runs", 0, key), value)
            case = copy.deepcopy(self.case)
            del case["history"]["runs"][0][key]
            with self.subTest(missing=key), self.assertRaises(KeyError):
                validate_payload(**case)

    def test_identity_target_archive_and_history_stay_strict(self):
        for keys, value in ((("data", "meta", "trackingIdentity", "status"), "unverified"), (("data", "meta", "trackingAlgorithm"), "unknown-v3"), (("data", "meta", "trackingTargetId"), "other-target"), (("history", "runs", 0, "trackingTargetId"), "other-target"), (("latest", "status"), "analysis_pending")):
            with self.subTest(keys=keys):
                self.reject_change(keys, value)
        self.case["archive"] = copy.deepcopy(self.case["data"])
        self.reject_change(("archive", "meta", "trackingTargetId"), "other-target")

    def test_legacy_algorithm_does_not_accept_prefix_semantics(self):
        self.reject_change(("data", "meta", "trackingAlgorithm"), "normalized-domain-closed-minimum-v2")

    def test_legacy_partial_noise_archive_is_still_accepted(self):
        case = prefix_fixture([21] * 30 + [11])
        data = case["data"]
        data["meta"]["trackingAlgorithm"] = "normalized-domain-closed-minimum-v2"
        data["meta"].pop("clusterForecastHour")
        data["tracks"][-1]["cluster"] = "NOISE"
        data["tracks"][-1]["noiseReasons"] = ["center_lost"]
        data["summary"]["noiseMembers"] = 1
        case["history"]["runs"][0]["noiseMembers"] = 1
        for fields in (data["summary"], case["history"]["runs"][0]):
            for key in ("terminatedMembers", "validMembers", "clusteredMembers", "availableMembersByLead", "clusterForecastHour"):
                fields.pop(key, None)
        validate_payload(**case)


if __name__ == "__main__":
    unittest.main()
