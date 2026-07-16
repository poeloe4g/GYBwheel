"""Grade the user's dashboard selections once their contracts expire.

Walks ``site/data/selections.json`` (written by the dashboard's "Select"
button) and, for every OPEN entry whose expiration has passed, settles it
with the same math the screener uses to grade itself
(``evaluate_outcomes.evaluate_contract``):

  EXPIRED_WIN    expiry close > strike — premium kept in full
  ASSIGNED       expiry close <= strike — shares assigned; the loss is marked
                 at the expiry close, like outcomes.json does

EARLY_CLOSED entries (the dashboard writes those itself, with the user's
buyback price) pass through untouched. Terminal entries are never
re-examined, so the pass is idempotent; per-ticker fetch failures skip and
retry next run.

The document's ``summary`` block (open/closed aggregates + cumulative-P&L
equity curve) is rebuilt on every pass — the dashboard renders it verbatim.

Usage:
    python scripts/grade_selections.py [--selections site/data/selections.json]
        [--config config.yaml] [--today YYYY-MM-DD]
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
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_outcomes import evaluate_contract  # noqa: E402

TERMINAL = ("EXPIRED_WIN", "ASSIGNED", "EARLY_CLOSED")


def grade_selection(
    sel: dict[str, Any], history: list[dict[str, Any]], today: date,
) -> dict[str, Any] | None:
    """Settle one OPEN selection at expiry; None if not evaluable yet."""
    # Annualize over the true holding period: the pick may have been selected
    # days after the run it came from.
    selected_date = (sel.get("selected_at") or sel.get("run_date") or "")[:10]
    contract = {
        "run_date": selected_date,
        "ticker": sel["ticker"],
        "expiration": sel["expiration"],
        "strike": float(sel["strike"]),
        "premium": float(sel["entry_premium"]),
    }
    result = evaluate_contract(contract, history, today)
    if not result:
        return None
    # Exact per-contract P&L (realized_roc is rounded — don't scale it back up):
    # premium kept minus assignment loss marked at the expiry close.
    contracts = int(sel.get("contracts", 1))
    loss = max(contract["strike"] - result["expiry_close"], 0.0)
    pnl_usd = (contract["premium"] - loss) * 100.0 * contracts
    return {
        **sel,
        "status": "EXPIRED_WIN" if result["win"] else "ASSIGNED",
        "close": {
            "method": "expiry",
            "closed_at": sel["expiration"],
            "expiry_close": result["expiry_close"],
            "pnl_usd": round(pnl_usd, 2),
            "realized_roc": result["realized_roc"],
            "annualized_realized": result["annualized_realized"],
            "win": result["win"],
        },
    }


def summarize_selections(selections: list[dict[str, Any]]) -> dict[str, Any]:
    """Open/closed aggregates + equity curve, consumed verbatim by the UI."""
    open_ = [s for s in selections if s.get("status") == "OPEN"]
    closed = [s for s in selections if s.get("status") in TERMINAL and s.get("close")]
    wins = sum(1 for s in closed if s["close"].get("win"))
    total_pnl = sum(float(s["close"].get("pnl_usd") or 0.0) for s in closed)
    def premium_usd(s: dict[str, Any]) -> float:
        return float(s.get("entry_premium") or 0.0) * 100.0 * int(s.get("contracts", 1))

    curve, cum = [], 0.0
    for s in sorted(closed, key=lambda s: s["close"].get("closed_at") or ""):
        cum += float(s["close"].get("pnl_usd") or 0.0)
        curve.append({"date": s["close"].get("closed_at"), "cum_pnl_usd": round(cum, 2)})

    n = len(closed)
    return {
        "open": {
            "n": len(open_),
            "collateral": round(sum(float(s.get("collateral") or 0.0) for s in open_), 2),
            "premium_at_risk_usd": round(sum(premium_usd(s) for s in open_), 2),
        },
        "closed": {
            "n": n,
            "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
            "total_pnl_usd": round(total_pnl, 2),
            "total_premium_collected_usd": round(sum(premium_usd(s) for s in closed), 2),
            "avg_realized_roc": round(
                sum(float(s["close"].get("realized_roc") or 0.0) for s in closed) / n, 6)
                if n else None,
        },
        "equity_curve": curve,
    }


def grade_file(
    selections_path: str | Path, provider: Any, today: date | None = None,
) -> dict[str, Any] | None:
    """Idempotent pass over the selections doc; None if there is no file."""
    today = today or date.today()
    path = Path(selections_path)
    if not path.exists():
        print(f"{path}: no selections file — nothing to grade")
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"warning: {path} unreadable ({exc}) — nothing graded", file=sys.stderr)
        return None
    selections = doc.get("selections") or []

    histories: dict[str, list | None] = {}
    graded = 0
    for i, sel in enumerate(selections):
        if not isinstance(sel, dict) or sel.get("status") != "OPEN":
            continue
        exp, ticker = sel.get("expiration"), sel.get("ticker")
        if not (exp and ticker and sel.get("entry_premium") and sel.get("strike")):
            print(f"warning: skipping malformed selection {sel.get('uid')!r}",
                  file=sys.stderr)
            continue
        if date.fromisoformat(exp) >= today:
            continue
        if ticker not in histories:
            try:
                histories[ticker] = provider.get_price_history(ticker, period="1y")
            except Exception as exc:  # noqa: BLE001 — skip, retried next run
                print(f"warning: {ticker}: history fetch failed ({exc}); skipping",
                      file=sys.stderr)
                histories[ticker] = None
        if histories[ticker] is None:
            continue
        result = grade_selection(sel, histories[ticker], today)
        if result:
            selections[i] = result
            graded += 1

    doc["selections"] = selections
    doc["summary"] = summarize_selections(selections)
    doc["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
    os.replace(tmp, path)
    still_open = sum(1 for s in selections
                     if isinstance(s, dict) and s.get("status") == "OPEN")
    print(f"{path.name}: {graded} newly graded, {still_open} still open, "
          f"{doc['summary']['closed']['n']} closed total")
    return doc


def main(argv: list[str] | None = None, provider: Any = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--selections", default="site/data/selections.json",
                   help="Selections JSON path")
    p.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    p.add_argument("--today", help="Override today's date (YYYY-MM-DD, for tests)")
    args = p.parse_args(argv)

    if provider is None:
        from config import load_config, load_secrets
        from data import DataProvider

        provider = DataProvider(load_config(args.config), load_secrets())

    today = date.fromisoformat(args.today) if args.today else None
    grade_file(args.selections, provider, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
