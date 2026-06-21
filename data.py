"""Abstracted, cached data layer (F03 / B7).

This is the single source-swappable interface for the screener. Swapping a
provider (e.g. FMP for fundamentals later) should be a one-file change here.

Network libraries (`requests`, `yfinance`) are imported lazily inside the
methods that need them so the module — and the tests — import with no network
and no optional deps installed.
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


def normalize_option(raw: dict[str, Any], expiration: str) -> dict[str, Any]:
    """Normalize a Tradier option entry into the screener's option shape."""
    greeks = raw.get("greeks") or {}
    bid = _f(raw.get("bid"))
    ask = _f(raw.get("ask"))
    mid = (bid + ask) / 2 if (bid is not None and ask is not None) else None
    return {
        "symbol": raw.get("symbol"),
        "option_type": raw.get("option_type"),
        "strike": _f(raw.get("strike")),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": _f(greeks.get("delta")),
        "iv": _f(greeks.get("mid_iv") or greeks.get("smv_vol")),
        "open_interest": _i(raw.get("open_interest")),
        "volume": _i(raw.get("volume")),
        "expiration": expiration,
        "dte": dte_for(expiration),
    }


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

    # --- Tradier (option chains) -------------------------------------------
    def _tradier_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        import requests

        url = f"{self.secrets.tradier_base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.secrets.tradier_token}",
            "Accept": "application/json",
        }

        def call() -> dict[str, Any]:
            resp = requests.get(url, params=params, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()

        return with_backoff(call, max_retries=self.max_retries, is_transient=_is_rate_limited)

    def get_expirations(self, ticker: str) -> list[str]:
        cached = self.cache.get("expirations", ticker)
        if cached is not None:
            return cached
        payload = self._tradier_get(
            "/markets/options/expirations", {"symbol": ticker, "includeAllRoots": "true"}
        )
        dates = ((payload or {}).get("expirations") or {}).get("date") or []
        if isinstance(dates, str):
            dates = [dates]
        self.cache.set("expirations", ticker, dates)
        return dates

    def get_option_chain(self, ticker: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        key = f"{ticker}:{expiration}"
        cached = self.cache.get("chain", key)
        if cached is not None:
            return cached
        payload = self._tradier_get(
            "/markets/options/chains",
            {"symbol": ticker, "expiration": expiration, "greeks": "true" if greeks else "false"},
        )
        raw = ((payload or {}).get("options") or {}).get("option") or []
        if isinstance(raw, dict):
            raw = [raw]
        chain = [normalize_option(o, expiration) for o in raw]
        self.cache.set("chain", key, chain)
        return chain

    def get_nearest_delta_put(
        self, ticker: str, spot: float, *, dte_min: int, dte_max: int,
        target_delta: float, delta_min: float, delta_max: float,
    ) -> dict[str, Any] | None:
        """Fetch candidate expiries and delegate selection to screen.select_put."""
        from screen import select_nearest_delta_put

        expirations = [e for e in self.get_expirations(ticker) if dte_min <= dte_for(e) <= dte_max]
        chains = [self.get_option_chain(ticker, e, greeks=True) for e in expirations]
        flat = [opt for chain in chains for opt in chain]
        return select_nearest_delta_put(
            flat, spot,
            target_delta=target_delta, delta_min=delta_min, delta_max=delta_max,
            risk_free_rate=self.config.get("quality", {}).get("risk_free_rate", 0.04),
        )

    # --- yfinance (fundamentals / prices / earnings / breadth / VIX) -------
    def get_fundamentals(self, ticker: str) -> dict[str, Any]:
        cached = self.cache.get("fundamentals", ticker)
        if cached is not None:
            return cached

        import yfinance as yf

        def call() -> dict[str, Any]:
            info = yf.Ticker(ticker).info
            return {
                "ticker": ticker,
                "market_cap": info.get("marketCap"),
                "avg_volume": info.get("averageVolume"),
                "net_income": info.get("netIncomeToCommon"),
                "free_cash_flow": info.get("freeCashflow"),
                "sector": info.get("sector"),
                "has_options": bool(info.get("optionsTimestamp") or True),
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
            }

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

        def call() -> str | None:
            df = yf.Ticker(ticker).get_earnings_dates(limit=8)
            if df is None or df.empty:
                return None
            today = date.today()
            future = [idx.date() for idx in df.index if idx.date() >= today]
            return min(future).isoformat() if future else None

        try:
            nxt = with_backoff(call, max_retries=self.max_retries, is_transient=_is_rate_limited)
        except Exception:  # noqa: BLE001 — earnings missing must degrade gracefully (B2)
            nxt = None
        self.cache.set("earnings", ticker, {"date": nxt})
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
