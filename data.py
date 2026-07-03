"""Abstracted, cached data layer (F03 / B7).

This is the single source-swappable interface for the screener. Swapping a
provider (e.g. FMP for fundamentals later) should be a one-file change here.

The network library (`yfinance`) is imported lazily inside the methods that
need it so the module — and the tests — import with no network and no optional
deps installed.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from typing import Any, Callable

from cache import DiskCache


class DataError(RuntimeError):
    pass


def with_backoff(
    fn: Callable[[], Any],
    *,
    max_retries: int = 4,
    base: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
    is_transient: Callable[[Exception], bool] | None = None,
) -> Any:
    """Call ``fn`` retrying transient failures with 2s/4s/8s/16s backoff."""
    is_transient = is_transient or (lambda exc: True)
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — provider exceptions vary
            attempt += 1
            if attempt > max_retries or not is_transient(exc):
                raise
            sleeper(base ** attempt)


def _is_rate_limited(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in (429, 500, 502, 503, 504) or "429" in text or "rate" in text or "timeout" in text


def normalize_yf_option(raw: dict[str, Any], expiration: str) -> dict[str, Any]:
    """Normalize a yfinance option row into the screener's option shape.

    yfinance has no Greeks, so ``delta`` is left None and computed downstream by
    the Black-Scholes fallback in ``screen._effective_abs_delta`` from iv/spot/
    strike/dte. Accepts a plain dict (e.g. ``DataFrame.to_dict('records')`` row).
    """
    bid = _f(raw.get("bid"))
    ask = _f(raw.get("ask"))
    mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None
    return {
        "symbol": raw.get("contractSymbol"),
        "option_type": "put",
        "strike": _f(raw.get("strike")),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": None,
        "iv": _f(raw.get("impliedVolatility")),
        "open_interest": _i(raw.get("openInterest")),
        "volume": _i(raw.get("volume")),
        "expiration": expiration,
        "dte": dte_for(expiration),
    }


def normalize_yf_fundamentals(ticker: str, info: dict[str, Any]) -> dict[str, Any]:
    """Normalize a yfinance ``info`` dict into the screener's fundamentals shape.

    ``has_options`` is tri-state: True when Yahoo reports an options timestamp,
    None (unknown) when it doesn't — yfinance's ``info`` is too flaky to assert
    a hard False from a missing key.
    """
    return {
        "ticker": ticker,
        "market_cap": info.get("marketCap"),
        "avg_volume": info.get("averageVolume"),
        "net_income": info.get("netIncomeToCommon"),
        "free_cash_flow": info.get("freeCashflow"),
        "sector": info.get("sector"),
        "has_options": True if info.get("optionsTimestamp") is not None else None,
        "price": info.get("currentPrice") or info.get("regularMarketPrice"),
    }


def _coerce_date(v: Any) -> date | None:
    """Best-effort conversion of yfinance date shapes (Timestamp/datetime/date/str)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "date"):  # pandas.Timestamp without importing pandas
        try:
            return v.date()
        except (TypeError, ValueError):
            return None
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _next_future_iso(dates: list[date | None], today: date) -> str | None:
    future = [d for d in dates if d is not None and d >= today]
    return min(future).isoformat() if future else None


def _earnings_from_calendar(cal: Any, today: date) -> str | None:
    """Next earnings date from ``yf.Ticker.calendar``.

    Current yfinance returns a dict whose "Earnings Date" is a list of dates;
    older versions returned a DataFrame with an "Earnings Date" row. Both are
    handled; anything unparseable degrades to None.
    """
    if cal is None:
        return None
    raw: Any = None
    if isinstance(cal, dict):
        raw = cal.get("Earnings Date")
    else:  # legacy DataFrame shape
        try:
            raw = list(cal.loc["Earnings Date"])
        except Exception:  # noqa: BLE001 — shape varies across yfinance versions
            return None
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    return _next_future_iso([_coerce_date(v) for v in raw], today)


def _earnings_from_dates(index_dates: list[Any], today: date) -> str | None:
    """Next earnings date from a ``get_earnings_dates`` DataFrame index."""
    return _next_future_iso([_coerce_date(v) for v in index_dates], today)


def dte_for(expiration: str, today: date | None = None) -> int:
    today = today or date.today()
    exp = datetime.strptime(expiration, "%Y-%m-%d").date()
    return (exp - today).days


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        return int(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


class DataProvider:
    """Cached access to fundamentals, prices, chains, earnings, breadth, VIX."""

    def __init__(self, config: dict[str, Any], secrets: Any, cache: DiskCache | None = None) -> None:
        self.config = config
        self.secrets = secrets
        data_cfg = config.get("data", {})
        self.cache = cache or DiskCache(data_cfg.get("cache_dir", ".cache"))
        self.max_retries = int(data_cfg.get("max_retries", 4))

    # --- yfinance (option chains) ------------------------------------------
    def get_expirations(self, ticker: str) -> list[str]:
        cached = self.cache.get("expirations", ticker)
        if cached is not None:
            return cached

        import yfinance as yf

        def call() -> list[str]:
            return list(yf.Ticker(ticker).options or [])

        dates = with_backoff(call, max_retries=self.max_retries, is_transient=_is_rate_limited)
        self.cache.set("expirations", ticker, dates)
        return dates

    def get_option_chain(self, ticker: str, expiration: str) -> list[dict[str, Any]]:
        key = f"{ticker}:{expiration}"
        cached = self.cache.get("chain", key)
        if cached is not None:
            return cached

        import yfinance as yf

        def call() -> list[dict[str, Any]]:
            puts = yf.Ticker(ticker).option_chain(expiration).puts
            return puts.to_dict("records")

        rows = with_backoff(call, max_retries=self.max_retries, is_transient=_is_rate_limited)
        chain = [normalize_yf_option(o, expiration) for o in rows]
        self.cache.set("chain", key, chain)
        return chain

    def get_put_candidate(
        self, ticker: str, spot: float, *, dte_min: int, dte_max: int,
        target_delta: float, delta_min: float, delta_max: float,
        quality: dict[str, Any], next_earnings: str | None = None,
    ) -> dict[str, Any]:
        """Fetch in-window expiries and gate-then-select via ``screen.evaluate_puts``.

        The two empty cases are distinguished with a ``reason`` so the pipeline
        can count them separately: ``no_expiry_in_window`` (nothing listed in
        the DTE window at all) vs ``no_put_in_band`` (chains fetched but no put
        lands in the delta band).
        """
        from screen import evaluate_puts

        expirations = [e for e in self.get_expirations(ticker) if dte_min <= dte_for(e) <= dte_max]
        if not expirations:
            return {"selected": None, "fallback": None, "n_in_band": 0,
                    "n_qualifying": 0, "gate_failures": {},
                    "reason": "no_expiry_in_window"}
        chains = [self.get_option_chain(ticker, e) for e in expirations]
        flat = [opt for chain in chains for opt in chain]
        result = evaluate_puts(
            flat, spot,
            target_delta=target_delta, delta_min=delta_min, delta_max=delta_max,
            quality=quality,
            risk_free_rate=self.config.get("quality", {}).get("risk_free_rate", 0.04),
            next_earnings=next_earnings,
        )
        if result["selected"] is None and result["fallback"] is None:
            result["reason"] = "no_put_in_band"
        return result

    # --- yfinance (fundamentals / prices / earnings / breadth / VIX) -------
    def get_fundamentals(self, ticker: str) -> dict[str, Any]:
        cached = self.cache.get("fundamentals", ticker)
        if cached is not None:
            return cached

        import yfinance as yf

        def call() -> dict[str, Any]:
            return normalize_yf_fundamentals(ticker, yf.Ticker(ticker).info)

        fundamentals = with_backoff(call, max_retries=self.max_retries, is_transient=_is_rate_limited)
        self.cache.set("fundamentals", ticker, fundamentals)
        return fundamentals

    def get_price_history(self, ticker: str, period: str = "1y") -> list[dict[str, Any]]:
        cached = self.cache.get("history", f"{ticker}:{period}")
        if cached is not None:
            return cached

        import yfinance as yf

        def call() -> list[dict[str, Any]]:
            hist = yf.Ticker(ticker).history(period=period)
            return [
                {"date": idx.date().isoformat(), "close": float(row["Close"])}
                for idx, row in hist.iterrows()
            ]

        history = with_backoff(call, max_retries=self.max_retries, is_transient=_is_rate_limited)
        self.cache.set("history", f"{ticker}:{period}", history)
        return history

    def get_next_earnings(self, ticker: str) -> str | None:
        cached = self.cache.get("earnings", ticker)
        if cached is not None:
            return cached.get("date")

        import yfinance as yf

        today = date.today()

        def from_calendar() -> str | None:
            return _earnings_from_calendar(yf.Ticker(ticker).calendar, today)

        def from_dates() -> str | None:
            df = yf.Ticker(ticker).get_earnings_dates(limit=8)
            if df is None or getattr(df, "empty", True):
                return None
            return _earnings_from_dates(list(df.index), today)

        # Two independent sources: calendar (lighter call) first, the earnings
        # history second. Either failing must degrade gracefully (B2); the
        # cached source records which feed answered, "unavailable" when neither.
        nxt, source = None, "unavailable"
        for name, fn in (("calendar", from_calendar), ("earnings_dates", from_dates)):
            try:
                nxt = with_backoff(fn, max_retries=self.max_retries, is_transient=_is_rate_limited)
            except Exception:  # noqa: BLE001
                nxt = None
            if nxt:
                source = name
                break
        self.cache.set("earnings", ticker, {"date": nxt, "source": source})
        return nxt

    def get_breadth(self, members: list[str] | None = None) -> float | None:
        """% of S&P 500 above their 50-DMA. Heavy; cached daily (B7)."""
        cached = self.cache.get("breadth", "sp500")
        if cached is not None:
            return cached.get("value")
        if not members:
            return None
        above = 0
        counted = 0
        for sym in members:
            hist = self.get_price_history(sym, period="3mo")
            closes = [h["close"] for h in hist][-50:]
            if len(closes) < 50:
                continue
            counted += 1
            if closes[-1] > sum(closes) / len(closes):
                above += 1
        value = (above / counted) if counted else None
        self.cache.set("breadth", "sp500", {"value": value})
        return value

    def get_vix(self) -> float | None:
        cached = self.cache.get("vix", "^VIX")
        if cached is not None:
            return cached.get("value")
        hist = self.get_price_history("^VIX", period="5d")
        value = hist[-1]["close"] if hist else None
        self.cache.set("vix", "^VIX", {"value": value})
        return value
