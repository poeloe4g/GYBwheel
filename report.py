"""Report output (F11 / Spec 2.7).

Header (regime light, total capital, % deployed, remaining cash) plus a ranked
table to console and CSV. Notion/Telegram push left as optional stubs.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

CSV_COLUMNS = [
    "ticker", "sector", "expiration", "dte", "strike", "mid", "abs_delta",
    "roc", "annualized_yield", "yield_30dte", "distance_to_strike", "implied_move",
    "score", "max_contracts", "collateral_per_contract",
    "breaches_per_name_cap", "min_account_for_1_contract",
]


def build_header(regime: Any, account: Any, config: dict[str, Any]) -> dict[str, Any]:
    total_capital = float(config["account"]["total_capital"])
    deployed = account.total_deployed
    return {
        "regime_light": regime.light,
        "regime_tripped": regime.tripped,
        "total_capital": total_capital,
        "deployed": deployed,
        "pct_deployed": (deployed / total_capital) if total_capital else 0.0,
        "remaining_cash": total_capital - deployed,
        "positions_source": account.source,
    }


def render_console(header: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"BORING Wheel Screener — Regime: {header['regime_light']}")
    if header["regime_tripped"]:
        lines.append(f"  signals tripped: {', '.join(header['regime_tripped'])}")
    lines.append(
        f"Capital ${header['total_capital']:,.0f} | deployed ${header['deployed']:,.0f} "
        f"({header['pct_deployed']*100:.1f}%) | remaining ${header['remaining_cash']:,.0f}"
    )
    lines.append(f"Positions: {header['positions_source']}")
    lines.append("=" * 78)

    if not rows:
        lines.append("No qualifying candidates.")
        return "\n".join(lines)

    hdr = (f"{'TICKER':<7}{'EXP':<12}{'DTE':>4}{'STRIKE':>9}{'MID':>7}{'|Δ|':>6}"
           f"{'ANN%':>7}{'DIST%':>7}{'SCORE':>8}{'MAXC':>5}  FLAGS")
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in rows:
        flags = "BREACH" if r.get("breaches_per_name_cap") else ""
        if flags:
            flags += f" min_acct=${r.get('min_account_for_1_contract',0):,.0f}"
        lines.append(
            f"{r.get('ticker',''):<7}{r.get('expiration',''):<12}{r.get('dte',0):>4}"
            f"{r.get('strike',0):>9.2f}{r.get('mid',0):>7.2f}{r.get('abs_delta',0):>6.2f}"
            f"{r.get('annualized_yield',0)*100:>7.1f}{r.get('distance_to_strike',0)*100:>7.1f}"
            f"{r.get('score',0):>8.3f}{r.get('max_contracts',0):>5}  {flags}"
        )
    return "\n".join(lines)


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return path


# --- optional push stubs ---------------------------------------------------
def push_notion(header: dict[str, Any], rows: list[dict[str, Any]]) -> None:  # pragma: no cover
    """Stub — wire up later if desired."""
    raise NotImplementedError("Notion push is an optional v2 stub.")


def push_telegram(header: dict[str, Any], rows: list[dict[str, Any]]) -> None:  # pragma: no cover
    """Stub — wire up later if desired."""
    raise NotImplementedError("Telegram push is an optional v2 stub.")
