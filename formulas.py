"""Pinned-down formulas (B5). Defined once, here, and unit-tested.

- Delta handling: puts have negative delta; compare on abs(delta).
- Implied move (contract life): IV_annual * sqrt(DTE/365)  (1 sigma).
- ROC: premium / collateral.   Annualized: ROC * (365 / DTE).
- Collateral: strike * 100.
- Mid: (bid + ask) / 2.   spread%: (ask - bid) / mid.
"""
from __future__ import annotations

import math


def mid(bid: float, ask: float) -> float:
    return (bid + ask) / 2.0


def spread_pct(bid: float, ask: float) -> float:
    m = mid(bid, ask)
    if m <= 0:
        return math.inf
    return (ask - bid) / m


def collateral(strike: float, contracts: int = 1) -> float:
    return strike * 100.0 * contracts


def roc(premium: float, strike: float) -> float:
    """Return on collateral for one contract. premium is per-share (mid)."""
    coll = collateral(strike, 1)
    if coll <= 0:
        return 0.0
    return (premium * 100.0) / coll


def annualized_yield(premium: float, strike: float, dte: int) -> float:
    if dte <= 0:
        return 0.0
    return roc(premium, strike) * (365.0 / dte)


def yield_30dte(premium: float, strike: float, dte: int) -> float:
    """ROC normalized to a 30-day holding period."""
    if dte <= 0:
        return 0.0
    return roc(premium, strike) * (30.0 / dte)


def implied_move(iv_annual: float, dte: int) -> float:
    """1-sigma move over the contract's life."""
    if iv_annual is None or dte <= 0:
        return 0.0
    return iv_annual * math.sqrt(dte / 365.0)


def distance_to_strike(spot: float, strike: float) -> float:
    """For a CSP, fractional cushion below spot. Positive when strike < spot."""
    if spot <= 0:
        return 0.0
    return (spot - strike) / spot


def bs_put_delta(spot: float, strike: float, dte: int, iv: float, r: float = 0.04) -> float | None:
    """Black-Scholes put delta fallback (negative). Requires py_vollib."""
    if not spot or not strike or not iv or dte <= 0:
        return None
    try:
        from py_vollib.black_scholes.greeks.analytical import delta as bs_delta
    except ImportError:  # pragma: no cover
        return _bs_put_delta_native(spot, strike, dte, iv, r)
    t = dte / 365.0
    return bs_delta("p", spot, strike, t, r, iv)


def _bs_put_delta_native(spot: float, strike: float, dte: int, iv: float, r: float) -> float:
    """Closed-form fallback if py_vollib is unavailable: delta_put = N(d1) - 1."""
    t = dte / 365.0
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    norm_cdf = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
    return norm_cdf - 1.0
