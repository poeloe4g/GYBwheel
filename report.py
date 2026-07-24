"""Report output (F11 / Spec 2.7).

Header (regime light, total capital, % deployed, remaining cash) plus a ranked
table to console and CSV. Notion/Telegram push left as optional stubs.
"""
from __future__ import annotations

import csv
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Versions are additive; readers must treat newer fields as optional and never
# gate on the version number.
#   v2: top-level ``near_misses``, ``meta.near_miss_count``,
#       ``meta.rejections_by_reason``.
#   v3: candidate rows may carry ``data_flags`` (policy-promoted flags) and
#       ``spot``; ``meta.flags_by_reason``; ``thresholds.unknown_earnings_policy``;
#       ``meta.contracts_evaluated`` + ``meta.contract_gate_failures``
#       (per-contract gate counts across the whole delta band, while
#       ``rejections_by_reason`` stays per-ticker for history comparability);
#       option rows carry ``last_price``/``last_trade_date``/``quote_quality``;
#       ``meta.market_session`` + ``meta.quotes_trusted`` stamp off-hours runs;
#       sized rows carry ``affordable``; ``meta.capital_warning`` surfaces the
#       B1 capital sanity check (null when the account fits the universe).
#   v4: rows may carry ``premium_used``/``premium_basis`` (conservative-fill
#       premium the yields are computed from), ``pop`` (1 - |delta|, the
#       probability the put expires worthless), ``iv_used``/``iv_band_median``
#       (robust IV actually used for the implied move); ``score_mode`` may be
#       ``risk_adjusted``; ``meta.market_session_end`` stamps the session at
#       the END of the run too (``quotes_trusted`` requires both endpoints in
#       regular hours).
#   v5: ``thresholds.quality`` (the numeric gate limits + ``risk_free_rate`` +
#       ``score_denominator_floor``) and ``thresholds.premium_basis``, so the
#       dashboard can re-run the quality gates client-side against live broker
#       quotes without hardcoding any threshold.
#   v6: header carries ``capital_source`` ("dashboard" when the selections
#       file's ``account.total_capital`` override was applied, else
#       "config.yaml") and the deployed split ``deployed_positions`` /
#       ``deployed_selections``, so the dashboard can recompute cash available
#       from the live selections file without double-counting the OPEN picks
#       baked into this snapshot.
#   v7: call-side (covered-call) context — rows may carry ``call_yield_ann``,
#       ``skew``, ``call_oi``, ``call_spread_pct``, ``thin_call_side``, and
#       ``dividend_yield``; ``data_flags`` may include the advisory
#       ``thin_call_side`` code (advisory flags never route rows to
#       near-miss); ``thresholds.call_side`` echoes the call_side config.
SCHEMA_VERSION = 7

CSV_COLUMNS = [
    "ticker", "sector", "expiration", "dte", "strike", "mid", "premium_used",
    "abs_delta", "pop",
    "roc", "annualized_yield", "yield_30dte", "distance_to_strike", "implied_move",
    "score", "max_contracts", "collateral_per_contract", "affordable",
    "breaches_per_name_cap", "min_account_for_1_contract",
    "call_yield_ann", "skew", "thin_call_side", "dividend_yield", "flags",
]


def _flag_codes(row: dict[str, Any]) -> str:
    return ";".join(e.get("code", "?") for e in row.get("data_flags") or [])


def build_header(regime: Any, account: Any, config: dict[str, Any]) -> dict[str, Any]:
    total_capital = float(config["account"]["total_capital"])
    deployed = account.total_deployed
    deployed_positions = float(getattr(account, "deployed_positions", 0.0) or 0.0)
    return {
        "regime_light": regime.light,
        "regime_tripped": regime.tripped,
        "total_capital": total_capital,
        "capital_source": ("dashboard"
                           if getattr(account, "total_capital_override", None) is not None
                           else "config.yaml"),
        "deployed": deployed,
        "deployed_positions": deployed_positions,
        "deployed_selections": deployed - deployed_positions,
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
        codes = _flag_codes(r)
        if codes:
            flags = f"{flags} {codes}".strip()
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
            writer.writerow({**r, "flags": _flag_codes(r)})
    return path


# --- JSON snapshot (dashboard feed) ----------------------------------------
def _json_safe(obj: Any) -> Any:
    """Recursively replace non-finite floats (inf/nan) with None.

    ``json.dump`` would emit ``Infinity``/``NaN`` literals, which the browser's
    ``JSON.parse`` rejects. ``min_account_for_1_contract`` can be ``math.inf``.
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def write_json(
    header: dict[str, Any],
    rows: list[dict[str, Any]],
    regime: Any,
    config: dict[str, Any],
    path: str | Path,
    *,
    near_misses: list[dict[str, Any]] | None = None,
    meta_extra: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> Path:
    """Write a self-contained run snapshot the static dashboard consumes.

    Reuses the already-built ``header`` (``build_header``), the ranked ``rows``
    (full dicts, not the trimmed CSV columns), and the ``Regime`` dataclass.
    ``near_misses`` are fully sized/scored rows that failed a quality gate or
    carry data flags — same shape as ``rows`` plus ``rejection_reasons`` and
    ``data_flags``. Written atomically (temp + ``os.replace``) like
    ``cache.DiskCache.set``.
    """
    now = generated_at or datetime.now(timezone.utc)
    quality = config.get("quality", {})
    call_side = config.get("call_side") or {}
    near_misses = near_misses or []
    meta = {
        "generated_at": now.isoformat(timespec="seconds"),
        "run_date": now.date().isoformat(),
        "candidate_count": len(rows),
        "near_miss_count": len(near_misses),
    }
    if meta_extra:
        meta.update(meta_extra)

    doc = {
        "schema_version": SCHEMA_VERSION,
        "meta": meta,
        "regime": {
            "light": regime.light,
            "tripped": regime.tripped,
            "signals": dict(regime.signals),
        },
        "header": header,
        "thresholds": {
            "dte": config.get("dte"),
            "delta": config.get("delta"),
            "scoring_mode": config.get("scoring", {}).get("mode"),
            "premium_basis": config.get("scoring", {}).get("premium_basis", "conservative"),
            "regime": config.get("regime"),
            "account": config.get("account"),
            "avoid_earnings_before_expiry": quality.get("avoid_earnings_before_expiry"),
            "unknown_earnings_policy": quality.get("unknown_earnings_policy"),
            "quality": {
                "min_yield_30dte": quality.get("min_yield_30dte"),
                "max_implied_move": quality.get("max_implied_move"),
                "max_spread_pct": quality.get("max_spread_pct"),
                "max_spread_abs": quality.get("max_spread_abs"),
                "min_open_interest": quality.get("min_open_interest"),
                "min_distance_to_strike": quality.get("min_distance_to_strike"),
                "risk_free_rate": quality.get("risk_free_rate", 0.04),
                "score_denominator_floor": quality.get("score_denominator_floor"),
            },
            # Advisory thresholds only — they mint the thin_call_side flag,
            # never a gate (see screen.evaluate_call_side). An absent config
            # section means the feature is off (attach_call_side no-ops).
            "call_side": {
                "enabled": bool(call_side) and bool(call_side.get("enabled", True)),
                "target_delta": call_side.get("target_delta", 0.25),
                "min_open_interest": call_side.get("min_open_interest", 10),
                "max_spread_pct": call_side.get("max_spread_pct", 0.25),
                "max_spread_abs": call_side.get("max_spread_abs", 0.15),
            },
        },
        "rows": rows,
        "near_misses": near_misses,
    }

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(_json_safe(doc), fh, indent=2)
    os.replace(tmp, path)
    return path


# --- optional push stubs ---------------------------------------------------
def push_notion(header: dict[str, Any], rows: list[dict[str, Any]]) -> None:  # pragma: no cover
    """Stub — wire up later if desired."""
    raise NotImplementedError("Notion push is an optional v2 stub.")


def push_telegram(header: dict[str, Any], rows: list[dict[str, Any]]) -> None:  # pragma: no cover
    """Stub — wire up later if desired."""
    raise NotImplementedError("Telegram push is an optional v2 stub.")
