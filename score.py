"""Scoring & ranking (F10 / B4, Spec 2.6).

Modes (``scoring.mode``):

  risk_adjusted (default)
      score = annualized_yield * pop * distance_to_strike / max(implied_move, floor)
      where ``pop`` = 1 - abs(delta), the Black-Scholes probability the put
      expires worthless (delta ~ P(ITM)). This is an expected-yield ranking:
      the premium is weighted by the chance of actually keeping it, then
      scaled by the cushion measured in units of the market's own expected
      move — so a fat premium on a knife-edge strike no longer outranks a
      slightly smaller premium with far better odds.
  blended
      score = annualized_yield * distance_to_strike / max(implied_move, floor)
      (the legacy default, kept for comparability with old snapshots).
  annualized_yield_only
      score = annualized_yield.

Components are always exposed on the row, not just the final number.

IV robustness: yfinance per-contract IVs are occasionally junk. When the row
carries ``iv_band_median`` (attached by ``screen.evaluate_puts``) and its own
IV is missing or exceeds ``quality.iv_outlier_mult`` x that median, the median
is used for the implied move instead — the IV actually used is exposed as
``iv_used``.
"""
from __future__ import annotations

from typing import Any

import formulas


def _robust_iv(row: dict[str, Any], quality: dict[str, Any]) -> float | None:
    """The row's own IV unless it is missing or an outlier vs the band median."""
    iv = row.get("iv")
    median = row.get("iv_band_median")
    if median is None:
        return iv
    if iv is None:
        return median
    mult = quality.get("iv_outlier_mult")
    if mult and iv > float(mult) * float(median):
        return median
    return iv


def score_candidate(row: dict[str, Any], config: dict[str, Any], spot: float) -> dict[str, Any]:
    quality = config["quality"]
    floor = float(quality["score_denominator_floor"])
    mode = config.get("scoring", {}).get("mode", "risk_adjusted")

    ann = float(row.get("annualized_yield", 0.0))
    dist = formulas.distance_to_strike(spot, float(row["strike"]))
    iv_used = _robust_iv(row, quality)
    imp_move = formulas.implied_move(iv_used, int(row["dte"])) if iv_used is not None else 0.0
    denom = max(imp_move, floor)

    abs_delta = row.get("abs_delta")
    # P(expires worthless) ~ 1 - |delta|; without a delta, degrade to no weight.
    pop = max(0.0, min(1.0, 1.0 - float(abs_delta))) if abs_delta is not None else None

    if mode == "annualized_yield_only":
        score = ann
    elif mode == "risk_adjusted":
        score = ann * (pop if pop is not None else 1.0) * dist / denom
    else:  # blended
        score = ann * dist / denom

    return {
        **row,
        "distance_to_strike": dist,
        "implied_move": imp_move,
        "iv_used": iv_used,
        "pop": pop,
        "score_denominator": denom,
        "score": score,
        "score_mode": mode,
    }


def rank(
    rows: list[dict[str, Any]], *,
    prefer_affordable: bool = False,
    prefer_live_quotes: bool = False,
    prefer_two_sided: bool = False,
) -> list[dict[str, Any]]:
    """Sort by score, with optional actionability tiers ranked first.

    ``prefer_affordable``: an unaffordable row (``size.size_candidate``'s
    headroom-aware flag) can't be acted on at 1 contract, so a lower-scoring
    affordable name outranks it.

    ``prefer_live_quotes``: a row whose premium came from the last trade
    instead of a live two-sided market (``quote_quality != "live"``) has a
    less trustworthy score, so live-quote rows rank first within a tier.

    ``prefer_two_sided``: rows flagged ``thin_call_side`` (the wheel's second
    leg looks hard to sell — ``screen.evaluate_call_side``) rank below clean
    rows. Deliberately the WEAKEST tier: a sanity check, not a driver, and a
    missing/unmeasured call side counts as clean so absent data never sinks a
    row.
    """
    def key(r: dict[str, Any]) -> tuple:
        k: list[Any] = []
        if prefer_affordable:
            k.append(not r.get("affordable", False))
        if prefer_live_quotes:
            k.append(r.get("quote_quality", "live") != "live")
        if prefer_two_sided:
            k.append(bool(r.get("thin_call_side") or False))
        k.append(-r.get("score", 0.0))
        return tuple(k)

    return sorted(rows, key=key)
