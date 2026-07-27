"""Blacklist — names you never want screened, whatever the numbers say.

Two sources merge into one list:
  - ``config.yaml`` ``universe.ban_list`` — structural exclusions, committed;
  - ``site/data/selections.json`` ``blacklist`` — the dashboard surface, the one
    you can actually reach from a phone. It wins on conflict.

An entry is either a bare ticker string (the historical shape, still accepted) or
a dict carrying the reason it was excluded plus an optional ``review_after`` date
that auto-releases a temporary ban:

    {"ticker": "XYZ", "reason": "burned me in March",
     "added": "2026-07-27", "review_after": "2027-01-01"}

Every parse here is total: malformed input degrades to "not blacklisted" and
warns. A blacklist that fails open costs you one unwanted candidate to eyeball;
one that fails closed silently empties your universe and the run looks merely
quiet, not broken.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

log = logging.getLogger("blacklist")


def _entry_ticker(entry: Any) -> str:
    """Ticker for a raw entry of either shape; "" when it has none."""
    if isinstance(entry, str):
        return entry.strip().upper()
    if isinstance(entry, dict):
        raw = entry.get("ticker")
        if isinstance(raw, str):
            return raw.strip().upper()
    return ""


def _parse_date(raw: Any, ticker: str, field: str) -> date | None:
    """YYYY-MM-DD or None. An unparseable date is dropped, never fatal."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError:
        log.warning("blacklist %s: ignoring unparseable %s %r", ticker, field, raw)
        return None


def normalize(
    entries: Any, today: date | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(active, expired)`` from raw blacklist entries.

    ``active`` maps TICKER -> canonical entry and is what actually excludes a
    name. ``expired`` carries entries whose ``review_after`` has passed: they no
    longer exclude anything, but they stay in the file so the dashboard can offer
    "remove" or "extend" rather than losing the note silently.
    """
    today = today or date.today()
    active: dict[str, dict[str, Any]] = {}
    expired: list[dict[str, Any]] = []

    if entries is None:
        return active, expired
    if not isinstance(entries, list):
        log.warning("blacklist is not a list (%s) — ignoring it entirely", type(entries).__name__)
        return active, expired

    for entry in entries:
        if not isinstance(entry, (str, dict)):
            log.warning("ignoring blacklist entry of unsupported type %s", type(entry).__name__)
            continue
        ticker = _entry_ticker(entry)
        if not ticker:
            log.warning("ignoring blacklist entry with no ticker: %r", entry)
            continue

        source = entry if isinstance(entry, dict) else {}
        reason = source.get("reason") or ""
        canonical = {
            "ticker": ticker,
            "reason": str(reason).strip(),
            "added": source.get("added"),
            "review_after": source.get("review_after"),
        }

        review_after = _parse_date(source.get("review_after"), ticker, "review_after")
        if review_after is None:
            canonical["review_after"] = None
        if review_after is not None and review_after <= today:
            expired.append(canonical)
            continue
        active[ticker] = canonical

    return active, expired


def from_selections(doc: dict[str, Any] | None) -> list[Any]:
    """Blacklist entries from the dashboard selections doc; [] when absent.

    Mirrors ``size._capital_override``: anything unexpected is warned about and
    ignored, because a bad dashboard write must never fail or distort a run.
    """
    if not doc:
        return []
    entries = doc.get("blacklist")
    if entries is None:
        return []
    if not isinstance(entries, list):
        log.warning("selections doc has non-list 'blacklist' — ignoring")
        return []
    return entries


def merge(config_entries: Any, dashboard_entries: Any) -> list[Any]:
    """Union config.yaml and dashboard entries; the dashboard wins on conflict.

    Returns raw (un-normalized) entries so callers keep a single source of truth
    to normalize — the snapshot echoes the same list the screener filtered on.
    """
    merged: dict[str, Any] = {}
    for source in (config_entries, dashboard_entries):
        if not isinstance(source, list):
            if source:
                log.warning("ignoring non-list blacklist source (%s)", type(source).__name__)
            continue
        for entry in source:
            ticker = _entry_ticker(entry)
            if not ticker:
                # Kept, so normalize() reports it once with a useful warning
                # rather than it vanishing between the two layers.
                merged[f"__invalid__{len(merged)}"] = entry
                continue
            merged[ticker] = entry
    return list(merged.values())


def describe(entry: dict[str, Any]) -> str:
    """Reject message for an excluded name — always says who excluded it."""
    reason = (entry.get("reason") or "").strip()
    review_after = entry.get("review_after")
    msg = f"blacklisted: {reason}" if reason else "blacklisted"
    if review_after:
        msg += f" (review after {review_after})"
    return msg
