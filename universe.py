"""Universe builder & ban list (F05 / Spec 1.1).

Applies the 1.1 fundamental filters, drops the ban list, honors an optional
allow list. Each drop is logged with a reason. The passing universe is cached
and reused within ``data.universe_refresh_days`` (B7).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

log = logging.getLogger("universe")


def _passes_fundamentals(f: dict[str, Any], u: dict[str, Any]) -> str | None:
    """Return a rejection reason, or None if the name passes."""
    mc = f.get("market_cap")
    if mc is None or mc < u["min_market_cap"]:
        return f"market_cap {mc} < {u['min_market_cap']}"
    vol = f.get("avg_volume")
    if vol is None or vol < u["min_avg_volume"]:
        return f"avg_volume {vol} < {u['min_avg_volume']}"
    if u.get("require_profitable") and (f.get("net_income") is None or f["net_income"] <= 0):
        return f"not profitable (net_income={f.get('net_income')})"
    if u.get("require_positive_fcf") and (f.get("free_cash_flow") is None or f["free_cash_flow"] <= 0):
        return f"non-positive FCF ({f.get('free_cash_flow')})"
    if u.get("require_options") and not f.get("has_options"):
        return "no listed options"
    if not f.get("sector"):
        return "no sector tag"
    return None


def build_universe(
    candidates: list[str], provider: Any, config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the list of passing names (with fundamentals), using the weekly cache."""
    u = config["universe"]
    refresh_days = config.get("data", {}).get("universe_refresh_days", 7)

    cached = provider.cache.get("universe", "passing")
    if cached and _cache_fresh(cached.get("built"), refresh_days):
        log.info("Using cached universe from %s (%d names)", cached["built"], len(cached["names"]))
        return cached["names"]

    ban = {t.upper() for t in u.get("ban_list", [])}
    allow = {t.upper() for t in u.get("allow_list", [])}

    passing: list[dict[str, Any]] = []
    for ticker in candidates:
        sym = ticker.upper()
        if sym in ban:
            log.info("DROP %s: on ban list", sym)
            continue
        if allow and sym not in allow:
            log.info("DROP %s: not on allow list", sym)
            continue
        try:
            f = provider.get_fundamentals(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("DROP %s: fundamentals error: %s", sym, exc)
            continue
        reason = _passes_fundamentals(f, u)
        if reason:
            log.info("DROP %s: %s", sym, reason)
            continue
        passing.append(f)

    provider.cache.set("universe", "passing", {"built": date.today().isoformat(), "names": passing})
    return passing


def _cache_fresh(built: str | None, refresh_days: int) -> bool:
    if not built:
        return False
    try:
        built_date = datetime.strptime(built, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - built_date).days < refresh_days
