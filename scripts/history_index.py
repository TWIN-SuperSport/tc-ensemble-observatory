#!/usr/bin/env python3
"""Build a compact manifest for archived GEFS analysis runs."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def target_label(meta: dict) -> str:
    info = meta.get("stormInfo", {})
    storm_id = str(info.get("id") or meta.get("storm") or "対象不明")
    number = info.get("number") or info.get("candidateNumber")
    if info.get("status") == "typhoon" and number:
        name = info.get("nameJa") or info.get("name")
        return f"台風{number}号" + (f"（{name}）" if name else "")
    if number:
        return f"{storm_id}（台風{number}号候補）"
    return storm_id


def build_history_index(history_dir: Path, latest_path: str | None = None) -> dict:
    index_path = history_dir / "index.json"
    runs = []
    for archive in sorted(history_dir.glob("*.json"), reverse=True):
        if archive == index_path:
            continue
        try:
            payload = json.loads(archive.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        init = str(payload.get("meta", {}).get("init") or archive.stem)
        meta = payload.get("meta", {})
        summary = payload.get("summary", {})
        runs.append(
            {
                "init": init,
                "path": archive.name,
                "bytes": archive.stat().st_size,
                "model": meta.get("model", "GEFS"),
                "storm": meta.get("storm"),
                "stormId": meta.get("stormInfo", {}).get("id"),
                "targetLabel": target_label(meta),
                "members": summary.get("members"),
                "cleanMembers": summary.get("cleanMembers"),
                "noiseMembers": summary.get("noiseMembers"),
                "clusterCount": summary.get("clusterCount"),
            }
        )
    runs.sort(key=lambda run: (run["init"], run["path"]), reverse=True)
    latest_run = next((run for run in runs if run["path"] == latest_path), None)
    if latest_run is None and runs:
        latest_run = runs[0]
    return {
        "schemaVersion": 1,
        "latest": latest_run["init"] if latest_run else None,
        "latestPath": latest_run["path"] if latest_run else None,
        "runCount": len(runs),
        "runs": runs,
    }


def write_history_index(history_dir: Path, latest_path: str | None = None) -> Path:
    history_dir.mkdir(exist_ok=True)
    index_path = history_dir / "index.json"
    payload = build_history_index(history_dir, latest_path=latest_path)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=history_dir, delete=False
    ) as tmp:
        tmp.write(text)
        temp_path = Path(tmp.name)
    temp_path.replace(index_path)
    return index_path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    index_path = write_history_index(root / "history")
    print(f"Wrote {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
