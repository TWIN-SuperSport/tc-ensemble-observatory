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
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
from eccodes import codes_get, codes_get_array, codes_new_from_message, codes_release
from history_index import write_history_index

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
JTWC_ABPW_URL = "https://www.metoc.navy.mil/jtwc/products/abpwweb.txt"
JMA_TARGET_TC_URL = "https://www.jma.go.jp/bosai/typhoon/data/targetTc.json"
JMA_FORECAST_URL = "https://www.jma.go.jp/bosai/typhoon/data/{tc_id}/forecast.json"


@dataclass
class TrackPoint:
    fhour: int
    lat: float
    lon: float
    mslp_hpa: float


def normalize_storm_id(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def storm_aliases(config: dict) -> set[str]:
    info = config.get("stormInfo", {})
    values = [
        config.get("storm"),
        info.get("id"),
        *info.get("aliases", []),
        *config.get("seedResolver", {}).get("aliases", []),
    ]
    return {normalize_storm_id(value) for value in values if value}


def same_tracking_target(previous: dict, config: dict) -> bool:
    """Return whether saved metadata and config refer to the same initial ID."""
    previous_info = previous.get("meta", {}).get("stormInfo", {})
    previous_ids = {
        normalize_storm_id(previous_info.get("id")),
        *{
            normalize_storm_id(value)
            for value in previous_info.get("aliases", [])
            if value
        },
    }
    previous_ids.discard("")
    return bool(previous_ids.intersection(storm_aliases(config)))


def matches_storm_alias(storm_id: str, storm_name: str | None, aliases: set[str]) -> bool:
    """Return whether a JTWC bulletin identity belongs to this tracked storm."""
    candidates = {normalize_storm_id(storm_id), normalize_storm_id(storm_name)}
    return bool(candidates.intersection(aliases))


def normalize_name(value: object) -> str:
    """Normalize romanized cyclone names for cross-agency matching."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def parse_jtwc_named_systems(text: str) -> list[dict]:
    """Return all active named JTWC systems with a position from a bulletin.

    ABPW and warning text use slightly different lead-ins, but both keep the
    storm serial, official name, and a ``NEAR <lat> <lon>`` position together.
    Keeping the complete list lets an Invest retain its identity after JTWC
    assigns a warning number instead of assuming that e.g. 96W becomes 96W.
    """
    systems: list[dict] = []
    pattern = re.compile(
        r"(?:SUPER\s+)?(?:TYPHOON|TROPICAL\s+STORM|TROPICAL\s+DEPRESSION)\s+"
        r"([0-9]{2}[A-Z])\s+\(([^)]+)\).*?"
        r"(?:WARNING\s+POSITION:\s*(?:[0-9]{6}Z\s+---\s*)?|WAS\s+LOCATED\s+NEAR\s+)"
        r"(?:NEAR\s+)?([0-9]+(?:\.[0-9]+)?)([NS])\s+"
        r"([0-9]+(?:\.[0-9]+)?)([EW])",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        storm_id, storm_name, lat_raw, lat_hemi, lon_raw, lon_hemi = match.groups()
        lat = float(lat_raw) * (-1 if lat_hemi.upper() == "S" else 1)
        lon = float(lon_raw)
        if lon_hemi.upper() == "W":
            lon = (360.0 - lon) % 360.0
        item = {
            "id": normalize_storm_id(storm_id),
            "name": storm_name.strip().upper(),
            "lat": lat,
            "lon": lon % 360.0,
        }
        if not any(existing["id"] == item["id"] for existing in systems):
            systems.append(item)
    return systems


def parse_jma_forecast_identity(
    tc_id: str,
    text: str,
    *,
    category: str | None = None,
    candidate_number: int | None = None,
) -> dict | None:
    """Extract JMA's number/name/current centre from a forecast payload.

    JMA uses ``typhoonNumber: \"a\"`` while publishing a developing tropical
    depression.  In that state the configured next-number candidate may be
    displayed, but it remains explicitly marked as a candidate until JMA
    assigns a four-digit annual typhoon number.
    """
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list) or not items:
        return None
    title = next((item for item in items if item.get("part") == "title"), {})
    number_code = str(title.get("typhoonNumber") or "")
    official_number = bool(re.fullmatch(r"\d{4}", number_code))
    if official_number:
        number = int(number_code[-2:])
        status = "typhoon"
    elif str(category or "").upper() == "TD" and candidate_number:
        number = int(candidate_number)
        status = "candidate"
    else:
        return None
    analysis = next((item for item in items if item.get("advancedHours") == 0), {})
    center = analysis.get("center")
    if not isinstance(center, list) or len(center) != 2:
        return None
    name = title.get("name") or {}
    return {
        "tropicalCyclone": tc_id,
        "number": number,
        "numberCode": number_code,
        "numberOfficial": official_number,
        "status": status,
        "category": str(category or ""),
        "nameJa": str(name.get("jp") or ""),
        "nameEn": str(name.get("en") or "").upper(),
        "lat": float(center[0]),
        "lon": float(center[1]) % 360.0,
        "issue": (title.get("issue") or {}).get("JST"),
        "sourceUrl": JMA_FORECAST_URL.format(tc_id=tc_id),
    }


def official_identity_aliases(previous: dict, config: dict) -> set[str]:
    """Collect retained identifiers, including serials learned on earlier runs."""
    aliases = storm_aliases(config)
    if not same_tracking_target(previous, config):
        return aliases
    prior_info = previous.get("meta", {}).get("stormInfo", {})
    aliases.update(normalize_storm_id(value) for value in prior_info.get("aliases", []) if value)
    prior_identity = previous.get("meta", {}).get("officialIdentity", {})
    for source in ("invest", "jtwc"):
        value = (prior_identity.get(source) or {}).get("id")
        if value:
            aliases.add(normalize_storm_id(value))
    for source in ("jtwc", "jma"):
        value = (prior_identity.get(source) or {}).get("name")
        if value:
            aliases.add(normalize_name(value))
    return aliases


def closest_system(systems: list[dict], lat: float, lon: float, max_distance_km: float) -> dict | None:
    ranked = sorted(
        ((haversine_km(lat, lon, item["lat"], item["lon"]), item) for item in systems),
        key=lambda item: item[0],
    )
    if not ranked or ranked[0][0] > max_distance_km:
        return None
    # A near-tie is not sufficient evidence to promote an Invest to a named
    # system.  Leave the lookup unresolved rather than making a silent jump.
    if len(ranked) > 1 and ranked[1][0] - ranked[0][0] < 120:
        return None
    chosen = dict(ranked[0][1])
    chosen["distanceKm"] = round(ranked[0][0], 1)
    return chosen


def parse_jtwc_named_seed(text: str, aliases: Iterable[str]) -> tuple[float, float, str] | None:
    """Extract a named cyclone center from a JTWC warning or ABPW summary."""
    normalized_aliases = {normalize_storm_id(value) for value in aliases}
    pattern = re.compile(
        r"(?:SUPER\s+)?(?:TYPHOON|TROPICAL\s+STORM|TROPICAL\s+DEPRESSION)\s+"
        r"([0-9]{2}[A-Z])\s+\(([^)]+)\).*?"
        r"(?:WARNING\s+POSITION:\s*(?:[0-9]{6}Z\s+---\s*)?"
        r"|WAS\s+LOCATED\s+NEAR\s+)"
        r"(?:NEAR\s+)?([0-9]+(?:\.[0-9]+)?)([NS])\s+"
        r"([0-9]+(?:\.[0-9]+)?)([EW])",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        storm_id, storm_name, lat_raw, lat_hemi, lon_raw, lon_hemi = match.groups()
        if not matches_storm_alias(storm_id, storm_name, normalized_aliases):
            continue
        lat = float(lat_raw) * (-1 if lat_hemi.upper() == "S" else 1)
        lon = float(lon_raw)
        if lon_hemi.upper() == "W":
            lon = (360.0 - lon) % 360.0
        return lat, lon % 360.0, normalize_storm_id(storm_id)
    return None


def parse_jtwc_abpw_seed(text: str, aliases: Iterable[str]) -> tuple[float, float, str] | None:
    """Extract an Invest center from JTWC ABPW text.

    The storm suffix is intentionally preserved. This lets a Central Pacific
    Invest such as 92C remain the same tracked object after it crosses 180E,
    instead of being silently relabeled as a guessed 92W.
    """
    normalized_aliases = {normalize_storm_id(value) for value in aliases}
    invest_headers = list(
        re.finditer(r"\(INVEST\s+([0-9]{2}[A-Z])\)", text, flags=re.IGNORECASE)
    )
    for index, header in enumerate(invest_headers):
        storm_id = header.group(1)
        if normalize_storm_id(storm_id) not in normalized_aliases:
            continue
        end_candidates = []
        if index + 1 < len(invest_headers):
            end_candidates.append(invest_headers[index + 1].start())
        # An ABPW bulletin may contain only one Invest, followed by an
        # unrelated subtropical or other-area paragraph.  Stop at the next
        # section/item boundary so its LOCATED NEAR position is not mistaken
        # for the Invest's current centre.
        boundary = re.search(
            r"(?m)^\s*(?:[A-Z]\.\s+|\d+\.\s+|\(\d+\)\s+)",
            text[header.end():],
        )
        if boundary:
            end_candidates.append(header.end() + boundary.start())
        end = min(end_candidates, default=len(text))
        block = text[header.end():end]
        positions = list(
            re.finditer(
                r"(?:PERSISTED|LOCATED)\s+NEAR\s+"
                r"([0-9]+(?:\.[0-9]+)?)([NS])\s+"
                r"([0-9]+(?:\.[0-9]+)?)([EW])",
                block,
                flags=re.IGNORECASE,
            )
        )
        if not positions:
            continue
        # ABPW commonly says "PREVIOUSLY LOCATED ... IS NOW LOCATED ...".
        # The last position in that Invest block is the current centre.
        lat_raw, lat_hemi, lon_raw, lon_hemi = positions[-1].groups()
        lat = float(lat_raw) * (-1 if lat_hemi.upper() == "S" else 1)
        lon = float(lon_raw)
        if lon_hemi.upper() == "W":
            lon = (360.0 - lon) % 360.0
        return lat, lon % 360.0, normalize_storm_id(storm_id)
    return None


def previous_forecast_seed(
    previous: dict,
    init: datetime,
    aliases: set[str],
    max_hours: int,
) -> tuple[float, float] | None:
    previous_meta = previous.get("meta", {})
    # A legacy run that was never identity-verified must not become the seed
    # for all later runs. That is exactly how an old Invest label can keep a
    # tracker attached to an unrelated weak low after formal designation.
    if previous_meta.get("trackingIdentity", {}).get("status") != "verified":
        return None
    previous_aliases = {
        normalize_storm_id(previous_meta.get("storm")),
        normalize_storm_id(previous_meta.get("stormInfo", {}).get("id")),
        *{
            normalize_storm_id(value)
            for value in previous_meta.get("stormInfo", {}).get("aliases", [])
        },
    }
    if not aliases.intersection(previous_aliases):
        return None
    try:
        previous_init = datetime.strptime(
            str(previous_meta["init"]), "%Y%m%d%H"
        ).replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None
    elapsed = int((init - previous_init).total_seconds() // 3600)
    if elapsed < 0 or elapsed > max_hours:
        return None
    clean_points = []
    for track in previous.get("tracks", []):
        if track.get("cluster") == "NOISE":
            continue
        point = next(
            (item for item in track.get("points", []) if int(item.get("fhour", -1)) == elapsed),
            None,
        )
        if point:
            clean_points.append(point)
    if not clean_points:
        return None
    lat = float(np.median([point["lat"] for point in clean_points]))
    lon_radians = np.unwrap(np.radians([point["lon"] for point in clean_points]))
    lon = float(np.degrees(np.median(lon_radians)) % 360.0)
    return lat, lon


def resolve_official_identity(config: dict, previous: dict, init: datetime) -> dict:
    """Resolve Invest, JTWC, and JMA identities from their original feeds.

    Each source is retained independently.  An Invest designation can disappear
    immediately after warning issuance, while JMA uses a separate annual number.
    We therefore link the records by retained aliases and, only when necessary,
    a conservative continuity check against the previous verified GEFS position.
    """
    resolver = config.get("identityResolver", {})
    abpw_url = str(resolver.get("jtwcAbpwUrl") or JTWC_ABPW_URL)
    jma_target_url = str(resolver.get("jmaTargetUrl") or JMA_TARGET_TC_URL)
    aliases = official_identity_aliases(previous, config)
    info = json.loads(json.dumps(config.get("stormInfo", {})))
    invest_id = normalize_storm_id(info.get("id"))
    identity = {
        "resolvedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "invest": {"id": invest_id or None, "sourceUrl": abpw_url, "status": "historical"},
        "jtwc": None,
        "jma": None,
        "linkage": {"method": "unresolved", "confidence": "none"},
    }
    named_system: dict | None = None
    try:
        abpw_text = request(abpw_url).decode("utf-8", errors="replace")
        invest = parse_jtwc_abpw_seed(abpw_text, {invest_id} if invest_id else set())
        if invest:
            lat, lon, current_invest_id = invest
            identity["invest"] = {
                "id": current_invest_id,
                "lat": lat,
                "lon": lon,
                "sourceUrl": abpw_url,
                "status": "active",
            }
            identity["linkage"] = {"method": "JTWC ABPW Invest", "confidence": "official"}

        systems = parse_jtwc_named_systems(abpw_text)
        direct = next(
            (item for item in systems if matches_storm_alias(item["id"], item["name"], aliases)),
            None,
        )
        if direct:
            named_system = direct
            identity["linkage"] = {"method": "JTWC retained alias", "confidence": "official"}
        elif not invest:
            prior = previous_forecast_seed(
                previous,
                init,
                storm_aliases(config),
                int(config.get("seedResolver", {}).get("previousForecastFallbackHours", 24)),
            )
            if prior:
                promoted = closest_system(
                    systems,
                    prior[0],
                    prior[1],
                    float(resolver.get("promotionMaxDistanceKm", 600)),
                )
                if promoted:
                    named_system = promoted
                    identity["linkage"] = {
                        "method": "JTWC serial promotion / previous verified position",
                        "confidence": "continuity",
                        "distanceKm": promoted["distanceKm"],
                    }
    except RuntimeError as exc:
        identity["jtwcError"] = str(exc)

    if named_system:
        serial = named_system["id"]
        year = init.strftime("%y")
        warning_url = f"https://www.metoc.navy.mil/jtwc/products/wp{serial[:2]}{year}web.txt"
        jtwc = {
            "id": serial,
            "name": named_system["name"],
            "lat": named_system["lat"],
            "lon": named_system["lon"],
            "summaryUrl": abpw_url,
            "warningUrl": warning_url,
        }
        try:
            warning_text = request(warning_url).decode("utf-8", errors="replace")
            parsed = parse_jtwc_named_seed(warning_text, {serial, normalize_name(named_system["name"])})
            if parsed:
                lat, lon, parsed_serial = parsed
                jtwc.update({"id": parsed_serial, "lat": lat, "lon": lon})
        except RuntimeError as exc:
            jtwc["warningError"] = str(exc)
        identity["jtwc"] = jtwc

    # JMA's target list supplies currently published TC IDs.  Its individual
    # forecast JSON is the authoritative source for JMA's annual typhoon number
    # and official Japanese/English names.
    try:
        target_items = json.loads(request(jma_target_url).decode("utf-8", errors="replace"))
        jma_candidates: list[dict] = []
        for item in target_items if isinstance(target_items, list) else []:
            tc_id = str(item.get("tropicalCyclone") or "")
            if not re.fullmatch(r"TC\d{4}", tc_id):
                continue
            try:
                candidate = parse_jma_forecast_identity(
                    tc_id,
                    request(JMA_FORECAST_URL.format(tc_id=tc_id)).decode("utf-8", errors="replace"),
                    category=str(item.get("category") or ""),
                    candidate_number=info.get("candidateNumber"),
                )
            except RuntimeError:
                continue
            if candidate:
                jma_candidates.append(candidate)
        jtwc_name = normalize_name((identity.get("jtwc") or {}).get("name"))
        jma = next(
            (item for item in jma_candidates if jtwc_name and normalize_name(item["nameEn"]) == jtwc_name),
            None,
        )
        reference = identity.get("jtwc")
        if not reference and (identity.get("invest") or {}).get("status") == "active":
            reference = identity["invest"]
        if not jma and reference:
            jma = closest_system(
                jma_candidates,
                float(reference["lat"]),
                float(reference["lon"]),
                float(resolver.get("jmaMatchMaxDistanceKm", 650)),
            )
        if jma:
            identity["jma"] = jma
    except RuntimeError as exc:
        identity["jmaError"] = str(exc)
    except json.JSONDecodeError as exc:
        identity["jmaError"] = f"invalid JMA target list: {exc}"

    # An upstream timeout must not erase a previously verified serial/name from
    # the public page.  Preserve it explicitly as stale until the source can be
    # checked again; a stale record is safer and more honest than a relabel.
    prior_identity = (
        previous.get("meta", {}).get("officialIdentity", {})
        if same_tracking_target(previous, config)
        else {}
    )
    for source in ("invest", "jtwc", "jma"):
        if not identity.get(source) and prior_identity.get(source):
            identity[source] = dict(prior_identity[source])
            identity[source]["status"] = "stale"

    aliases_to_keep = list(info.get("aliases", []))
    for value in (invest_id, (identity.get("jtwc") or {}).get("id"), (identity.get("jtwc") or {}).get("name")):
        if value and value not in aliases_to_keep:
            aliases_to_keep.append(value)
    info["aliases"] = aliases_to_keep
    if identity.get("jtwc"):
        info["jtwcNumber"] = identity["jtwc"]["id"]
        info["name"] = identity["jtwc"]["name"]
    if identity.get("jma"):
        info["status"] = identity["jma"].get("status", "typhoon")
        info["number"] = identity["jma"]["number"]
        info["candidateNumber"] = identity["jma"]["number"]
        info["name"] = identity["jma"]["nameEn"] or info.get("name")
        info["nameJa"] = identity["jma"]["nameJa"]
    identity["stormInfo"] = info
    return identity


def refresh_existing_identity(previous: dict, config: dict, init: datetime) -> bool:
    """Refresh only official ID metadata when GEFS already has this cycle.

    The fallback scheduled run should still reflect an Invest being promoted to
    a named storm; it must not wait for a new ensemble cycle merely to update a
    label.  The track data itself remains byte-for-byte untouched.
    """
    identity = resolve_official_identity(config, previous, init)
    meta = previous.setdefault("meta", {})
    def signature(storm_info: dict | None, official: dict | None) -> dict:
        official = official or {}
        return {
            "stormInfo": storm_info or {},
            "invest": {key: (official.get("invest") or {}).get(key) for key in ("id", "status")},
            "jtwc": {key: (official.get("jtwc") or {}).get(key) for key in ("id", "name")},
            "jma": {key: (official.get("jma") or {}).get(key) for key in ("tropicalCyclone", "number", "numberOfficial", "status", "category", "nameJa", "nameEn")},
        }
    before = json.dumps(signature(meta.get("stormInfo"), meta.get("officialIdentity")), sort_keys=True)
    meta["stormInfo"] = identity["stormInfo"]
    meta["officialIdentity"] = identity
    after = json.dumps(signature(meta.get("stormInfo"), meta.get("officialIdentity")), sort_keys=True)
    if before == after:
        return False
    write_atomically(previous, init)
    if LATEST_PATH.exists():
        latest = json.loads(LATEST_PATH.read_text(encoding="utf-8"))
        latest["checkedAt"] = identity["resolvedAt"]
        latest["identityCheckedAt"] = identity["resolvedAt"]
        LATEST_PATH.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Refreshed official identity metadata for {init.strftime('%Y%m%d%H')}")
    return True


def resolve_tracking_seed(config: dict, previous: dict, init: datetime) -> dict:
    resolved = json.loads(json.dumps(config))
    resolver = resolved.get("seedResolver", {})
    fallback = resolved["seed"]
    resolution = {
        "lat": float(fallback["lat"]),
        "lon": float(fallback["lon"]) % 360.0,
        "source": "tracking_config",
    }
    if not resolver.get("enabled", False):
        resolved["seed"] = {"lat": resolution["lat"], "lon": resolution["lon"]}
        resolved["_seedResolution"] = resolution
        return resolved

    official_identity = resolve_official_identity(resolved, previous, init)
    resolved["stormInfo"] = official_identity["stormInfo"]
    resolved["_officialIdentity"] = official_identity
    aliases = official_identity_aliases(previous, resolved)
    if official_identity.get("jtwc"):
        jtwc = official_identity["jtwc"]
        resolution = {
            "lat": jtwc["lat"],
            "lon": jtwc["lon"],
            "source": "JTWC warning" if not jtwc.get("warningError") else "JTWC ABPW",
            "sourceStormId": jtwc["id"],
            "sourceUrl": jtwc.get("warningUrl") if not jtwc.get("warningError") else jtwc["summaryUrl"],
        }
    elif official_identity.get("invest", {}).get("status") == "active":
        invest = official_identity["invest"]
        resolution = {
            "lat": invest["lat"],
            "lon": invest["lon"],
            "source": "JTWC ABPW",
            "sourceStormId": invest["id"],
            "sourceUrl": invest["sourceUrl"],
        }
    for key, source_name, parser in (
        ("warningUrl", "JTWC warning", parse_jtwc_named_seed),
        ("officialUrl", "JTWC ABPW", parse_jtwc_named_seed),
    ):
        official_url = resolver.get(key)
        if resolution["source"] != "tracking_config" or not official_url:
            continue
        try:
            bulletin = request(str(official_url)).decode("utf-8", errors="replace")
            official = parser(bulletin, aliases)
            # ABPW keeps Invest reports in a different textual form.
            if not official and key == "officialUrl":
                official = parse_jtwc_abpw_seed(bulletin, aliases)
            if official:
                lat, lon, storm_id = official
                resolution = {
                    "lat": lat,
                    "lon": lon,
                    "source": source_name,
                    "sourceStormId": storm_id,
                    "sourceUrl": official_url,
                }
        except RuntimeError as exc:
            print(f"Official seed lookup unavailable: {exc}", file=sys.stderr)

    if resolution["source"] == "tracking_config":
        prior = previous_forecast_seed(
            previous,
            init,
            aliases,
            int(resolver.get("previousForecastFallbackHours", 24)),
        )
        if prior:
            resolution = {
                "lat": prior[0],
                "lon": prior[1],
                "source": "previous GEFS ensemble median",
            }

    if resolver.get("requireResolvedSeed", False) and resolution["source"] == "tracking_config":
        raise RuntimeError(
            "Tracking seed unresolved: official JTWC identity was unavailable and "
            "no identity-verified previous GEFS run is eligible for fallback"
        )

    resolved["seed"] = {
        "lat": round(float(resolution["lat"]), 2),
        "lon": round(float(resolution["lon"]) % 360.0, 2),
    }
    resolved["_seedResolution"] = resolution
    print(
        "Tracking seed: "
        f"{resolved['seed']['lat']:.2f}, {resolved['seed']['lon']:.2f} "
        f"({resolution['source']})",
        flush=True,
    )
    return resolved


def request(
    url: str,
    method: str = "GET",
    retries: int = 5,
    headers: dict[str, str] | None = None,
    timeout: int = TIMEOUT,
) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        request_headers = {"User-Agent": USER_AGENT, **(headers or {})}
        req = urllib.request.Request(url, method=method, headers=request_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (400, 404):
                break
            if attempt + 1 < retries:
                time.sleep(min(20, 2 ** attempt))
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


def s3_prmsl_blob(init: datetime, member: str, fhour: int) -> bytes:
    """Fetch PRMSL directly from NOAA S3 using the GRIB index byte offset."""
    index_url = idx_url(init, member, fhour)
    index_text = request(index_url).decode("utf-8", errors="replace")
    lines = index_text.splitlines()
    for index, line in enumerate(lines):
        if ":PRMSL:mean sea level:" not in line:
            continue
        fields = line.split(":", 2)
        if len(fields) < 3:
            break
        start = int(fields[1])
        end = None
        if index + 1 < len(lines):
            next_fields = lines[index + 1].split(":", 2)
            if len(next_fields) >= 2:
                end = int(next_fields[1]) - 1
        byte_range = f"bytes={start}-{end if end is not None else ''}"
        data_url = index_url.removesuffix(".idx")
        blob = request(data_url, headers={"Range": byte_range})
        if not is_grib_message(blob):
            raise RuntimeError(
                f"Invalid S3 GRIB range for {member} f{fhour:03d}"
            )
        return blob
    raise RuntimeError(f"PRMSL offset missing from {index_url}")


def decode_prmsl(blob: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    handle = codes_new_from_message(blob)
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


def is_grib_message(blob: bytes) -> bool:
    """Reject HTML/error payloads that some upstream endpoints return as HTTP 200."""
    return len(blob) >= 100 and blob.startswith(b"GRIB") and blob.endswith(b"7777")


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
    if os.environ.get("GEFS_DOWNLOAD_SOURCE", "").lower() == "s3":
        return member, fhour, s3_prmsl_blob(init, member, fhour)
    try:
        blob = request(filter_url(init, member, fhour, box), retries=2, timeout=25)
        if not is_grib_message(blob):
            raise RuntimeError("NOMADS returned a non-GRIB payload")
    except RuntimeError as nomads_error:
        print(
            f"NOMADS fallback to NOAA S3: {member} f{fhour:03d}: {nomads_error}",
            file=sys.stderr,
            flush=True,
        )
        blob = s3_prmsl_blob(init, member, fhour)
    if not is_grib_message(blob):
        raise RuntimeError(f"Invalid GRIB response for {member} f{fhour:03d}")
    return member, fhour, blob


def build_tracks(init: datetime, config: dict) -> dict[str, list[TrackPoint]]:
    box = config["domain"]
    jobs = [(init, m, h, box) for m in MEMBERS for h in FORECAST_HOURS]
    blobs: dict[tuple[str, int], bytes] = {}
    workers = int(os.environ.get("GEFS_DOWNLOAD_WORKERS", "8"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download_one, job): job for job in jobs}
        for n, future in enumerate(concurrent.futures.as_completed(futures), 1):
            job = futures[future]
            try:
                member, fhour, blob = future.result()
            except Exception as exc:
                _, member, fhour, _ = job
                raise RuntimeError(
                    f"GEFS download failed for {member} f{fhour:03d}: {exc}"
                ) from exc
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


def verify_tracking_identity(config: dict, tracks: dict[str, list[TrackPoint]]) -> dict:
    """Fail closed when the tracked f000 vortex cannot plausibly be the seed.

    A smooth spaghetti plot is not evidence that the right cyclone was
    tracked.  This guard records the f000 relationship to the independently
    resolved official position and rejects weak-background-low solutions.
    """
    resolution = config.get("_seedResolution", {})
    source = str(resolution.get("source", "tracking_config"))
    if source == "tracking_config":
        raise RuntimeError("Tracking identity rejected: unresolved configured seed")
    seed = config["seed"]
    starts = [tracks[member][0] for member in MEMBERS]
    distances = [haversine_km(float(seed["lat"]), float(seed["lon"]), p.lat, p.lon) for p in starts]
    pressures = [p.mslp_hpa for p in starts]
    median_distance = float(np.median(distances))
    median_pressure = float(np.median(pressures))
    max_distance = float(config.get("initialIdentityMedianMaxDistanceKm", 750))
    max_pressure = float(config.get("initialIdentityMedianMslpMaxHpa", 990))
    identity = {
        "status": "verified",
        "source": source,
        "sourceStormId": resolution.get("sourceStormId"),
        "seed": {"lat": round(float(seed["lat"]), 2), "lon": round(float(seed["lon"]) % 360.0, 2)},
        "initialMedianDistanceKm": round(median_distance, 1),
        "initialMedianMslpHpa": round(median_pressure, 1),
        "maxMedianDistanceKm": max_distance,
        "maxMedianMslpHpa": max_pressure,
    }
    if median_distance > max_distance or median_pressure > max_pressure:
        identity["status"] = "rejected"
        raise RuntimeError(
            "Tracking identity rejected: "
            f"f000 median distance={median_distance:.0f}km (limit {max_distance:.0f}), "
            f"median MSLP={median_pressure:.1f}hPa (limit {max_pressure:.1f})"
        )
    return identity


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
        "officialIdentity": config.get("_officialIdentity", meta.get("officialIdentity", {})),
        "trackingSeed": config["seed"],
        "trackingSeedSource": config.get("_seedResolution", {}).get(
            "source", "tracking_config"
        ),
        "trackingIdentity": config.get("_trackingIdentity", {"status": "unverified"}),
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
    assert payload["meta"].get("trackingIdentity", {}).get("status") == "verified"
    assert "officialIdentity" in payload["meta"]


def archive_path_for_payload(payload: dict, init: datetime) -> Path:
    """Choose an archive path without overwriting another tracked cyclone."""
    init_key = init.strftime("%Y%m%d%H")
    base = HISTORY_DIR / f"{init_key}.json"
    current_id = normalize_storm_id(
        payload.get("meta", {}).get("stormInfo", {}).get("id")
        or payload.get("meta", {}).get("storm")
    )
    if not base.exists():
        return base
    try:
        saved = json.loads(base.read_text(encoding="utf-8"))
        saved_id = normalize_storm_id(
            saved.get("meta", {}).get("stormInfo", {}).get("id")
            or saved.get("meta", {}).get("storm")
        )
    except (OSError, json.JSONDecodeError):
        saved_id = ""
    if not saved_id or saved_id == current_id:
        return base
    suffix = current_id or "TARGET"
    return HISTORY_DIR / f"{init_key}-{suffix}.json"


def write_atomically(payload: dict, init: datetime) -> None:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    HISTORY_DIR.mkdir(exist_ok=True)
    archive = archive_path_for_payload(payload, init)
    archive.write_text(text, encoding="utf-8")
    write_history_index(HISTORY_DIR, latest_path=archive.name)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=ROOT, delete=False) as tmp:
        tmp.write(text)
        temp_path = Path(tmp.name)
    temp_path.replace(DATA_PATH)


def self_test() -> None:
    sample_abpw = """
    THE AREA OF CONVECTION (INVEST 92C) HAS PERSISTED NEAR
    12.7N 179.8E, APPROXIMATELY 597 NM NORTHEAST OF MAJURO.
    """
    assert parse_jtwc_abpw_seed(sample_abpw, {"92C"}) == (12.7, 179.8, "92C")
    sample_west = """
    THE AREA OF CONVECTION (INVEST 92C) HAS PERSISTED NEAR
    13.0N 179.5W, APPROXIMATELY 600 NM NORTHEAST OF MAJURO.
    """
    assert parse_jtwc_abpw_seed(sample_west, {"CP92", "92C"}) == (13.0, 180.5, "92C")
    sample_96w = """
    AN AREA OF CONVECTION (INVEST 96W) HAS PERSISTED NEAR 17.1N 166.1E,
    APPROXIMATELY 134 NM SOUTH-SOUTHWEST OF WAKE ISLAND.
    """
    assert parse_jtwc_abpw_seed(sample_96w, {"96W"}) == (17.1, 166.1, "96W")
    sample_94w_with_other_system = """
    B. TROPICAL DISTURBANCE SUMMARY:
       (1) AN AREA OF CONVECTION (INVEST 94W) HAS PERSISTED NEAR 20.2N
       134.7E, APPROXIMATELY 706 NM NORTHWEST OF GUAM. THE POTENTIAL FOR
       DEVELOPMENT WITHIN THE NEXT 24 HOURS IS MEDIUM.
    C. SUBTROPICAL SYSTEM SUMMARY:
       (1) THE AREA OF CONVECTION (SD 01C) PREVIOUSLY LOCATED NEAR 38.5N
       178.8E IS NOW LOCATED NEAR 38.4N 176.4E.
    """
    assert parse_jtwc_abpw_seed(sample_94w_with_other_system, {"94W"}) == (
        20.2,
        134.7,
        "94W",
    )
    assert parse_jtwc_abpw_seed(sample_94w_with_other_system, {"01C"}) is None
    sample_relocated = """
    THE AREA OF CONVECTION (INVEST 91W) PREVIOUSLY LOCATED NEAR
    22.0N 136.0E IS NOW LOCATED NEAR 22.2N 137.3E.
    """
    assert parse_jtwc_abpw_seed(sample_relocated, {"91W"}) == (22.2, 137.3, "91W")
    sample_warning = """
    1. TYPHOON 12W (DOLPHIN) WARNING NR 022
    WARNING POSITION:
    010600Z --- NEAR 20.8N 157.5E
    """
    assert parse_jtwc_named_seed(sample_warning, {"12W", "DOLPHIN"}) == (20.8, 157.5, "12W")
    sample_named_abpw = """
    AT 01AUG26 0000Z, TYPHOON 12W (DOLPHIN) WAS LOCATED NEAR
    20.2N 158.3E, APPROXIMATELY 1718 NM EAST OF KADENA AB.
    """
    assert parse_jtwc_named_seed(sample_named_abpw, {"12W", "DOLPHIN"}) == (20.2, 158.3, "12W")
    parsed_systems = parse_jtwc_named_systems(sample_named_abpw)
    assert parsed_systems == [{"id": "12W", "name": "DOLPHIN", "lat": 20.2, "lon": 158.3}]
    sample_jma = json.dumps([{
        "part": "title",
        "issue": {"JST": "2026-08-10T12:45:00+09:00"},
        "typhoonNumber": "2615",
        "name": {"jp": "チャンホン", "en": "Chan-hom"},
    }, {"part": {"jp": "実況"}, "advancedHours": 0, "center": [34.1, 149.3]}])
    assert parse_jma_forecast_identity("TC2617", sample_jma) == {
        "tropicalCyclone": "TC2617", "number": 15, "numberCode": "2615",
        "numberOfficial": True, "status": "typhoon", "category": "",
        "nameJa": "チャンホン", "nameEn": "CHAN-HOM", "lat": 34.1, "lon": 149.3,
        "issue": "2026-08-10T12:45:00+09:00",
        "sourceUrl": "https://www.jma.go.jp/bosai/typhoon/data/TC2617/forecast.json",
    }
    sample_developing = json.dumps([{
        "part": "title",
        "issue": {"JST": "2026-08-11T19:30:00+09:00"},
        "typhoonNumber": "a",
    }, {"part": {"jp": "実況"}, "advancedHours": 0, "center": [21.2, 138.4]}])
    assert parse_jma_forecast_identity(
        "TC2620", sample_developing, category="TD", candidate_number=17
    ) == {
        "tropicalCyclone": "TC2620", "number": 17, "numberCode": "a",
        "numberOfficial": False, "status": "candidate", "category": "TD",
        "nameJa": "", "nameEn": "", "lat": 21.2, "lon": 138.4,
        "issue": "2026-08-11T19:30:00+09:00",
        "sourceUrl": "https://www.jma.go.jp/bosai/typhoon/data/TC2620/forecast.json",
    }
    old_target = {
        "meta": {"storm": "WP96", "stormInfo": {"id": "96W", "aliases": ["14W"]}}
    }
    new_target_config = {"storm": "WP91", "stormInfo": {"id": "91W", "aliases": ["91W"]}}
    assert not same_tracking_target(old_target, new_target_config)
    assert official_identity_aliases(old_target, new_target_config) == {"WP91", "91W"}
    base = [TrackPoint(h, 12.5 + h / 24, 179.5 - h / 80, 1007 - h / 40) for h in FORECAST_HOURS]
    tracks = {}
    for i, member in enumerate(MEMBERS):
        tracks[member] = [TrackPoint(p.fhour, p.lat + (i % 5) * 0.15, p.lon + (i % 3) * 0.1, p.mslp_hpa) for p in base]
    tracks["p30"] = [TrackPoint(p.fhour, p.lat, p.lon if p.fhour < 120 else p.lon + 30, p.mslp_hpa) for p in base]
    previous = {"meta": {}, "disclaimer": {"ja": "test", "en": "test"}}
    config = {
        "seed": {"lat": 12.7, "lon": 179.8},
        "storm": "CP92",
        "stormInfo": {
            "id": "92C",
            "aliases": ["CP92", "92C"],
            "candidateNumber": 13,
        },
        "clusterThresholdKm": 650,
        "initialIdentityMedianMaxDistanceKm": 750,
        "initialIdentityMedianMslpMaxHpa": 1010,
        "_seedResolution": {"source": "JTWC warning", "sourceStormId": "12W"},
    }
    init = datetime(2026, 7, 16, 0, tzinfo=timezone.utc)
    config["_trackingIdentity"] = verify_tracking_identity(config, tracks)
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
        previous_id = normalize_storm_id(
            previous.get("meta", {}).get("stormInfo", {}).get("id")
        )
        configured_id = normalize_storm_id(config.get("stormInfo", {}).get("id"))
        if previous_id == configured_id:
            refresh_existing_identity(previous, config, init)
            print(f"data.json already contains complete run {init_string}")
            return 0

    print(f"Building GEFS analysis for {init_string}")
    config = resolve_tracking_seed(config, previous, init)
    tracks = build_tracks(init, config)
    config["_trackingIdentity"] = verify_tracking_identity(config, tracks)
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
