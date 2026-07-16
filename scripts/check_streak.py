"""Detect unhealthy run streaks in the dashboard index (CI alert helper).

Two independent checks, selected with ``--mode`` (stdlib-only, mirrors
``build_index.py``); each exits 1 at its threshold so a workflow step can gate
an alert on it:

``zero`` (default) — walks ``index.json``'s runs newest-first, skipping
RED-regime days (zero candidates is correct behavior on RED), demo seeds, and
off-hours runs, and counts consecutive runs with ``row_count == 0`` until the
first run that produced candidates.

``untrusted`` — counts consecutive most-recent non-demo runs whose
``quotes_trusted`` is explicitly ``false``. Every scheduled run executing
off-hours means the cron is landing after the market close (exactly what
happened when the 19:45 UTC slot drifted past 20:00): quotes are stale, gate
results are unreliable, and the zero-candidate alert is disarmed — so this
condition needs its own alarm.

Usage:
    python scripts/check_streak.py [--index site/data/index.json]
        [--threshold 3] [--mode zero|untrusted]
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


def untrusted_streak(index: dict) -> int:
    """Consecutive most-recent non-demo runs with ``quotes_trusted`` == False.

    Pre-v3 runs never stamped a session (``quotes_trusted`` is None/absent) —
    they are skipped rather than counted either way; an explicitly trusted run
    breaks the streak.
    """
    streak = 0
    for run in reversed(index.get("runs") or []):
        if run.get("demo") or run.get("quotes_trusted") is None:
            continue
        if run.get("quotes_trusted"):
            break
        streak += 1
    return streak


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--index", default="site/data/index.json", help="Path to index.json")
    p.add_argument("--threshold", type=int, default=3,
                   help="Exit 1 when the streak reaches this many runs")
    p.add_argument("--mode", choices=("zero", "untrusted"), default="zero",
                   help="zero: zero-candidate streak; untrusted: off-hours-data streak")
    args = p.parse_args(argv)

    try:
        index = json.loads(Path(args.index).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        # No/corrupt index is a build problem, not a streak — don't false-alarm.
        print(f"check_streak: could not read {args.index}: {exc}", file=sys.stderr)
        return 0

    if args.mode == "untrusted":
        streak = untrusted_streak(index)
        print(f"untrusted-quotes streak: {streak} (threshold {args.threshold})")
    else:
        streak = zero_candidate_streak(index)
        print(f"zero-candidate streak: {streak} (threshold {args.threshold})")
    return 1 if streak >= args.threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
