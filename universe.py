"""Universe builder & ban list (F05 / Spec 1.1).

Applies the 1.1 fundamental filters, drops the ban list, honors an optional
allow list. Each drop is logged with a reason and returned to the caller so
runs can report why names fell out. The passing universe is cached and reused
within ``data.universe_refresh_days`` (B7).

The two exclusion lists are deliberately handled differently, because they are
edited on different clocks:

  - ``allow_list`` is config-only and structural, so it prefilters *before* the
    fundamentals fetch (no wasted calls) and is folded into the cache key — a
    change to it correctly invalidates the cached screen.
  - ``ban_list`` is maintained from the dashboard and changes often, so it is
    applied *after* every cache read and is deliberately NOT in the cache key.
    The cache therefore holds the unfiltered screen, which is what makes both
    directions instant: banning a name takes effect on the next run, and so does
    un-banning it. Folding it into the key instead would throw away the whole
    fundamentals cache on every edit; leaving it as a prefilter (the original
    behaviour) meant an edit did nothing at all for up to a week.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from typing import Any

import blacklist

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


def _candidates_hash(candidates: list[str], allow: set[str] | None = None) -> str:
    """Key the cached screen to what was actually fetched.

    The allow list is part of that (it decides which names get fetched at all);
    the ban list deliberately is not — see the module docstring.
    """
    payload = ",".join(sorted(t.upper() for t in candidates))
    if allow:
        payload += "|allow:" + ",".join(sorted(allow))
    return hashlib.sha1(payload.encode()).hexdigest()


def _allow_set(u: dict[str, Any]) -> set[str]:
    return {
        t.strip().upper() for t in (u.get("allow_list") or [])
        if isinstance(t, str) and t.strip()
    }


def _apply_ban_list(
    passing: list[dict[str, Any]],
    rejects: list[dict[str, str]],
    u: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Drop blacklisted names from an already-screened universe.

    Runs on the cache-hit path as well as a fresh build, so an edit to the list
    takes effect immediately in both directions. A blacklisted name that ALSO
    failed the fundamentals screen is reported once, as ``blacklisted`` — you
    excluded it on purpose, so that is the honest reason, and it keeps the
    rejection counts from double-counting a single ticker.
    """
    active, _expired = blacklist.normalize(u.get("ban_list", []))
    if not active:
        return passing, rejects

    def _reject(sym: str) -> dict[str, str]:
        message = blacklist.describe(active[sym])
        log.info("DROP %s: %s", sym, message)
        return {"ticker": sym, "code": "blacklisted", "message": message}

    kept_passing: list[dict[str, Any]] = []
    kept_rejects: list[dict[str, str]] = []
    banned_syms: list[str] = []

    for f in passing:
        sym = str(f.get("ticker", "")).upper()
        if sym in active:
            banned_syms.append(sym)
        else:
            kept_passing.append(f)

    for r in rejects:
        sym = str(r.get("ticker", "")).upper()
        if sym in active:
            banned_syms.append(sym)
        else:
            kept_rejects.append(r)

    # dict.fromkeys: one reject per ticker even if it somehow appeared twice
    banned = [_reject(sym) for sym in dict.fromkeys(banned_syms)]
    return kept_passing, kept_rejects + banned


def build_universe(
    candidates: list[str], provider: Any, config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return ``(passing, rejects)`` using the weekly cache.

    ``passing`` carries fundamentals dicts; ``rejects`` carries
    ``{"ticker", "code", "message"}`` entries for every drop — ``blacklisted``
    for a name you excluded yourself, ``universe`` for a fundamentals or
    allow-list drop. The codes are distinct so the dashboard never tells you a
    name you banned "failed the company-quality screen".
    """
    u = config["universe"]
    refresh_days = config.get("data", {}).get("universe_refresh_days", 7)
    allow = _allow_set(u)
    cand_hash = _candidates_hash(candidates, allow)

    cached = provider.cache.get("universe", "passing")
    if (
        cached
        and _cache_fresh(cached.get("built"), refresh_days)
        and cached.get("candidates_hash") == cand_hash
    ):
        log.info("Using cached universe from %s (%d names)", cached["built"], len(cached["names"]))
        names, rejects = cached["names"], cached.get("rejects", [])
        # A transient fundamentals fetch error must not exclude a name for the
        # whole cache window — retry just the errored tickers on every hit.
        errored = [r["ticker"] for r in rejects if r.get("transient")]
        if errored:
            log.info("retrying %d cached fundamentals errors: %s", len(errored), errored)
            retried_passing, retried_rejects = _screen_tickers(errored, provider, u)
            names = names + retried_passing
            rejects = [r for r in rejects if not r.get("transient")] + retried_rejects
            provider.cache.set("universe", "passing", {
                **cached, "names": names, "rejects": rejects,
            })
        return _apply_ban_list(names, rejects, u)

    prefiltered: list[str] = []
    rejects: list[dict[str, str]] = []
    for ticker in candidates:
        sym = ticker.upper()
        if allow and sym not in allow:
            log.info("DROP %s: not on allow list", sym)
            rejects.append({"ticker": sym, "code": "universe", "message": "not on allow list"})
        else:
            prefiltered.append(sym)

    passing, screen_rejects = _screen_tickers(prefiltered, provider, u)
    rejects.extend(screen_rejects)

    # Cached WITHOUT the ban list applied, on purpose: the cache is the expensive
    # fundamentals screen, and keeping it unfiltered is what lets an un-ban take
    # effect on the next run rather than when the cache expires.
    provider.cache.set("universe", "passing", {
        "built": date.today().isoformat(),
        "candidates_hash": cand_hash,
        "names": passing,
        "rejects": rejects,
    })
    return _apply_ban_list(passing, rejects, u)


def _screen_tickers(
    tickers: list[str], provider: Any, u: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Fetch fundamentals and apply the 1.1 filters to ``tickers``.

    Fetch failures are marked ``transient: True`` so the cache-hit path knows
    to retry them instead of treating a rate-limit blip as a week-long drop.
    """
    passing: list[dict[str, Any]] = []
    rejects: list[dict[str, str]] = []
    for sym in tickers:
        try:
            f = provider.get_fundamentals(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("DROP %s: fundamentals error: %s", sym, exc)
            rejects.append({"ticker": sym, "code": "universe",
                            "message": f"fundamentals error: {exc}", "transient": True})
            continue
        reason = _passes_fundamentals(f, u)
        if reason:
            log.info("DROP %s: %s", sym, reason)
            rejects.append({"ticker": sym, "code": "universe", "message": reason})
            continue
        passing.append(f)
    return passing, rejects


def _cache_fresh(built: str | None, refresh_days: int) -> bool:
    if not built:
        return False
    try:
        built_date = datetime.strptime(built, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (date.today() - built_date).days < refresh_days
