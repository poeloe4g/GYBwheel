"""Account-aware sizing (F08 / B1, B3, Spec 1.4).

Computes collateral/ROC/annualized and enforces per-name, per-sector, and
total-deployed caps using current state from positions.yaml. Over-cap names are
never silently dropped: they are shown WITH ``breaches_per_name_cap = True`` and
``min_account_for_1_contract`` so the cap stays honest (B1).
"""
from __future__ import annotations

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


def load_positions(path: str | Path = "positions.yaml") -> AccountState:
    """Load current positions. Absent file => greenfield (B3), flagged in header."""
    p = Path(path)
    if not p.exists():
        return AccountState()
    with p.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    positions = data.get("positions", []) or []
    state = AccountState(positions_loaded=True, source=str(p))
    for pos in positions:
        coll = float(pos.get("collateral", 0) or 0)
        state.total_deployed += coll
        sector = pos.get("sector", "Unknown")
        state.per_sector[sector] = state.per_sector.get(sector, 0.0) + coll
        ticker = (pos.get("ticker") or "").upper()
        state.per_ticker[ticker] = state.per_ticker.get(ticker, 0.0) + coll
    return state


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
    premium = float(candidate["mid"])
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
