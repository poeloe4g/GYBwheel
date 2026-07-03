"""Contract selection and quality filtering.

F04 — nearest-delta put selector (B5).
F06 — earnings filter (B2).
F07 — trade-quality filters (Spec 1.3, B5).
``evaluate_puts`` — gate-then-select over the whole delta band.

Every rejection records a human-readable reason.
"""
from __future__ import annotations

import statistics
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


# Contracts with these rejection codes cannot be sized or scored at all, so
# they make the worst possible fallback pick.
UNSIZEABLE_CODES = {"no_premium", "missing_strike_dte"}


def evaluate_puts(
    chain: list[dict[str, Any]], spot: float, *,
    target_delta: float, delta_min: float, delta_max: float,
    quality: dict[str, Any], risk_free_rate: float = 0.04,
    next_earnings: str | None = None,
) -> dict[str, Any]:
    """Gate every in-band put, then pick the nearest-target-delta qualifier.

    The legacy flow picked the delta-nearest contract first and gated it after,
    so a single illiquid strike could reject a ticker whose adjacent in-band
    strikes pass cleanly. Here every put in the delta band is gated — earnings
    checked against each contract's *own* expiration — and the qualifying
    contract nearest ``target_delta`` wins.

    Returns::

        {"selected":      qualifying contract (rejections == []; flags allowed),
                          or None,
         "fallback":      delta-nearest contract annotated with its rejections
                          and flags (None only when no put is in band) — the
                          near-miss representative when nothing qualifies,
         "n_in_band":     puts in the delta band,
         "n_qualifying":  puts that passed every gate,
         "gate_failures": {code: count} across ALL in-band contracts (the
                          calibration-grade counter; a ticker's near-miss row
                          only surfaces the fallback's own reasons)}

    IV sanity: yfinance per-contract IVs are occasionally junk (an outlier
    inflates both the implied-move gate and the BS-fallback delta). A contract
    whose IV exceeds ``quality.iv_outlier_mult`` × the median in-band IV keeps
    its other gate results but swaps an ``implied_move`` rejection for an
    ``iv_outlier`` flag, and loses fallback priority to sane-IV alternatives.
    """
    in_band: list[dict[str, Any]] = []
    for opt in chain:
        if opt.get("option_type") != "put":
            continue
        ad = _effective_abs_delta(opt, spot, risk_free_rate)
        if ad is None or not (delta_min <= ad <= delta_max):
            continue
        in_band.append({**opt, "abs_delta": ad})

    if not in_band:
        return {"selected": None, "fallback": None,
                "n_in_band": 0, "n_qualifying": 0, "gate_failures": {}}

    mult = quality.get("iv_outlier_mult")
    ivs = [o["iv"] for o in in_band if o.get("iv")]
    median_iv = statistics.median(ivs) if ivs else None

    avoid = quality.get("avoid_earnings_before_expiry", True)
    gate_failures: dict[str, int] = {}
    evaluated: list[dict[str, Any]] = []
    outlier_flags: list[bool] = []
    for opt in in_band:
        rejections, flags = apply_quality_filters(opt, spot, quality)

        iv = opt.get("iv")
        is_outlier = bool(mult and median_iv and iv and iv > float(mult) * median_iv)
        if is_outlier:
            rejections = [e for e in rejections if e["code"] != "implied_move"]
            flags.append(_entry(
                "iv_outlier",
                f"IV {iv:.2f} > {mult}x median in-band IV {median_iv:.2f} — "
                "implied-move gate not evaluated"))

        ok, reason = passes_earnings_filter(opt["expiration"], next_earnings, avoid=avoid)
        if not ok:
            rejections.append(_entry("earnings", reason or "spans earnings"))
        elif reason:
            flags.append(_entry("earnings_unknown", reason))

        for e in rejections:
            gate_failures[e["code"]] = gate_failures.get(e["code"], 0) + 1
        evaluated.append({**opt, "rejections": rejections, "flags": flags})
        outlier_flags.append(is_outlier)

    def delta_dist(o: dict[str, Any]) -> float:
        return abs(o["abs_delta"] - target_delta)

    qualifying = [o for o in evaluated if not o["rejections"]]
    selected = min(qualifying, key=delta_dist) if qualifying else None

    # Fallback = what the legacy selector would have shown as the near miss:
    # delta-nearest, except unsizeable and IV-outlier contracts lose priority.
    def fallback_key(i_o: tuple[int, dict[str, Any]]) -> tuple:
        i, o = i_o
        unsizeable = any(e["code"] in UNSIZEABLE_CODES for e in o["rejections"])
        return (unsizeable, outlier_flags[i], delta_dist(o))

    fallback = min(enumerate(evaluated), key=fallback_key)[1]

    return {"selected": selected, "fallback": fallback,
            "n_in_band": len(evaluated), "n_qualifying": len(qualifying),
            "gate_failures": gate_failures}


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
