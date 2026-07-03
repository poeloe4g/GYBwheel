"""Contract selection and quality filtering.

F04 — nearest-delta put selector (B5).
F06 — earnings filter (B2).
F07 — trade-quality filters (Spec 1.3, B5).

Every rejection records a human-readable reason.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import formulas


def _effective_abs_delta(opt: dict[str, Any], spot: float, risk_free_rate: float) -> float | None:
    """abs(delta) from the greeks feed, or a Black-Scholes fallback (B5)."""
    delta = opt.get("delta")
    if delta is not None:
        return abs(delta)
    bs = formulas.bs_put_delta(spot, opt.get("strike"), opt.get("dte"), opt.get("iv"),
                               risk_free_rate)
    return abs(bs) if bs is not None else None


def select_nearest_delta_put(
    chain: list[dict[str, Any]], spot: float, *,
    target_delta: float, delta_min: float, delta_max: float,
    risk_free_rate: float = 0.04,
) -> dict[str, Any] | None:
    """Return the put whose abs(delta) is nearest target within [min, max].

    Returns None if no put qualifies. Negative put deltas handled via abs().
    """
    best: dict[str, Any] | None = None
    best_dist = float("inf")
    for opt in chain:
        if opt.get("option_type") != "put":
            continue
        ad = _effective_abs_delta(opt, spot, risk_free_rate)
        if ad is None or not (delta_min <= ad <= delta_max):
            continue
        dist = abs(ad - target_delta)
        if dist < best_dist:
            best_dist = dist
            best = {**opt, "abs_delta": ad}
    return best


def passes_earnings_filter(
    expiration: str, next_earnings: str | None, *, avoid: bool,
) -> tuple[bool, str | None]:
    """Reject a contract whose expiry is on/after the next earnings date (B2).

    Missing earnings data degrades gracefully: accept, with a warning reason.
    """
    if not avoid:
        return True, None
    if not next_earnings:
        return True, "earnings date unknown — not filtered"
    exp = datetime.strptime(expiration, "%Y-%m-%d").date()
    earn = datetime.strptime(next_earnings, "%Y-%m-%d").date()
    if exp >= earn:
        return False, f"expiry {expiration} spans earnings {next_earnings}"
    return True, None


def _entry(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def apply_quality_filters(
    opt: dict[str, Any], spot: float, quality: dict[str, Any],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return ``(rejections, flags)``; ``([], [])`` == passes all filters cleanly.

    Rejections are hard gate failures. Flags mark gates that could not be
    evaluated because the feed lacked data (missing IV/quotes/OI) — the caller
    decides how to route flagged contracts; they must never pass silently.
    Each entry is ``{"code", "message"}``.
    """
    reasons: list[dict[str, str]] = []
    flags: list[dict[str, str]] = []
    strike = opt.get("strike")
    premium = opt.get("mid")
    dte = opt.get("dte")
    iv = opt.get("iv")
    bid, ask = opt.get("bid"), opt.get("ask")

    if premium is None or premium <= 0:
        return [_entry("no_premium", "no valid premium (mid <= 0)")], []
    if strike is None or dte is None or dte <= 0:
        return [_entry("missing_strike_dte", "missing strike/DTE")], []

    y30 = formulas.yield_30dte(premium, strike, dte)
    if y30 < quality["min_yield_30dte"]:
        reasons.append(_entry("yield_30dte",
                              f"yield/30DTE {y30:.4f} < {quality['min_yield_30dte']}"))

    if iv is None:
        flags.append(_entry("iv_missing", "no IV from feed — implied-move gate not evaluated"))
    else:
        im = formulas.implied_move(iv, dte)
        if im > quality["max_implied_move"]:
            reasons.append(_entry("implied_move",
                                  f"implied move {im:.4f} > {quality['max_implied_move']}"))

    if bid is None or ask is None:
        flags.append(_entry("spread_unknown", "no bid/ask from feed — spread gate not evaluated"))
    else:
        sp = formulas.spread_pct(bid, ask)
        spread_abs = ask - bid
        # A tight absolute spread is acceptable even when it is a large share of
        # a small mid (e.g. a $0.05-wide market on a $0.50 premium).
        if sp > quality["max_spread_pct"] and spread_abs > quality.get("max_spread_abs", float("inf")):
            reasons.append(_entry("spread", f"spread {sp:.4f} > {quality['max_spread_pct']}"))

    oi = opt.get("open_interest")
    if oi is None:
        flags.append(_entry("oi_unknown", "no open interest from feed — OI gate not evaluated"))
    elif oi < quality["min_open_interest"]:
        reasons.append(_entry("open_interest", f"OI {oi} < {quality['min_open_interest']}"))

    dist = formulas.distance_to_strike(spot, strike)
    if dist < quality["min_distance_to_strike"]:
        reasons.append(_entry("distance",
                              f"distance {dist:.4f} < {quality['min_distance_to_strike']}"))

    return reasons, flags
