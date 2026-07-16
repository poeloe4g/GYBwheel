"""Account-aware sizing (F08 / B1, B3, Spec 1.4).

Computes collateral/ROC/annualized and enforces per-name, per-sector, and
total-deployed caps using current state from positions.yaml. Over-cap names are
never silently dropped: they are shown WITH ``breaches_per_name_cap = True`` and
``min_account_for_1_contract`` so the cap stays honest (B1).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

import formulas

log = logging.getLogger("size")


@dataclass
class AccountState:
    total_deployed: float = 0.0
    per_sector: dict[str, float] = field(default_factory=dict)
    per_ticker: dict[str, float] = field(default_factory=dict)
    positions_loaded: bool = False
    source: str = "greenfield (no positions.yaml)"


def _apply(state: AccountState, ticker: str, sector: str, collateral: float) -> None:
    state.total_deployed += collateral
    state.per_sector[sector] = state.per_sector.get(sector, 0.0) + collateral
    state.per_ticker[ticker] = state.per_ticker.get(ticker, 0.0) + collateral


def load_positions(
    path: str | Path = "positions.yaml",
    selections_path: str | Path = "site/data/selections.json",
) -> AccountState:
    """Load current positions. Absent file => greenfield (B3), flagged in header.

    Two sources merge into one AccountState:
      - positions.yaml — positions opened outside the dashboard (manual);
      - site/data/selections.json — OPEN picks selected in the dashboard.
    The same position must live in only one of them or it counts double.
    """
    sources: list[str] = []
    state = AccountState()

    p = Path(path)
    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        for pos in data.get("positions", []) or []:
            _apply(state, (pos.get("ticker") or "").upper(),
                   pos.get("sector", "Unknown"), float(pos.get("collateral", 0) or 0))
        sources.append(str(p))

    n_open = _apply_open_selections(state, selections_path)
    if n_open:
        sources.append(f"{n_open} open selection{'s' if n_open != 1 else ''}")

    if sources:
        state.positions_loaded = True
        state.source = " + ".join(sources)
    return state


def _apply_open_selections(state: AccountState, path: str | Path) -> int | None:
    """Accumulate OPEN dashboard selections; None if no readable file.

    A malformed file or entry must never fail the run — warn and skip, like
    build_index tolerates corrupt snapshots.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        entries = doc.get("selections") or []
    except (json.JSONDecodeError, OSError, AttributeError) as exc:
        log.warning("selections file %s unreadable (%s) — ignoring", p, exc)
        return None
    n_open = 0
    for sel in entries:
        if not isinstance(sel, dict) or sel.get("status") != "OPEN":
            continue
        try:
            coll = float(sel["collateral"])
            ticker = str(sel["ticker"]).upper()
        except (KeyError, TypeError, ValueError):
            log.warning("skipping malformed selection entry: %r", sel.get("uid", sel))
            continue
        _apply(state, ticker, sel.get("sector", "Unknown"), coll)
        n_open += 1
    return n_open


def size_candidate(
    candidate: dict[str, Any], account: AccountState, config: dict[str, Any],
) -> dict[str, Any]:
    """Augment a candidate with sizing math, cap flags, and max_contracts."""
    acct = config["account"]
    total_capital = float(acct["total_capital"])
    max_pct_name = float(acct["max_pct_per_name"])
    max_pct_sector = float(acct["max_pct_per_sector"])
    max_pct_deployed = float(acct["max_pct_deployed"])

    strike = float(candidate["strike"])
    # premium_used (main._effective_premium) is the conservative expected fill;
    # yields computed from it, not the optimistic raw mid, so the ranking
    # reflects money you can realistically collect.
    premium = float(candidate.get("premium_used") or candidate["mid"])
    dte = int(candidate["dte"])
    ticker = (candidate.get("ticker") or "").upper()
    sector = candidate.get("sector", "Unknown")

    coll_per_contract = formulas.collateral(strike, 1)

    per_name_cap = max_pct_name * total_capital
    per_sector_cap = max_pct_sector * total_capital
    total_cap = max_pct_deployed * total_capital

    name_headroom = per_name_cap - account.per_ticker.get(ticker, 0.0)
    sector_headroom = per_sector_cap - account.per_sector.get(sector, 0.0)
    total_headroom = total_cap - account.total_deployed

    binding_headroom = max(0.0, min(name_headroom, sector_headroom, total_headroom))
    max_contracts = int(math.floor(binding_headroom / coll_per_contract)) if coll_per_contract > 0 else 0

    breaches = coll_per_contract > per_name_cap
    min_account_for_1 = coll_per_contract / max_pct_name if max_pct_name > 0 else math.inf

    return {
        **candidate,
        "collateral_per_contract": coll_per_contract,
        "roc": formulas.roc(premium, strike),
        "annualized_yield": formulas.annualized_yield(premium, strike, dte),
        "yield_30dte": formulas.yield_30dte(premium, strike, dte),
        "max_contracts": max_contracts,
        # Headroom-aware: false when even one contract exceeds what the caps
        # (per-name/sector/total, net of open positions) leave available.
        "affordable": max_contracts >= 1,
        "breaches_per_name_cap": breaches,
        "min_account_for_1_contract": round(min_account_for_1, 2),
        "name_headroom": round(name_headroom, 2),
        "sector_headroom": round(sector_headroom, 2),
        "total_headroom": round(total_headroom, 2),
    }


def sanity_check_capital(candidates_strikes: list[float], config: dict[str, Any]) -> str | None:
    """Warn (B1) if total_capital is too small for typical universe strikes."""
    if not candidates_strikes:
        return None
    acct = config["account"]
    per_name_cap = float(acct["max_pct_per_name"]) * float(acct["total_capital"])
    typical = sorted(candidates_strikes)[len(candidates_strikes) // 2]
    typical_collateral = formulas.collateral(typical, 1)
    if typical_collateral > per_name_cap:
        return (
            f"total_capital ${acct['total_capital']:,.0f} is small: the typical strike "
            f"(${typical:.0f}) needs ${typical_collateral:,.0f} collateral but the per-name "
            f"cap is only ${per_name_cap:,.0f}. Most single contracts will breach the cap."
        )
    return None
