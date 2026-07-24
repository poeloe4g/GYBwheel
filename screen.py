"""Contract selection and quality filtering.

F04 — nearest-delta put selector (B5).
F06 — earnings filter (B2).
F07 — trade-quality filters (Spec 1.3, B5).
``evaluate_puts`` — gate-then-select over the whole delta band.
``evaluate_call_side`` / ``attach_call_side`` — covered-call context metrics
(advisory only: they mint fields and a ``thin_call_side`` flag, never a
rejection).

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


def _effective_abs_call_delta(opt: dict[str, Any], spot: float, risk_free_rate: float) -> float | None:
    """Call-side twin of ``_effective_abs_delta``."""
    delta = opt.get("delta")
    if delta is not None:
        return abs(delta)
    bs = formulas.bs_call_delta(spot, opt.get("strike"), opt.get("dte"), opt.get("iv"),
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
        # iv_band_median rides along so scoring can swap a junk/missing
        # per-contract IV for the band's median (see score._robust_iv).
        evaluated.append({**opt, "iv_band_median": median_iv,
                          "rejections": rejections, "flags": flags})
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


# Null shape returned when the call side can't be measured — absent calls or
# unusable data must produce silence (no flag), never a demotion.
_CALL_SIDE_NULL: dict[str, Any] = {
    "call_yield_ann": None, "skew": None, "call_oi": None,
    "call_spread_pct": None, "thin_call_side": False,
}


def evaluate_call_side(
    chain: list[dict[str, Any]], spot: float, put_row: dict[str, Any],
    call_cfg: dict[str, Any], risk_free_rate: float = 0.04,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Covered-call context for a put candidate: ``(fields, flags)``.

    The wheel's second leg: if this put is assigned, how workable is selling
    calls on the shares? From the calls at the put's own expiration, the
    "mirror call" nearest ``call_cfg.target_delta`` is measured:

    - ``call_yield_ann`` — the mirror call's annualized premium yield on the
      put-strike collateral (the shares' cost if assigned), directly comparable
      to the put's ``annualized_yield``. Computed from the raw mid — this is a
      context metric that never feeds the score, so the conservative-fill
      premium basis is deliberately not applied.
    - ``skew`` — put IV minus mirror-call IV; steep skew means the put leg's
      richness is not matched on the call side.
    - ``call_oi`` / ``call_spread_pct`` — the mirror call's liquidity.
    - ``thin_call_side`` — True only on *definitive* evidence of a thin call
      market: OI reported below ``min_open_interest``, or a live two-sided
      quote whose spread exceeds BOTH ``max_spread_pct`` and
      ``max_spread_abs`` (the same absolute-spread rescue as the put gate).

    Missing calls, missing OI/IV, or indicative quotes yield null fields and no
    flag — advisory data must never punish a name for being unmeasurable. The
    returned flag (at most one, ``thin_call_side``) is advisory: `main`
    excludes it from near-miss routing.
    """
    target = float(call_cfg.get("target_delta", 0.25))
    best: dict[str, Any] | None = None
    best_dist = float("inf")
    for opt in chain:
        if opt.get("option_type") != "call" or opt.get("expiration") != put_row.get("expiration"):
            continue
        ad = _effective_abs_call_delta(opt, spot, risk_free_rate)
        if ad is None:
            continue
        dist = abs(ad - target)
        if dist < best_dist:
            best_dist = dist
            best = opt

    fields = dict(_CALL_SIDE_NULL)
    if best is None:
        return fields, []

    mid, put_strike, dte = best.get("mid"), put_row.get("strike"), put_row.get("dte")
    if mid and put_strike and dte:
        fields["call_yield_ann"] = formulas.annualized_yield(mid, put_strike, dte)

    # Raw put IV (or the band median): iv_used is only derived later in
    # score.score_candidate, so the rare IV-outlier row can diverge slightly.
    put_iv = put_row.get("iv") or put_row.get("iv_band_median")
    call_iv = best.get("iv")
    if put_iv is not None and call_iv is not None:
        fields["skew"] = put_iv - call_iv

    oi = best.get("open_interest")
    fields["call_oi"] = oi

    bid, ask = best.get("bid"), best.get("ask")
    live = bid is not None and ask is not None and best.get("quote_quality", "live") == "live"
    if live:
        fields["call_spread_pct"] = formulas.spread_pct(bid, ask)

    thin_reason: str | None = None
    min_oi = call_cfg.get("min_open_interest")
    if min_oi is not None and oi is not None and oi < min_oi:
        thin_reason = f"mirror-call OI {oi} < {min_oi}"
    elif live:
        sp, spread_abs = fields["call_spread_pct"], ask - bid
        if (sp > call_cfg.get("max_spread_pct", float("inf"))
                and spread_abs > call_cfg.get("max_spread_abs", float("inf"))):
            thin_reason = f"mirror-call spread {sp:.4f} > {call_cfg['max_spread_pct']}"

    if thin_reason is None:
        return fields, []
    fields["thin_call_side"] = True
    return fields, [_entry(
        "thin_call_side",
        f"{thin_reason} — covered calls may be hard to sell if assigned")]


def attach_call_side(
    result: dict[str, Any], chain: list[dict[str, Any]], spot: float,
    call_cfg: dict[str, Any] | None, risk_free_rate: float = 0.04,
) -> dict[str, Any]:
    """Annotate an ``evaluate_puts`` result's selected/fallback rows with
    call-side fields — copy-not-mutate, so selected/fallback pointing at the
    same dict can't double-append flags.

    Absent or ``enabled: false`` config is a no-op (rows simply lack the call
    fields).
    """
    if not call_cfg or not call_cfg.get("enabled", True):
        return result
    out = dict(result)
    for key in ("selected", "fallback"):
        row = result.get(key)
        if row is None:
            continue
        fields, flags = evaluate_call_side(chain, spot, row, call_cfg, risk_free_rate)
        out[key] = {**row, **fields, "flags": row.get("flags", []) + flags}
    return out


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
    elif opt.get("quote_quality", "live") != "live":
        # Zeroed/crossed off-hours quotes make (ask-bid)/mid meaningless; the
        # mid already degraded to the last trade in the data layer.
        flags.append(_entry("quote_indicative",
                            "bid/ask unusable — mid from last trade; spread gate not evaluated"))
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
