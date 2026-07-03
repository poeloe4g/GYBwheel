"""Detect a zero-candidate streak in the dashboard index (CI alert helper).

Walks ``index.json``'s runs newest-first, skipping RED-regime days (zero
candidates is correct behavior on RED) and demo seeds, and counts consecutive
runs with ``row_count == 0`` until the first run that produced candidates.
Exits 1 when the streak reaches the threshold so a workflow step can gate an
alert on it — stdlib-only, mirrors ``build_index.py``.

Usage:
    python scripts/check_streak.py [--index site/data/index.json] [--threshold 3]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def zero_candidate_streak(index: dict) -> int:
    """Consecutive most-recent non-RED, non-demo runs with zero candidates.

    Off-hours runs (``quotes_trusted`` explicitly False) are skipped too:
    zero candidates on stale/zeroed quotes is expected, not alert-worthy.
    """
    streak = 0
    for run in reversed(index.get("runs") or []):
        if run.get("light") == "RED" or run.get("demo") or run.get("quotes_trusted") is False:
            continue
        if (run.get("row_count") or 0) > 0:
            break
        streak += 1
    return streak


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", default="site/data/index.json", help="Path to index.json")
    p.add_argument("--threshold", type=int, default=3,
                   help="Exit 1 when the streak reaches this many runs")
    args = p.parse_args(argv)

    try:
        index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # No/corrupt index is a build problem, not a streak — don't false-alarm.
        print(f"check_streak: could not read {args.index}: {exc}", file=sys.stderr)
        return 0

    streak = zero_candidate_streak(index)
    print(f"zero-candidate streak: {streak} (threshold {args.threshold})")
    return 1 if streak >= args.threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
