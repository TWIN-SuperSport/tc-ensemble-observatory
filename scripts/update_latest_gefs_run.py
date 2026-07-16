#!/usr/bin/env python3
"""Detect the latest available GEFS cycle in NOAA's public S3 archive.

This script intentionally updates only latest_run.json. It does not change the
analysis init in data.json, because doing so without regenerating the tracks
would mislabel old track data as a new model run.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

OUTPUT = Path("latest_run.json")
BASE = "https://noaa-gefs-pds.s3.amazonaws.com"
CYCLES = (18, 12, 6, 0)
LOOKBACK_HOURS = 72
TIMEOUT_SECONDS = 12


def candidate_cycles(now: datetime):
    """Yield GEFS cycles newest first, aligned to 00/06/12/18 UTC."""
    cursor = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(LOOKBACK_HOURS + 1):
        if cursor.hour in CYCLES:
            yield cursor
        cursor -= timedelta(hours=1)


def probe_url(dt: datetime) -> str:
    date = dt.strftime("%Y%m%d")
    cycle = dt.strftime("%H")
    return (
        f"{BASE}/gefs.{date}/{cycle}/atmos/pgrb2ap5/"
        f"gec00.t{cycle}z.pgrb2a.0p50.f000.idx"
    )


def exists(url: str) -> bool:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "tc-ensemble-observatory/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 400
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return False
        raise
    except urllib.error.URLError:
        return False


def main() -> int:
    now = datetime.now(timezone.utc)
    latest = None
    latest_url = None

    for candidate in candidate_cycles(now):
        url = probe_url(candidate)
        if exists(url):
            latest = candidate
            latest_url = url
            break

    if latest is None:
        print("No available GEFS cycle found in the lookback window.", file=sys.stderr)
        return 1

    payload = {
        "model": "GEFS",
        "init": latest.strftime("%Y%m%d%H"),
        "checkedAt": now.isoformat().replace("+00:00", "Z"),
        "source": "NOAA Open Data / noaa-gefs-pds",
        "status": "available",
        "probeUrl": latest_url,
    }

    previous = None
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = None

    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    changed = not previous or previous.get("init") != payload["init"]
    print(f"Latest GEFS init: {payload['init']} (changed={changed})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
