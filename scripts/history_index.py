#!/usr/bin/env python3
"""Build a compact manifest for archived GEFS analysis runs."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path


def build_history_index(history_dir: Path) -> dict:
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
        summary = payload.get("summary", {})
        runs.append(
            {
                "init": init,
                "path": archive.name,
                "bytes": archive.stat().st_size,
                "model": payload.get("meta", {}).get("model", "GEFS"),
                "storm": payload.get("meta", {}).get("storm"),
                "members": summary.get("members"),
                "cleanMembers": summary.get("cleanMembers"),
                "noiseMembers": summary.get("noiseMembers"),
                "clusterCount": summary.get("clusterCount"),
            }
        )
    runs.sort(key=lambda run: run["init"], reverse=True)
    return {
        "schemaVersion": 1,
        "latest": runs[0]["init"] if runs else None,
        "runCount": len(runs),
        "runs": runs,
    }


def write_history_index(history_dir: Path) -> Path:
    history_dir.mkdir(exist_ok=True)
    index_path = history_dir / "index.json"
    payload = build_history_index(history_dir)
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
