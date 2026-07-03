"""Generate clearly-marked DEMO snapshots so the dashboard renders pre-CI.

Run once to populate ``site/data`` with a few synthetic runs + index. The real
scheduled workflow overwrites ``latest.json``/``index.json`` and appends real
dated snapshots. Demo snapshots carry ``meta.demo = true`` so the page shows a
"demo seed data" notice.

    python scripts/seed_demo.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import report as report_mod  # noqa: E402
from build_index import build_index  # noqa: E402

_CONFIG = {
    "dte": {"target": 35, "min": 30, "max": 45},
    "delta": {"target": 0.20, "min": 0.15, "max": 0.30},
    "scoring": {"mode": "blended"},
    "regime": {"breadth_floor": 0.40, "vix_high": 30.0, "spy_falling_lookback": 5},
    "account": {"total_capital": 50000, "max_pct_per_name": 0.40,
                "max_pct_per_sector": 0.25, "max_pct_deployed": 0.50},
    "quality": {"avoid_earnings_before_expiry": True},
}


class _Regime:
    def __init__(self, light, signals):
        self.light = light
        self.signals = signals

    @property
    def tripped(self):
        return [k for k, v in self.signals.items() if v]


def _row(ticker, sector, strike, mid, ann, dist, score, contracts, breach=False):
    return {
        "ticker": ticker, "sector": sector, "expiration": "2026-08-15", "dte": 35,
        "strike": strike, "mid": mid, "bid": mid - 0.01, "ask": mid + 0.01,
        "delta": -0.20, "abs_delta": 0.20, "iv": 0.25, "open_interest": 1800,
        "volume": 600, "collateral_per_contract": strike * 100,
        "roc": ann * 35 / 365, "annualized_yield": ann, "yield_30dte": ann * 30 / 365,
        "max_contracts": contracts, "breaches_per_name_cap": breach,
        "min_account_for_1_contract": strike * 100 / 0.05 if breach else 0.0,
        "distance_to_strike": dist, "implied_move": 0.06,
        "score_denominator": 0.06, "score": score, "score_mode": "blended",
    }


# (days_ago, light, signals, deployed, rows)
_RUNS = [
    (4, "GREEN", {"spy_below_200dma": False, "breadth_below_floor": False, "vix_high_and_spy_falling": False}, 0, [
        _row("KO", "Consumer Defensive", 58, 0.55, 0.21, 0.08, 2.80, 4),
        _row("MSFT", "Technology", 390, 4.10, 0.19, 0.07, 2.41, 1),
        _row("JNJ", "Healthcare", 150, 1.45, 0.16, 0.09, 2.13, 3),
        _row("PG", "Consumer Defensive", 158, 1.30, 0.14, 0.10, 1.92, 3),
        _row("NVDA", "Technology", 110, 2.20, 0.28, 0.05, 1.70, 4, breach=True),
    ]),
    (3, "GREEN", {"spy_below_200dma": False, "breadth_below_floor": False, "vix_high_and_spy_falling": False}, 0.05, [
        _row("KO", "Consumer Defensive", 58, 0.52, 0.20, 0.08, 2.65, 4),
        _row("PEP", "Consumer Defensive", 168, 1.55, 0.18, 0.07, 2.30, 2),
        _row("V", "Financial Services", 270, 2.80, 0.17, 0.08, 2.05, 1),
        _row("HD", "Consumer Cyclical", 340, 3.40, 0.15, 0.09, 1.78, 1),
    ]),
    (2, "YELLOW", {"spy_below_200dma": False, "breadth_below_floor": True, "vix_high_and_spy_falling": False}, 0.12, [
        _row("KO", "Consumer Defensive", 57, 0.60, 0.23, 0.07, 2.90, 4),
        _row("JNJ", "Healthcare", 148, 1.60, 0.18, 0.09, 2.20, 3),
        _row("XOM", "Energy", 105, 1.10, 0.16, 0.10, 1.85, 2),
    ]),
    (1, "RED", {"spy_below_200dma": True, "breadth_below_floor": True, "vix_high_and_spy_falling": False}, 0.18, []),
    (0, "GREEN", {"spy_below_200dma": False, "breadth_below_floor": False, "vix_high_and_spy_falling": False}, 0.10, [
        _row("MSFT", "Technology", 385, 4.30, 0.21, 0.07, 2.55, 1),
        _row("COST", "Consumer Defensive", 820, 7.50, 0.18, 0.08, 2.20, 1, breach=True),
        _row("MA", "Financial Services", 440, 4.10, 0.16, 0.09, 1.95, 1),
        _row("ABBV", "Healthcare", 165, 1.70, 0.15, 0.10, 1.80, 3),
        _row("WMT", "Consumer Defensive", 70, 0.65, 0.17, 0.11, 1.74, 4),
    ]),
]


# Demo near-misses for the newest run so the near-miss table/chart render locally.
_NEAR_MISSES = [
    {**_row("META", "Communication Services", 640, 6.80, 0.19, 0.06, 1.60, 1),
     "rejection_reasons": [{"code": "implied_move", "message": "implied move 0.1580 > 0.15"}],
     "data_flags": []},
    {**_row("UNH", "Healthcare", 480, 4.90, 0.17, 0.08, 1.40, 1),
     "rejection_reasons": [],
     "data_flags": [{"code": "iv_missing", "message": "no IV from feed — implied-move gate not evaluated"}]},
]
_REJECTION_COUNTS = {"implied_move": 1, "iv_missing": 1, "universe": 2, "no_put_in_window": 1}


def main() -> int:
    data_dir = ROOT / "site" / "data"
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc)

    for days_ago, light, signals, pct, rows in _RUNS:
        ts = (today - timedelta(days=days_ago)).replace(hour=21, minute=15, second=0, microsecond=0)
        total = _CONFIG["account"]["total_capital"]
        deployed = total * pct
        header = {
            "regime_light": light, "regime_tripped": [k for k, v in signals.items() if v],
            "total_capital": total, "deployed": deployed, "pct_deployed": pct,
            "remaining_cash": total - deployed,
            "positions_source": "greenfield (no positions.yaml)",
        }
        newest = days_ago == 0
        report_mod.write_json(
            header, rows, _Regime(light, signals), _CONFIG,
            runs_dir / f"{ts.date().isoformat()}.json",
            near_misses=_NEAR_MISSES if newest else [],
            meta_extra={"demo": True, "data_source": "yfinance",
                        "tickers_screened": [r["ticker"] for r in rows],
                        "breadth_evaluated": False, "max_rows": 25,
                        "rejections_by_reason": _REJECTION_COUNTS if newest else {}},
            generated_at=ts,
        )

    index = build_index(data_dir)
    print(f"Seeded {len(index['runs'])} demo runs -> {data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
