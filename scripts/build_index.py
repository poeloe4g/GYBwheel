"""Regenerate the dashboard's index.json + latest.json from run snapshots.

Scans ``site/data/runs/*.json`` (each a snapshot written by
``report.write_json``), extracts a lightweight per-run summary so the
time-series charts can render from ``index.json`` alone, copies the newest
snapshot to ``latest.json``, and writes both atomically.

Usage:
    python scripts/build_index.py [--data-dir site/data]
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _summarize(snapshot: dict[str, Any]) -> dict[str, Any]:
    """One row of index.json's ``runs`` list."""
    meta = snapshot.get("meta", {})
    header = snapshot.get("header", {})
    rows = snapshot.get("rows", []) or []
    scores = [r.get("score") for r in rows if isinstance(r.get("score"), (int, float))]
    return {
        "date": meta.get("run_date"),
        "light": (snapshot.get("regime") or {}).get("light"),
        "row_count": len(rows),
        "near_miss_count": len(snapshot.get("near_misses") or []),  # 0 for v1 snapshots
        "top_score": max(scores) if scores else None,
        "pct_deployed": header.get("pct_deployed"),
        "demo": bool(meta.get("demo")),
    }


def build_index(data_dir: str | Path) -> dict[str, Any]:
    data_dir = Path(data_dir)
    runs_dir = data_dir / "runs"
    snapshots: list[tuple[str, dict[str, Any]]] = []
    for fp in sorted(runs_dir.glob("*.json")):
        try:
            with fp.open("r", encoding="utf-8") as fh:
                snapshots.append((fp.stem, json.load(fh)))
        except (json.JSONDecodeError, OSError):
            continue  # skip a corrupt/partial file rather than fail the build

    # Sort by run_date (fall back to filename stem) ascending for time-series.
    def _key(item: tuple[str, dict[str, Any]]) -> str:
        stem, snap = item
        return snap.get("meta", {}).get("run_date") or stem

    snapshots.sort(key=_key)

    runs = [_summarize(snap) for _, snap in snapshots]
    latest_stem = snapshots[-1][0] if snapshots else None
    latest_snapshot = snapshots[-1][1] if snapshots else None

    index = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest": runs[-1]["date"] if runs else None,
        "runs": runs,
    }

    _write_json_atomic(data_dir / "index.json", index)
    if latest_snapshot is not None:
        _write_json_atomic(data_dir / "latest.json", latest_snapshot)
    return index


def _write_json_atomic(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="site/data", help="Dashboard data directory")
    args = p.parse_args(argv)
    index = build_index(args.data_dir)
    print(f"index.json: {len(index['runs'])} runs, latest={index['latest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
