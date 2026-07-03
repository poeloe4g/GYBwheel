"""Universe builder & ban list (F05 / Spec 1.1).

Applies the 1.1 fundamental filters, drops the ban list, honors an optional
allow list. Each drop is logged with a reason and returned to the caller so
runs can report why names fell out. The passing universe is cached and reused
within ``data.universe_refresh_days`` (B7); the cache is keyed to the candidate
list so a changed list invalidates it.
"""
from __future__ import annotations

import hashlib
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
    # has_options is tri-state: only a definite False rejects — None (unknown)
    # passes and truly optionless names fall out at the no-put-in-window stage.
    if u.get("require_options") and f.get("has_options") is False:
        return "no listed options"
    if not f.get("sector"):
        return "no sector tag"
    return None


def _candidates_hash(candidates: list[str]) -> str:
    return hashlib.sha1(",".join(sorted(t.upper() for t in candidates)).encode()).hexdigest()


def build_universe(
    candidates: list[str], provider: Any, config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return ``(passing, rejects)`` using the weekly cache.

    ``passing`` carries fundamentals dicts; ``rejects`` carries
    ``{"ticker", "code": "universe", "message"}`` entries for every drop.
    """
    u = config["universe"]
    refresh_days = config.get("data", {}).get("universe_refresh_days", 7)
    cand_hash = _candidates_hash(candidates)

    cached = provider.cache.get("universe", "passing")
    if (
        cached
        and _cache_fresh(cached.get("built"), refresh_days)
        and cached.get("candidates_hash") == cand_hash
    ):
        log.info("Using cached universe from %s (%d names)", cached["built"], len(cached["names"]))
        return cached["names"], cached.get("rejects", [])

    ban = {t.upper() for t in u.get("ban_list", [])}
    allow = {t.upper() for t in u.get("allow_list", [])}

    passing: list[dict[str, Any]] = []
    rejects: list[dict[str, str]] = []

    def drop(sym: str, message: str) -> None:
        log.info("DROP %s: %s", sym, message)
        rejects.append({"ticker": sym, "code": "universe", "message": message})

    for ticker in candidates:
        sym = ticker.upper()
        if sym in ban:
            drop(sym, "on ban list")
            continue
        if allow and sym not in allow:
            drop(sym, "not on allow list")
            continue
        try:
            f = provider.get_fundamentals(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("DROP %s: fundamentals error: %s", sym, exc)
            rejects.append({"ticker": sym, "code": "universe", "message": f"fundamentals error: {exc}"})
            continue
        reason = _passes_fundamentals(f, u)
        if reason:
            drop(sym, reason)
            continue
        passing.append(f)

    provider.cache.set("universe", "passing", {
        "built": date.today().isoformat(),
        "candidates_hash": cand_hash,
        "names": passing,
        "rejects": rejects,
    })
    return passing, rejects


def _cache_fresh(built: str | None, refresh_days: int) -> bool:
    if not built:
        return False
    try:
        built_date = datetime.strptime(built, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - built_date).days < refresh_days
