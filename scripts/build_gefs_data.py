#!/usr/bin/env python3
"""Build data.json from all 31 GEFS members.

The pipeline deliberately fails closed: data.json is replaced only after all
members and forecast hours have been downloaded, decoded, tracked, classified,
and validated successfully.

This is an experimental tracker. It follows the nearest sea-level-pressure
minimum from a configured seed, not an operational tropical-cyclone tracker.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import io
import json
import math
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from eccodes import codes_get, codes_get_array, codes_grib_new_from_file, codes_release

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "data.json"
LATEST_PATH = ROOT / "latest_run.json"
CONFIG_PATH = ROOT / "tracking_config.json"
HISTORY_DIR = ROOT / "history"
S3_BASE = "https://noaa-gefs-pds.s3.amazonaws.com"
NOMADS_FILTER = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p50a.pl"
MEMBERS = ["c00"] + [f"p{i:02d}" for i in range(1, 31)]
FORECAST_HOURS = list(range(0, 241, 12))
USER_AGENT = "tc-ensemble-observatory/1.0"
TIMEOUT = 90


@dataclass
class TrackPoint:
    fhour: int
    lat: float
    lon: float
    mslp_hpa: float


def request(url: str, method: str = "GET", retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 403, 404):
                break
            if attempt + 1 < retries:
                import time
                time.sleep(2 ** attempt)
    raise RuntimeError(f"request failed: {url}: {last}")


def head_exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return 200 <= response.status < 400
    except Exception:
        return False


def member_prefix(member: str) -> str:
    return "gec00" if member == "c00" else f"ge{member}"


def idx_url(init: datetime, member: str, fhour: int) -> str:
    date, cycle = init.strftime("%Y%m%d"), init.strftime("%H")
    prefix = member_prefix(member)
    return (
        f"{S3_BASE}/gefs.{date}/{cycle}/atmos/pgrb2ap5/"
        f"{prefix}.t{cycle}z.pgrb2a.0p50.f{fhour:03d}.idx"
    )


def candidate_cycles(now: datetime, lookback_hours: int = 96) -> Iterable[datetime]:
    cursor = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(lookback_hours + 1):
        if cursor.hour in (0, 6, 12, 18):
            yield cursor
        cursor -= timedelta(hours=1)


def latest_complete_cycle(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    for cycle in candidate_cycles(now):
        # Probe both ends of the ensemble and the final requested forecast hour.
        if head_exists(idx_url(cycle, "c00", 240)) and head_exists(idx_url(cycle, "p30", 240)):
            return cycle
    raise RuntimeError("No complete GEFS cycle (+240h, c00 and p30) found")


def filter_url(init: datetime, member: str, fhour: int, box: dict[str, float]) -> str:
    date, cycle = init.strftime("%Y%m%d"), init.strftime("%H")
    prefix = member_prefix(member)
    filename = f"{prefix}.t{cycle}z.pgrb2a.0p50.f{fhour:03d}"
    directory = f"/gefs.{date}/{cycle}/atmos/pgrb2ap5"
    params = {
        "file": filename,
        "var_PRMSL": "on",
        "subregion": "",
        "leftlon": str(box["leftlon"]),
        "rightlon": str(box["rightlon"]),
        "toplat": str(box["toplat"]),
        "bottomlat": str(box["bottomlat"]),
        "dir": directory,
    }
    return NOMADS_FILTER + "?" + urllib.parse.urlencode(params)


def decode_prmsl(blob: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    handle = codes_grib_new_from_file(io.BytesIO(blob))
    if handle is None:
        raise RuntimeError("No GRIB message decoded")
    try:
        short_name = str(codes_get(handle, "shortName"))
        if short_name not in ("prmsl", "msl"):
            raise RuntimeError(f"Unexpected GRIB field: {short_name}")
        values = np.asarray(codes_get_array(handle, "values"), dtype=float)
        lats = np.asarray(codes_get_array(handle, "latitudes"), dtype=float)
        lons = np.asarray(codes_get_array(handle, "longitudes"), dtype=float)
        if np.nanmedian(values) > 2000:
            values = values / 100.0
        lons = np.mod(lons, 360.0)
        return lats, lons, values
    finally:
        codes_release(handle)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(((lon2 - lon1 + 180) % 360) - 180)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 6371.0 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))


def select_minimum(
    lats: np.ndarray,
    lons: np.ndarray,
    values: np.ndarray,
    previous: tuple[float, float],
    radius_km: float,
) -> tuple[float, float, float]:
    lat0, lon0 = previous
    # Fast equirectangular prefilter followed by exact distance for candidates.
    dx = (((lons - lon0 + 180) % 360) - 180) * np.cos(np.radians((lats + lat0) / 2)) * 111.32
    dy = (lats - lat0) * 110.57
    mask = (dx * dx + dy * dy) <= radius_km * radius_km
    if not np.any(mask):
        raise RuntimeError(f"No grid points within tracking radius near {lat0:.1f},{lon0:.1f}")
    candidate_indices = np.flatnonzero(mask)
    local = values[candidate_indices]
    order = np.argsort(local)
    # Prefer the deepest minimum, with a weak continuity penalty to avoid jumps.
    best_score = float("inf")
    best_idx = None
    for rel in order[: min(30, len(order))]:
        idx = int(candidate_indices[int(rel)])
        distance = haversine_km(lat0, lon0, float(lats[idx]), float(lons[idx]))
        score = float(values[idx]) + 0.0015 * distance
        if score < best_score:
            best_score, best_idx = score, idx
    assert best_idx is not None
    return float(lats[best_idx]), float(lons[best_idx]), float(values[best_idx])


def download_one(args: tuple[datetime, str, int, dict[str, float]]) -> tuple[str, int, bytes]:
    init, member, fhour, box = args
    blob = request(filter_url(init, member, fhour, box))
    if len(blob) < 100:
        raise RuntimeError(f"Suspiciously small GRIB response for {member} f{fhour:03d}")
    return member, fhour, blob


def build_tracks(init: datetime, config: dict) -> dict[str, list[TrackPoint]]:
    box = config["domain"]
    jobs = [(init, m, h, box) for m in MEMBERS for h in FORECAST_HOURS]
    blobs: dict[tuple[str, int], bytes] = {}
    workers = int(os.environ.get("GEFS_DOWNLOAD_WORKERS", "8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_one, job): job for job in jobs}
        for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
            member, fhour, blob = future.result()
            blobs[(member, fhour)] = blob
            if n % 100 == 0 or n == len(jobs):
                print(f"Downloaded {n}/{len(jobs)} fields")
    if len(blobs) != len(jobs):
        raise RuntimeError(f"Incomplete download: {len(blobs)}/{len(jobs)}")

    tracks: dict[str, list[TrackPoint]] = {}
    seed = (float(config["seed"]["lat"]), float(config["seed"]["lon"]) % 360)
    first_radius = float(config.get("initialSearchRadiusKm", 1800))
    step_radius = float(config.get("stepSearchRadiusKm", 1000))
    for member in MEMBERS:
        previous = seed
        points: list[TrackPoint] = []
        for fhour in FORECAST_HOURS:
            lats, lons, values = decode_prmsl(blobs[(member, fhour)])
            lat, lon, pressure = select_minimum(
                lats, lons, values, previous, first_radius if fhour == 0 else step_radius
            )
            points.append(TrackPoint(fhour, lat, lon, pressure))
            previous = (lat, lon)
        tracks[member] = points
    return tracks


def noise_reasons(points: list[TrackPoint]) -> list[str]:
    reasons: list[str] = []
    speeds = []
    for a, b in zip(points, points[1:]):
        hours = max(1, b.fhour - a.fhour)
        speeds.append(haversine_km(a.lat, a.lon, b.lat, b.lon) / hours)
    if speeds and max(speeds) > 85:
        reasons.append("translation_speed")
    if any(a.lat * b.lat < 0 for a, b in zip(points, points[1:])):
        reasons.append("equator_crossing")
    if any(abs(a.mslp_hpa - b.mslp_hpa) > 25 for a, b in zip(points, points[1:])):
        reasons.append("pressure_discontinuity")
    return reasons


def track_distance(a: list[TrackPoint], b: list[TrackPoint]) -> float:
    return float(np.mean([haversine_km(x.lat, x.lon, y.lat, y.lon) for x, y in zip(a, b)]))


def median_track(members: list[str], tracks: dict[str, list[TrackPoint]]) -> list[TrackPoint]:
    result = []
    for i, fhour in enumerate(FORECAST_HOURS):
        pts = [tracks[m][i] for m in members]
        lats = np.array([p.lat for p in pts])
        lons = np.unwrap(np.radians([p.lon for p in pts]))
        result.append(TrackPoint(
            fhour,
            float(np.median(lats)),
            float(np.degrees(np.median(lons)) % 360),
            float(np.median([p.mslp_hpa for p in pts])),
        ))
    return result


def cluster_tracks(clean_members: list[str], tracks: dict[str, list[TrackPoint]], threshold_km: float) -> list[list[str]]:
    groups: list[list[str]] = []
    for member in clean_members:
        best_group, best_distance = None, float("inf")
        for group in groups:
            med = median_track(group, tracks)
            distance = track_distance(tracks[member], med)
            if distance < best_distance:
                best_group, best_distance = group, distance
        if best_group is not None and best_distance <= threshold_km:
            best_group.append(member)
        else:
            groups.append([member])
    groups.sort(key=len, reverse=True)
    return groups


def scenario_label(points: list[TrackPoint]) -> str:
    start, end = points[0], points[-1]
    dlat = end.lat - start.lat
    dlon = ((end.lon - start.lon + 180) % 360) - 180
    if dlat >= 15 and dlon > 5:
        return "Recurve / east"
    if dlat >= 12:
        return "North / poleward"
    if dlon <= -12:
        return "West-northwest"
    return "Mixed / slow"


def point_json(member: str, p: TrackPoint) -> dict:
    return {
        "member": member,
        "fhour": p.fhour,
        "lat": round(p.lat, 2),
        "lon": round(p.lon, 2),
        "mslp_hpa": round(p.mslp_hpa, 1),
        "vmax_kt": None,
    }


def build_payload(init: datetime, config: dict, tracks: dict[str, list[TrackPoint]], previous: dict) -> dict:
    reasons = {m: noise_reasons(tracks[m]) for m in MEMBERS}
    clean = [m for m in MEMBERS if not reasons[m]]
    noise = [m for m in MEMBERS if reasons[m]]
    if len(clean) < 20:
        raise RuntimeError(f"Too many rejected members: clean={len(clean)}, noise={len(noise)}")
    groups = cluster_tracks(clean, tracks, float(config.get("clusterThresholdKm", 650)))
    clusters = []
    member_cluster: dict[str, str] = {m: "NOISE" for m in noise}
    for index, group in enumerate(groups, 1):
        cid = f"C{index}"
        med = median_track(group, tracks)
        for member in group:
            member_cluster[member] = cid
        clusters.append({
            "id": cid,
            "label": scenario_label(med),
            "members": group,
            "count": len(group),
            "share": round(len(group) / len(MEMBERS) * 100, 1),
            "medianTrack": [
                {"fhour": p.fhour, "lat": round(p.lat, 2), "lon": round(p.lon, 2)} for p in med
            ],
        })
    meta = dict(previous.get("meta", {}))
    meta.update({
        "title": "西太平洋台風進路予測観測所",
        "init": init.strftime("%Y%m%d%H"),
        "storm": config.get("storm", meta.get("storm", "WP90")),
        "model": "GEFS",
        "generatedFrom": "NOAA GEFS PRMSL experimental tracker",
        "stormInfo": config.get("stormInfo", meta.get("stormInfo", {})),
        "trackingSeed": config["seed"],
    })
    return {
        "meta": meta,
        "disclaimer": previous.get("disclaimer", {}),
        "summary": {
            "members": len(MEMBERS),
            "cleanMembers": len(clean),
            "noiseMembers": len(noise),
            "clusterCount": len(clusters),
        },
        "clusters": clusters,
        "tracks": [
            {
                "member": m,
                "cluster": member_cluster[m],
                "noiseReasons": reasons[m],
                "points": [point_json(m, p) for p in tracks[m]],
            }
            for m in MEMBERS
        ],
    }


def validate(payload: dict, expected_init: str) -> None:
    assert payload["meta"]["init"] == expected_init
    assert payload["summary"]["members"] == 31
    assert len(payload["tracks"]) == 31
    assert {t["member"] for t in payload["tracks"]} == set(MEMBERS)
    assert all(len(t["points"]) == len(FORECAST_HOURS) for t in payload["tracks"])
    assert payload["summary"]["cleanMembers"] + payload["summary"]["noiseMembers"] == 31


def write_atomically(payload: dict, init: datetime) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    HISTORY_DIR.mkdir(exist_ok=True)
    archive = HISTORY_DIR / f"{init.strftime('%Y%m%d%H')}.json"
    archive.write_text(text, encoding="utf-8")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ROOT, delete=False) as tmp:
        tmp.write(text)
        temp_path = Path(tmp.name)
    temp_path.replace(DATA_PATH)


def self_test() -> None:
    base = [TrackPoint(h, 8 + h / 24, 160 - h / 80, 1007 - h / 40) for h in FORECAST_HOURS]
    tracks = {}
    for i, member in enumerate(MEMBERS):
        tracks[member] = [TrackPoint(p.fhour, p.lat + (i % 5) * 0.15, p.lon + (i % 3) * 0.1, p.mslp_hpa) for p in base]
    tracks["p30"] = [TrackPoint(p.fhour, p.lat, p.lon if p.fhour < 120 else p.lon + 30, p.mslp_hpa) for p in base]
    previous = {"meta": {}, "disclaimer": {"ja": "test", "en": "test"}}
    config = {"seed": {"lat": 8, "lon": 160}, "storm": "WP90", "stormInfo": {"id": "90W", "candidateNumber": 11}, "clusterThresholdKm": 650}
    init = datetime(2026, 7, 16, 0, tzinfo=timezone.utc)
    payload = build_payload(init, config, tracks, previous)
    validate(payload, "2026071600")
    print("Self-test OK")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--force-init", help="YYYYMMDDHH, mainly for reproducible testing")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    previous = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    init = (
        datetime.strptime(args.force_init, "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if args.force_init
        else latest_complete_cycle()
    )
    init_string = init.strftime("%Y%m%d%H")
    if previous.get("meta", {}).get("init") == init_string and previous.get("summary", {}).get("members") == 31:
        print(f"data.json already contains complete run {init_string}")
        return 0

    print(f"Building GEFS analysis for {init_string}")
    tracks = build_tracks(init, config)
    payload = build_payload(init, config, tracks, previous)
    validate(payload, init_string)
    write_atomically(payload, init)
    latest = {
        "model": "GEFS",
        "init": init_string,
        "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": "NOAA GEFS / NOMADS",
        "status": "analysis_complete",
        "members": 31,
        "forecastHours": FORECAST_HOURS,
    }
    LATEST_PATH.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Successfully wrote data.json for {init_string}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
