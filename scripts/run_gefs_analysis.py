#!/usr/bin/env python3
"""Run the GEFS pipeline with detailed noise diagnostics."""
from __future__ import annotations

from collections import Counter

import build_gefs_data as pipeline


_original_build_payload = pipeline.build_payload


def _diagnostic_build_payload(init, config, tracks, previous):
    reasons = {member: pipeline.noise_reasons(tracks[member]) for member in pipeline.MEMBERS}
    clean = [member for member in pipeline.MEMBERS if not reasons[member]]
    noise = [member for member in pipeline.MEMBERS if reasons[member]]
    counts = Counter(reason for member in noise for reason in reasons[member])

    print(
        "Noise diagnostics: "
        f"clean={len(clean)}, noise={len(noise)}, reasonCounts={dict(counts)}",
        flush=True,
    )

    for member in noise:
        points = tracks[member]
        segments = list(zip(points, points[1:]))
        speed_events = [
            (
                pipeline.haversine_km(a.lat, a.lon, b.lat, b.lon)
                / max(1, b.fhour - a.fhour),
                a.fhour,
                b.fhour,
            )
            for a, b in segments
        ]
        pressure_events = [
            (abs(a.mslp_hpa - b.mslp_hpa), a.fhour, b.fhour)
            for a, b in segments
        ]
        crossing_hours = [
            f"{a.fhour}-{b.fhour}"
            for a, b in segments
            if a.lat * b.lat < 0
        ]
        max_speed, speed_from, speed_to = max(speed_events, default=(0.0, 0, 0))
        max_jump, jump_from, jump_to = max(pressure_events, default=(0.0, 0, 0))
        print(
            f"  {member}: reasons={','.join(reasons[member])}; "
            f"maxSpeed={max_speed:.1f}km/h@f{speed_from:03d}-f{speed_to:03d}; "
            f"maxPressureJump={max_jump:.1f}hPa@f{jump_from:03d}-f{jump_to:03d}; "
            f"equatorCrossings={crossing_hours or '-'}; "
            f"start={points[0].lat:.1f},{points[0].lon:.1f}; "
            f"end={points[-1].lat:.1f},{points[-1].lon:.1f}",
            flush=True,
        )

    return _original_build_payload(init, config, tracks, previous)


pipeline.build_payload = _diagnostic_build_payload


if __name__ == "__main__":
    raise SystemExit(pipeline.main())
