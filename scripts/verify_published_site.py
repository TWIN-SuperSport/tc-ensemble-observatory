#!/usr/bin/env python3
"""Read-only verification of the public Pages payload after deployment."""
from __future__ import annotations
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


def validate_payload(data, latest, monitor, history, expected_target, minimum_init):
    meta, summary, tracks = data["meta"], data["summary"], data["tracks"]
    assert meta.get("trackingTargetId") == expected_target, "Published target episode differs"
    assert monitor.get("trackingTargetId") == expected_target, "Published monitoring episode differs"
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
    for track in tracks:
        points = track["points"]
        assert points and [p["fhour"] for p in points] == HOURS[:len(points)]
        assert all(math.isfinite(float(p[key])) for p in points for key in ("lat", "lon", "mslp_hpa"))
        if track["cluster"] != "NOISE":
            assert len(points) == 21 and not track["noiseReasons"]
        if len(points) != 21:
            assert track["cluster"] == "NOISE" and track.get("termination")
    assert history["latest"] == init and history["runCount"] == len(history["runs"])
    assert any(run["path"] == history["latestPath"] and run["init"] == init for run in history["runs"])
    return {"init": init, "trackingTargetId": expected_target, "summary": summary, "historyRunCount": history["runCount"]}


def self_test():
    import copy
    target = "test-episode"
    tracks = [{"member": m, "cluster": "C1", "noiseReasons": [], "points": [{"fhour": h, "lat": 10, "lon": 160, "mslp_hpa": 1000} for h in HOURS]} for m in sorted(MEMBERS)]
    data = {"meta": {"init": "2026100318", "trackingTargetId": target, "trackingAlgorithm": "normalized-domain-closed-minimum-v2", "trackingIdentity": {"status": "verified"}}, "summary": {"members": 31, "cleanMembers": 31, "noiseMembers": 0}, "tracks": tracks}
    latest = {"init": "2026100318", "status": "analysis_complete", "members": 31}
    monitor = {"trackingTargetId": target}
    history = {"latest": "2026100318", "latestPath": "2026100318.json", "runCount": 1, "runs": [{"path": "2026100318.json", "init": "2026100318"}]}
    validate_payload(data, latest, monitor, history, target, "2026100312")
    for mutation in ("target", "old", "count", "nan"):
        broken = copy.deepcopy(data)
        if mutation == "target": broken["meta"]["trackingTargetId"] = "old-episode"
        if mutation == "old": broken["meta"]["init"] = "2026100300"
        if mutation == "count": broken["summary"]["cleanMembers"] = 30
        if mutation == "nan": broken["tracks"][0]["points"][0]["lat"] = float("nan")
        try:
            validate_payload(broken, latest, monitor, history, target, "2026100312")
        except AssertionError:
            continue
        raise AssertionError(f"Failed to reject {mutation}")
    print("Published-payload self-test passed (valid/newer and four rejection cases)")


def main(base):
    assert base.startswith("https://"), "Expected public HTTPS Pages URL"
    expected = json.loads((ROOT / "data.json").read_text())
    config = json.loads((ROOT / "tracking_config.json").read_text())
    expected_history = json.loads((ROOT / "history/index.json").read_text())
    expected_paths = {run["path"] for run in expected_history["runs"]}
    target = config["trackingTargetId"]
    stamp = os.environ.get("GITHUB_SHA", "verification")
    def get(path, attempt):
        url = urllib.parse.urljoin(base.rstrip("/") + "/", path)
        url += "?verification=" + urllib.parse.quote(stamp + "-" + str(attempt))
        request = urllib.request.Request(url, headers={"User-Agent": "tc-ensemble-observatory-public-verification/1.0", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=20) as response:
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
            report = validate_payload(data, latest, monitor, history, target, str(expected["meta"]["init"]))
            assert expected_paths.issubset({run["path"] for run in history["runs"]}), "Published history entries missing"
            archive = json.loads(get("history/" + history["latestPath"], attempt))
            assert archive == data, "Published archive and live data differ"
            report.update({"status": "public-payload-verified", "deploymentCommit": stamp, "url": base})
            print(json.dumps(report, ensure_ascii=False))
            summary = os.environ.get("GITHUB_STEP_SUMMARY")
            if summary:
                with open(summary, "a") as handle:
                    handle.write("\nPublic Pages payload verified: " + json.dumps(report, ensure_ascii=False) + "\n")
            return
        except (AssertionError, KeyError, ValueError, OSError) as exc:
            last_error = exc
            print(f"Public verification attempt {attempt}/30: {type(exc).__name__}: {exc}", flush=True)
            if attempt < 30:
                time.sleep(10)
    raise RuntimeError(f"Public Pages verification did not converge: {last_error}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        assert len(sys.argv) == 2, "Usage: verify_published_site.py <https-pages-url>"
        main(sys.argv[1])
