"""Scoring & ranking (F10 / B4, Spec 2.6).

  score = annualized_yield * distance_to_strike / max(implied_move, floor)

``scoring.mode = annualized_yield_only`` switches to ranking on yield alone.
Components are always exposed on the row, not just the blended number.
"""
from __future__ import annotations

from typing import Any

import formulas


def score_candidate(row: dict[str, Any], config: dict[str, Any], spot: float) -> dict[str, Any]:
    quality = config["quality"]
    floor = float(quality["score_denominator_floor"])
    mode = config.get("scoring", {}).get("mode", "blended")

    ann = float(row.get("annualized_yield", 0.0))
    dist = formulas.distance_to_strike(spot, float(row["strike"]))
    iv = row.get("iv")
    imp_move = formulas.implied_move(iv, int(row["dte"])) if iv is not None else 0.0
    denom = max(imp_move, floor)

    if mode == "annualized_yield_only":
        score = ann
    else:
        score = ann * dist / denom

    return {
        **row,
        "distance_to_strike": dist,
        "implied_move": imp_move,
        "score_denominator": denom,
        "score": score,
        "score_mode": mode,
    }


def rank(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: r.get("score", 0.0), reverse=True)
