#!/usr/bin/env python3
"""Read-only verification of the public Pages payload after deployment."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MEMBERS = {"c00"} | {f"p{i:02d}" for i in range(1, 31)}
HOURS = list(range(0, 241, 12))


def validate_pending_archive(data, monitor, history, expected_target, expected_data, archive):
    """Allow only a declared, quality-rejected switch to an unchanged archive."""
    assert monitor.get("analysisState") == "analysis_pending_quality", "Undeclared target mismatch"
    pending, archived = monitor["pendingAnalysis"], monitor["archivedAnalysis"]
    assert pending["trackingTargetId"] == expected_target, "Pending target episode differs"
    assert pending["status"] == "quality_rejected" and pending["reason"] == "center_lost"
    assert all(type(pending[key]) is int for key in ("members", "cleanMembers", "noiseMembers", "minimumCleanMembers"))
    assert pending["members"] == 31 and pending["minimumCleanMembers"] == 20
    assert 0 <= pending["cleanMembers"] < pending["minimumCleanMembers"]
    assert pending["noiseMembers"] == pending["members"] - pending["cleanMembers"]
    pending_init = pending["init"]
    assert isinstance(pending_init, str) and re.fullmatch(r"\d{10}", pending_init)
    pending_time = datetime.strptime(pending_init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    checked_at = pending["checkedAt"]
    assert isinstance(checked_at, str) and checked_at.endswith("Z")
    assert datetime.fromisoformat(checked_at[:-1] + "+00:00") >= pending_time
    assert re.fullmatch(r"https://github\.com/TWIN-SuperSport/tc-ensemble-observatory/actions/runs/[1-9]\d*", pending["runUrl"])

    init, target = str(data["meta"]["init"]), data["meta"]["trackingTargetId"]
    assert target != expected_target and archived["trackingTargetId"] == target, "Archived target episode differs"
    assert archived["init"] == init and pending_init >= init, "Archived or pending initialization differs"
    assert archived["path"] == "history/" + init + ".json"
    assert history["latestPath"] == init + ".json", "Archived history path differs"
    latest_entries = [run for run in history["runs"] if run["path"] == history["latestPath"]]
    assert len(latest_entries) == 1, "Archived history entry missing or duplicated"
    entry = latest_entries[0]
    assert entry["init"] == init and entry["trackingTargetId"] == target
    assert all(entry[key] == data["summary"][key] for key in ("members", "cleanMembers", "noiseMembers", "clusterCount"))
    # The declaration is not permission to relabel or replace the prior data.
    # Bind the public archive to this deployment's trusted, verified payload.
    assert expected_data is not None and data == expected_data, "Pending archive changed from deployment"
    assert archive is not None and archive == data, "Published archive and live data differ"


def validate_payload(data, latest, monitor, history, expected_target, minimum_init, *, expected_data=None, archive=None):
    meta, summary, tracks = data["meta"], data["summary"], data["tracks"]
    assert all(isinstance(data["disclaimer"][key], str) and data["disclaimer"][key] for key in ("ja", "en"))
    assert monitor.get("trackingTargetId") == expected_target, "Published monitoring episode differs"
    pending = meta.get("trackingTargetId") != expected_target
    if pending:
        validate_pending_archive(data, monitor, history, expected_target, expected_data, archive)
    # Same-target completed data always follows the strict current-data checks,
    # even if a previous rejection remains in the monitoring metadata.
    assert meta.get("trackingAlgorithm") == "normalized-domain-closed-minimum-v2"
    init = str(meta["init"])
    assert re.fullmatch(r"\d{10}", init) and init >= minimum_init, "Older published analysis"
    assert latest["init"] == init and latest["status"] == "analysis_complete"
    assert latest["members"] == summary["members"] == 31 and len(tracks) == 31
    assert {t["member"] for t in tracks} == MEMBERS
    clean = [t for t in tracks if t["cluster"] != "NOISE"]
    assert len(clean) == summary["cleanMembers"] >= 20
    assert summary["noiseMembers"] == 31 - len(clean)
    assert meta["trackingIdentity"]["status"] == "verified"
    clusters = data["clusters"]
    assert isinstance(clusters, list) and len(clusters) == summary["clusterCount"] > 0
    cluster_ids = [cluster["id"] for cluster in clusters]
    assert len(cluster_ids) == len(set(cluster_ids)) and "NOISE" not in cluster_ids
    assigned = {}
    for cluster in clusters:
        assert isinstance(cluster["label"], str) and cluster["label"]
        members = cluster["members"]
        assert members and len(members) == len(set(members)) == cluster["count"]
        assert abs(float(cluster["share"]) - round(len(members) / 31 * 100, 1)) < 1e-9
        for member in members:
            assert member in MEMBERS and member not in assigned
            assigned[member] = cluster["id"]
        median = cluster["medianTrack"]
        assert [point["fhour"] for point in median] == HOURS
        assert all(math.isfinite(float(point[key])) for point in median for key in ("lat", "lon"))
    assert set(assigned) == {track["member"] for track in clean}
    assert all(assigned[track["member"]] == track["cluster"] for track in clean)
    for track in tracks:
        assert isinstance(track["noiseReasons"], list) and all(isinstance(reason, str) for reason in track["noiseReasons"])
        points = track["points"]
        assert points and [p["fhour"] for p in points] == HOURS[:len(points)]
        assert all(math.isfinite(float(p[key])) for p in points for key in ("lat", "lon", "mslp_hpa"))
        if track["cluster"] != "NOISE":
            assert len(points) == 21 and not track["noiseReasons"]
        if len(points) != 21:
            assert track["cluster"] == "NOISE" and track.get("termination")
            termination = track["termination"]
            assert termination["reason"] in track["noiseReasons"]
            assert termination["lastValidForecastHour"] == points[-1]["fhour"]
            assert termination["atForecastHour"] == HOURS[len(points)]
        else:
            assert not track.get("termination")
    assert history["latest"] == init and history["runCount"] == len(history["runs"])
    assert any(run["path"] == history["latestPath"] and run["init"] == init for run in history["runs"])
    if archive is not None:
        assert archive == data, "Published archive and live data differ"
    return {"init": init, "trackingTargetId": meta["trackingTargetId"], "monitoringTargetId": expected_target, "analysisState": "analysis_pending_quality" if pending else "analysis_complete", "summary": summary, "historyRunCount": history["runCount"]}


def self_test():
    import copy
    target = "test-episode"
    tracks = [{"member": m, "cluster": "C1", "noiseReasons": [], "points": [{"fhour": h, "lat": 10, "lon": 160, "mslp_hpa": 1000} for h in HOURS]} for m in sorted(MEMBERS)]
    data = {"meta": {"init": "2026100318", "trackingTargetId": target, "trackingAlgorithm": "normalized-domain-closed-minimum-v2", "trackingIdentity": {"status": "verified"}}, "summary": {"members": 31, "cleanMembers": 31, "noiseMembers": 0, "clusterCount": 1}, "tracks": tracks, "disclaimer": {"ja": "test", "en": "test"}, "clusters": [{"id": "C1", "label": "test", "members": sorted(MEMBERS), "count": 31, "share": 100.0, "medianTrack": [{"fhour": h, "lat": 10, "lon": 160} for h in HOURS]}]}
    latest = {"init": "2026100318", "status": "analysis_complete", "members": 31}
    monitor = {"trackingTargetId": target}
    history = {"latest": "2026100318", "latestPath": "2026100318.json", "runCount": 1, "runs": [{"path": "2026100318.json", "init": "2026100318"}]}
    validate_payload(data, latest, monitor, history, target, "2026100312")
    for mutation in ("target", "old", "count", "nan", "clusters", "median", "termination", "disclaimer"):
        broken = copy.deepcopy(data)
        if mutation == "target": broken["meta"]["trackingTargetId"] = "old-episode"
        if mutation == "old": broken["meta"]["init"] = "2026100300"
        if mutation == "count": broken["summary"]["cleanMembers"] = 30
        if mutation == "nan": broken["tracks"][0]["points"][0]["lat"] = float("nan")
        if mutation == "clusters": broken["clusters"] = []
        if mutation == "median": broken["clusters"][0]["medianTrack"][0]["lat"] = float("nan")
        if mutation == "termination": broken["tracks"][0]["termination"] = {"reason": "center_lost"}
        if mutation == "disclaimer": broken["disclaimer"] = {}
        try:
            validate_payload(broken, latest, monitor, history, target, "2026100312")
        except (AssertionError, KeyError):
            continue
        raise AssertionError(f"Failed to reject {mutation}")
    old_target, new_target = "2026-10-invest-94w", "2026-10-invest-95w"
    old_init, new_init = "2026100506", "2026100906"
    archived_data = copy.deepcopy(data)
    archived_data["meta"].update(init=old_init, trackingTargetId=old_target)
    archived_latest = {"init": old_init, "status": "analysis_complete", "members": 31}
    archived_entry = {"path": old_init + ".json", "init": old_init, "trackingTargetId": old_target, **archived_data["summary"]}
    archived_history = {"latest": old_init, "latestPath": archived_entry["path"], "runCount": 1, "runs": [archived_entry]}
    pending_monitor = {
        "trackingTargetId": new_target,
        "analysisState": "analysis_pending_quality",
        "pendingAnalysis": {
            "trackingTargetId": new_target, "init": new_init,
            "checkedAt": "2026-10-09T11:46:43Z", "status": "quality_rejected",
            "cleanMembers": 10, "noiseMembers": 21, "members": 31,
            "minimumCleanMembers": 20, "reason": "center_lost",
            "runUrl": "https://github.com/TWIN-SuperSport/tc-ensemble-observatory/actions/runs/37925726531",
        },
        "archivedAnalysis": {"trackingTargetId": old_target, "init": old_init, "path": "history/" + old_init + ".json"},
    }
    fixture = {"data": archived_data, "latest": archived_latest, "monitor": pending_monitor, "history": archived_history, "expected_data": copy.deepcopy(archived_data), "archive": copy.deepcopy(archived_data)}

    def check(case):
        return validate_payload(**case, expected_target=new_target, minimum_init=old_init)

    rejection_count = 8

    def reject(label, case):
        nonlocal rejection_count
        try:
            check(case)
        except (AssertionError, KeyError, TypeError, ValueError):
            rejection_count += 1
            return
        raise AssertionError(f"Failed to reject {label}")

    before = copy.deepcopy(fixture)
    pending_report = check(fixture)
    assert pending_report["analysisState"] == "analysis_pending_quality"
    assert pending_report["trackingTargetId"] == old_target and pending_report["monitoringTargetId"] == new_target
    assert check(fixture) == check(fixture) == pending_report
    assert fixture == before, "Repeated validation mutated the payload"

    mutations = [
        (("monitor", "analysisState"), "awaiting_analysis"),
        (("monitor", "trackingTargetId"), old_target),
        (("monitor", "pendingAnalysis", "trackingTargetId"), old_target),
        (("monitor", "pendingAnalysis", "status"), "analysis_complete"),
        (("monitor", "pendingAnalysis", "reason"), "unverified"),
        (("monitor", "pendingAnalysis", "members"), 30),
        (("monitor", "pendingAnalysis", "cleanMembers"), 20),
        (("monitor", "pendingAnalysis", "cleanMembers"), -1),
        (("monitor", "pendingAnalysis", "cleanMembers"), True),
        (("monitor", "pendingAnalysis", "noiseMembers"), 20),
        (("monitor", "pendingAnalysis", "minimumCleanMembers"), 10),
        (("monitor", "pendingAnalysis", "init"), "2026100318"),
        (("monitor", "pendingAnalysis", "init"), "2026130906"),
        (("monitor", "pendingAnalysis", "checkedAt"), "2026-10-09T05:00:00Z"),
        (("monitor", "pendingAnalysis", "checkedAt"), "2026-10-09T11:46:43"),
        (("monitor", "pendingAnalysis", "runUrl"), "https://example.org/actions/runs/37925726531"),
        (("monitor", "archivedAnalysis", "trackingTargetId"), new_target),
        (("monitor", "archivedAnalysis", "init"), new_init),
        (("monitor", "archivedAnalysis", "path"), "history/other.json"),
        (("history", "latest"), new_init),
        (("history", "latestPath"), new_init + ".json"),
        (("history", "runs", 0, "trackingTargetId"), new_target),
        (("history", "runs", 0, "cleanMembers"), 10),
        (("latest", "init"), new_init),
        (("latest", "status"), "quality_rejected"),
        (("latest", "members"), 30),
        (("expected_data",), None),
        (("archive",), None),
        (("archive", "meta", "trackingTargetId"), new_target),
        (("data", "tracks", 0, "points", 0, "lat"), 11),
    ]
    for keys, value in mutations:
        broken = copy.deepcopy(fixture)
        parent = broken
        for key in keys[:-1]:
            parent = parent[key]
        parent[keys[-1]] = value
        reject("pending " + ".".join(map(str, keys)), broken)
    for field in ("analysisState", "pendingAnalysis", "archivedAnalysis"):
        broken = copy.deepcopy(fixture)
        del broken["monitor"][field]
        reject("missing " + field, broken)
    for field in pending_monitor["pendingAnalysis"]:
        broken = copy.deepcopy(fixture)
        del broken["monitor"]["pendingAnalysis"][field]
        reject("missing pending " + field, broken)
    broken = copy.deepcopy(fixture)
    broken["history"]["runs"].append(copy.deepcopy(archived_entry))
    broken["history"]["runCount"] = 2
    reject("duplicate archived history entry", broken)
    # Even internally matching metadata cannot authorize another old episode.
    broken = copy.deepcopy(fixture)
    for payload in (broken["data"], broken["archive"]):
        payload["meta"]["trackingTargetId"] = "unrelated-episode"
    broken["monitor"]["archivedAnalysis"]["trackingTargetId"] = "unrelated-episode"
    broken["history"]["runs"][0]["trackingTargetId"] = "unrelated-episode"
    reject("undeployed archive episode", broken)
    # All original quality checks still run on a declared archive, even when
    # the local baseline and fetched archive contain the same malformed data.
    for mutation in ("identity", "algorithm", "nan", "cluster", "count"):
        broken = copy.deepcopy(fixture)
        payload = broken["data"]
        if mutation == "identity": payload["meta"]["trackingIdentity"]["status"] = "unverified"
        if mutation == "algorithm": payload["meta"]["trackingAlgorithm"] = "unverified"
        if mutation == "nan": payload["tracks"][0]["points"][0]["lat"] = float("nan")
        if mutation == "cluster": payload["clusters"] = []
        if mutation == "count": payload["summary"]["cleanMembers"] = 19
        broken["archive"] = copy.deepcopy(payload)
        broken["expected_data"] = copy.deepcopy(payload)
        broken["history"]["runs"][0].update(payload["summary"])
        reject("invalid archived " + mutation, broken)

    current = copy.deepcopy(fixture)
    current["data"]["meta"].update(init=new_init, trackingTargetId=new_target)
    current["archive"] = copy.deepcopy(current["data"])
    current["latest"]["init"] = new_init
    new_entry = {"path": new_init + ".json", "init": new_init, "trackingTargetId": new_target, **current["data"]["summary"]}
    current["history"] = {"latest": new_init, "latestPath": new_entry["path"], "runCount": 2, "runs": [new_entry, archived_entry]}
    # A successful run supersedes pending metadata without depending on its
    # removal; stale or incomplete old declarations must not block recovery.
    current["monitor"]["pendingAnalysis"] = {"status": "stale"}
    current["monitor"]["archivedAnalysis"] = {"init": "stale"}
    current_report = check(current)
    assert current_report["analysisState"] == "analysis_complete" and current_report["trackingTargetId"] == new_target
    assert check(current) == current_report
    for mutation in ("quality", "identity", "older", "archive"):
        broken = copy.deepcopy(current)
        if mutation == "quality": broken["data"]["summary"]["cleanMembers"] = 19
        if mutation == "identity": broken["data"]["meta"]["trackingIdentity"]["status"] = "unverified"
        if mutation == "older": broken["data"]["meta"]["init"] = "2026100318"
        if mutation == "archive": broken["archive"]["meta"]["trackingTargetId"] = old_target
        reject("current " + mutation + " despite stale pending metadata", broken)
    rollback = copy.deepcopy(fixture)
    rollback["expected_data"] = copy.deepcopy(current["data"])
    reject("old archive after current-target deployment", rollback)
    print(f"Published-payload self-test passed (current, pending, repeated checks, recovery and {rejection_count} rejection cases)")


def main(base):
    assert base.startswith("https://"), "Expected public HTTPS Pages URL"
    expected = json.loads((ROOT / "data.json").read_text())
    config = json.loads((ROOT / "tracking_config.json").read_text())
    expected_history = json.loads((ROOT / "history/index.json").read_text())
    expected_paths = {run["path"] for run in expected_history["runs"]}
    target = config["trackingTargetId"]
    stamp = os.environ.get("GITHUB_SHA", "verification")
    deadline = time.monotonic() + 300
    def get(path, attempt):
        url = urllib.parse.urljoin(base.rstrip("/") + "/", path)
        url += "?verification=" + urllib.parse.quote(stamp + "-" + str(attempt))
        request = urllib.request.Request(url, headers={"User-Agent": "tc-ensemble-observatory-public-verification/1.0", "Cache-Control": "no-cache"})
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Public verification deadline exceeded")
        with urllib.request.urlopen(request, timeout=min(20, remaining)) as response:
            assert response.status == 200
            return response.read()
    last_error = None
    for attempt in range(1, 31):
        try:
            # These immutable assets must come from this deployment. Newer
            # completed analyses of the same episode are allowed below.
            for path in ("index.html", "app.js"):
                assert hashlib.sha256(get(path, attempt)).digest() == hashlib.sha256((ROOT / path).read_bytes()).digest(), f"Stale {path}"
            data, latest, monitor, history = [json.loads(get(path, attempt)) for path in ("data.json", "latest_run.json", "monitor_status.json", "history/index.json")]
            assert re.fullmatch(r"\d{10}\.json", history["latestPath"]), "Invalid published archive path"
            archive = json.loads(get("history/" + history["latestPath"], attempt))
            report = validate_payload(data, latest, monitor, history, target, str(expected["meta"]["init"]), expected_data=expected, archive=archive)
            assert expected_paths.issubset({run["path"] for run in history["runs"]}), "Published history entries missing"
            report.update({"status": "public-payload-verified", "deploymentCommit": stamp, "url": base})
            print(json.dumps(report, ensure_ascii=False))
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a") as handle:
                    handle.write("\nPublic Pages payload verified: " + json.dumps(report, ensure_ascii=False) + "\n")
            return
        except (AssertionError, KeyError, TypeError, ValueError, OSError) as exc:
            last_error = exc
            print(f"Public verification attempt {attempt}/30: {type(exc).__name__}: {exc}", flush=True)
            remaining = deadline - time.monotonic()
            if attempt >= 30 or remaining <= 0:
                break
            time.sleep(min(10, remaining))
    raise RuntimeError(f"Public Pages verification did not converge: {last_error}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        assert len(sys.argv) == 2, "Usage: verify_published_site.py <https-pages-url>"
        main(sys.argv[1])

