"""Regime light (F09 / B8, Spec 1.5).

Three boolean signals are counted into a traffic light:
  0 tripped -> GREEN, 1 -> YELLOW, >=2 -> RED.
RED short-circuits the pipeline (manage-only).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

GREEN, YELLOW, RED = "GREEN", "YELLOW", "RED"


@dataclass
class Regime:
    light: str
    signals: dict[str, bool] = field(default_factory=dict)

    @property
    def tripped(self) -> list[str]:
        return [name for name, on in self.signals.items() if on]

    @property
    def is_red(self) -> bool:
        return self.light == RED


def _sma(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def compute_signals(
    spy_history: list[float], vix: float | None, breadth: float | None, config: dict[str, Any],
) -> dict[str, bool]:
    r = config["regime"]
    spy_below_200dma = False
    if len(spy_history) >= 200:
        sma200 = _sma(spy_history[-200:])
        spy_below_200dma = spy_history[-1] < sma200

    breadth_below_floor = breadth is not None and breadth < r["breadth_floor"]

    look = int(r.get("spy_falling_lookback", 5))
    spy_falling = len(spy_history) > look and spy_history[-1] < spy_history[-1 - look]
    vix_high_and_spy_falling = (vix is not None and vix > r["vix_high"]) and spy_falling

    return {
        "spy_below_200dma": bool(spy_below_200dma),
        "breadth_below_floor": bool(breadth_below_floor),
        "vix_high_and_spy_falling": bool(vix_high_and_spy_falling),
    }


def light_for(signals: dict[str, bool]) -> str:
    count = sum(1 for on in signals.values() if on)
    if count >= 2:
        return RED
    if count == 1:
        return YELLOW
    return GREEN


def assess(
    spy_history: list[float], vix: float | None, breadth: float | None, config: dict[str, Any],
) -> Regime:
    signals = compute_signals(spy_history, vix, breadth, config)
    return Regime(light=light_for(signals), signals=signals)
