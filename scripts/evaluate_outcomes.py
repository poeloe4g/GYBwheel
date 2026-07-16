"""Evaluate expired contracts from past run snapshots (the success-rate feed).

Walks ``site/data/runs/*.json`` and, for every candidate AND near-miss row
whose expiration has passed (and whose price history already contains a bar on
or after expiry), records the outcome:

  win            expiry close > strike (the put expired worthless)
  touched        the strike was breached intraday-close at least once
  realized_roc   premium kept, minus assignment loss approximated at the
                 expiry close: (premium - max(strike - expiry_close, 0)) * 100
                 / collateral

Near misses are evaluated on purpose: the ``summary.by_rejection_code`` block
is the gate-calibration signal — a rejection code whose win-rate matches the
candidates' marks a gate that is too tight.

Results accumulate in ``site/data/outcomes.json`` (separate file: run
snapshots stay immutable write-once artifacts). Idempotent — already-evaluated
keys are never refetched; per-ticker fetch failures skip and retry next run.

Usage:
    python scripts/evaluate_outcomes.py [--runs site/data/runs]
        [--out site/data/outcomes.json] [--config config.yaml]
        [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = 1


def collect_contracts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract evaluable contract records from one run snapshot (v1/v2/v3)."""
    meta = snapshot.get("meta", {})
    if meta.get("demo"):
        return []
    run_date = meta.get("run_date")
    if not run_date:
        return []
    out: list[dict[str, Any]] = []
    groups = (("candidate", snapshot.get("rows")),
              ("near_miss", snapshot.get("near_misses")))
    for group, rows in groups:
        for r in rows or []:
            ticker, exp = r.get("ticker"), r.get("expiration")
            strike, mid = r.get("strike"), r.get("mid")
            if not (ticker and exp and strike and mid):
                continue
            spot = r.get("spot")
            if spot is None:
                # Pre-v3 rows carry no spot; derive it from the distance the
                # run itself recorded: dist = (spot - strike) / spot.
                dist = r.get("distance_to_strike")
                if dist is not None and dist < 1:
                    spot = round(float(strike) / (1.0 - float(dist)), 4)
            out.append({
                "key": f"{run_date}|{ticker}|{exp}|{strike}",
                "run_date": run_date,
                "ticker": ticker,
                "expiration": exp,
                "strike": float(strike),
                # v4 rows carry the conservative-fill premium the run actually
                # ranked on; realized ROC should match what was screened.
                "premium": float(r.get("premium_used") or mid),
                "spot": spot,
                "group": group,
                "rejection_codes": [e.get("code") for e in r.get("rejection_reasons") or []],
                "flag_codes": [e.get("code") for e in r.get("data_flags") or []],
            })
    return out


def evaluate_contract(
    contract: dict[str, Any], history: list[dict[str, Any]], today: date,
) -> dict[str, Any] | None:
    """Score one contract against daily closes; None if not evaluable yet.

    Requires a bar on/after expiration so a half-baked evaluation can't happen
    on the expiry day itself (weekends/holidays: the last close <= expiration
    is used as the settlement proxy).
    """
    exp = contract["expiration"]
    if date.fromisoformat(exp) >= today:
        return None
    bars = sorted((h["date"], h["close"]) for h in history or [] if h.get("close") is not None)
    if not bars or bars[-1][0] < exp:
        return None
    upto = [c for d, c in bars if d <= exp]
    if not upto:
        return None  # history window no longer reaches back to expiry
    expiry_close = upto[-1]
    window = [c for d, c in bars if contract["run_date"] < d <= exp]
    min_close = min(window) if window else expiry_close

    strike, premium = contract["strike"], contract["premium"]
    win = expiry_close > strike
    collateral = strike * 100.0
    pnl = premium * 100.0 - max(strike - expiry_close, 0.0) * 100.0
    realized_roc = pnl / collateral if collateral else 0.0
    held_days = max(
        (date.fromisoformat(exp) - date.fromisoformat(contract["run_date"])).days, 1)
    return {
        **{k: v for k, v in contract.items() if k != "key"},
        "expiry_close": round(expiry_close, 4),
        "min_close": round(min_close, 4),
        "win": win,
        "touched": min_close < strike,
        "realized_roc": round(realized_roc, 6),
        "annualized_realized": round(realized_roc * 365.0 / held_days, 6),
        "evaluated_at": today.isoformat(),
    }


def summarize(outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Win-rate aggregates; by_rejection_code is the gate-calibration signal."""
    def agg(items: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(items)
        wins = sum(1 for o in items if o.get("win"))
        return {
            "n": n,
            "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
            "avg_realized_roc": round(sum(o.get("realized_roc", 0.0) for o in items) / n, 6)
                                if n else None,
        }

    items = list(outcomes.values())
    near = [o for o in items if o.get("group") == "near_miss"]
    by_rejection: dict[str, list] = {}
    for o in near:  # a near miss contributes to every code it carried
        for code in o.get("rejection_codes") or []:
            by_rejection.setdefault(code, []).append(o)
    by_flag: dict[str, list] = {}
    for o in items:
        for code in o.get("flag_codes") or []:
            by_flag.setdefault(code, []).append(o)
    return {
        "candidates": agg([o for o in items if o.get("group") == "candidate"]),
        "near_misses": agg(near),
        "by_rejection_code": {k: agg(v) for k, v in sorted(by_rejection.items())},
        "by_flag_code": {k: agg(v) for k, v in sorted(by_flag.items())},
    }


def evaluate_runs(
    runs_dir: str | Path, out_path: str | Path, provider: Any, today: date | None = None,
) -> dict[str, Any]:
    """Idempotent pass: evaluate not-yet-recorded expired contracts, rewrite doc."""
    today = today or date.today()
    out_path = Path(out_path)

    outcomes: dict[str, dict[str, Any]] = {}
    if out_path.exists():
        try:
            outcomes = dict(json.loads(out_path.read_text(encoding="utf-8"))
                            .get("outcomes") or {})
        except (json.JSONDecodeError, OSError):
            print("warning: existing outcomes file unreadable — rebuilding", file=sys.stderr)

    contracts: list[dict[str, Any]] = []
    for fp in sorted(Path(runs_dir).glob("*.json")):
        try:
            snapshot = json.loads(fp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # skip a corrupt snapshot, like build_index does
        contracts.extend(collect_contracts(snapshot))

    pending = [c for c in contracts
               if c["key"] not in outcomes and date.fromisoformat(c["expiration"]) < today]
    histories: dict[str, list | None] = {}
    evaluated = 0
    for c in pending:
        ticker = c["ticker"]
        if ticker not in histories:
            try:
                histories[ticker] = provider.get_price_history(ticker, period="1y")
            except Exception as exc:  # noqa: BLE001 — skip, retried next run
                print(f"warning: {ticker}: history fetch failed ({exc}); skipping",
                      file=sys.stderr)
                histories[ticker] = None
        if histories[ticker] is None:
            continue
        result = evaluate_contract(c, histories[ticker], today)
        if result:
            outcomes[c["key"]] = result
            evaluated += 1

    doc = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "outcomes": outcomes,
        "summary": summarize(outcomes),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, out_path)
    print(f"outcomes.json: {len(outcomes)} resolved contracts "
          f"({evaluated} new, {len(pending) - evaluated} pending/skipped)")
    return doc


def main(argv: list[str] | None = None, provider: Any = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", default="site/data/runs", help="Run snapshots directory")
    p.add_argument("--out", default="site/data/outcomes.json", help="Outcomes JSON path")
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--today", help="Override today's date (YYYY-MM-DD, for tests)")
    args = p.parse_args(argv)

    if provider is None:
        from config import load_config, load_secrets
        from data import DataProvider

        provider = DataProvider(load_config(args.config), load_secrets())

    today = date.fromisoformat(args.today) if args.today else None
    evaluate_runs(args.runs, args.out, provider, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
