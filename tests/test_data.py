import pytest

import data
from cache import DiskCache


def test_normalize_yf_option_maps_fields_and_omits_greeks():
    # yfinance puts-row shape (DataFrame.to_dict('records') entry).
    raw = {"contractSymbol": "XYZ250718P00095000", "strike": 95.0, "bid": 0.95,
           "ask": 1.05, "impliedVolatility": 0.24, "openInterest": 2500, "volume": 800}
    opt = data.normalize_yf_option(raw, "2099-07-18")
    assert opt["symbol"] == "XYZ250718P00095000"
    assert opt["option_type"] == "put"
    assert opt["strike"] == 95.0
    assert opt["mid"] == 1.0
    assert opt["iv"] == 0.24
    assert opt["delta"] is None  # no Greeks feed; BS fallback fills delta downstream
    assert opt["open_interest"] == 2500
    assert opt["volume"] == 800
    assert opt["dte"] > 0


def test_normalize_yf_fundamentals_has_options_tristate():
    info = {"marketCap": 3e12, "averageVolume": 5e7, "netIncomeToCommon": 1e11,
            "freeCashflow": 9e10, "sector": "Technology", "currentPrice": 200.0}
    # No optionsTimestamp -> unknown, never a fabricated True/False.
    assert data.normalize_yf_fundamentals("MEGA", info)["has_options"] is None
    assert data.normalize_yf_fundamentals(
        "MEGA", {**info, "optionsTimestamp": 1750000000})["has_options"] is True


def test_normalize_yf_fundamentals_price_fallback():
    info = {"regularMarketPrice": 101.5}
    assert data.normalize_yf_fundamentals("X", info)["price"] == 101.5


def test_with_backoff_retries_then_succeeds():
    calls = {"n": 0}
    slept = []

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 rate limit")
        return "ok"

    out = data.with_backoff(flaky, max_retries=4, sleeper=slept.append,
                            is_transient=data._is_rate_limited)
    assert out == "ok"
    assert calls["n"] == 3
    assert slept == [2.0, 4.0]  # 2^1, 2^2


def test_with_backoff_gives_up_after_max():
    def always_fail():
        raise RuntimeError("429")

    with pytest.raises(RuntimeError):
        data.with_backoff(always_fail, max_retries=2, sleeper=lambda s: None,
                          is_transient=data._is_rate_limited)


def test_earnings_from_calendar_shapes():
    from datetime import date

    today = date(2026, 7, 3)
    # Current yfinance: dict with a list of dates; earliest FUTURE one wins.
    cal = {"Earnings Date": [date(2026, 7, 28), date(2026, 10, 27)]}
    assert data._earnings_from_calendar(cal, today) == "2026-07-28"
    # Strings and single (non-list) values are tolerated.
    assert data._earnings_from_calendar({"Earnings Date": "2026-08-05"}, today) == "2026-08-05"
    # Past-only, missing key, and empty shapes degrade to None.
    assert data._earnings_from_calendar({"Earnings Date": [date(2026, 1, 2)]}, today) is None
    assert data._earnings_from_calendar({}, today) is None
    assert data._earnings_from_calendar(None, today) is None
    assert data._earnings_from_calendar({"Earnings Date": ["garbage"]}, today) is None


def test_earnings_from_dates_picks_next_future():
    from datetime import date, datetime

    today = date(2026, 7, 3)
    idx = [datetime(2026, 4, 22), datetime(2026, 7, 21), datetime(2026, 10, 20)]
    assert data._earnings_from_dates(idx, today) == "2026-07-21"
    assert data._earnings_from_dates([datetime(2026, 1, 1)], today) is None
    assert data._earnings_from_dates([], today) is None


class _FakeTicker:
    """yfinance.Ticker stub: calendar fails, get_earnings_dates answers."""

    def __init__(self, symbol):
        self.symbol = symbol

    @property
    def calendar(self):
        raise RuntimeError("calendar endpoint down")

    def get_earnings_dates(self, limit=8):
        from datetime import datetime

        class _DF:
            empty = False
            index = [datetime(2099, 8, 4)]

        return _DF()


def test_get_next_earnings_falls_back_to_earnings_dates(tmp_path, monkeypatch):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_FakeTicker))
    provider = data.DataProvider({"data": {"cache_dir": str(tmp_path), "max_retries": 0}}, None)
    assert provider.get_next_earnings("MEGA") == "2099-08-04"
    # The winning source is recorded alongside the cached date.
    assert provider.cache.get("earnings", "MEGA") == {"date": "2099-08-04",
                                                      "source": "earnings_dates"}


def test_get_next_earnings_unavailable_degrades_to_none(tmp_path, monkeypatch):
    import sys
    import types

    class _DeadTicker(_FakeTicker):
        def get_earnings_dates(self, limit=8):
            raise RuntimeError("also down")

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=_DeadTicker))
    provider = data.DataProvider({"data": {"cache_dir": str(tmp_path), "max_retries": 0}}, None)
    assert provider.get_next_earnings("MEGA") is None
    assert provider.cache.get("earnings", "MEGA") == {"date": None, "source": "unavailable"}


def test_cache_roundtrip_and_date_keying(tmp_path):
    c = DiskCache(tmp_path)
    c.set("ns", "key", {"a": 1}, stamp="2026-01-01")
    assert c.get("ns", "key", stamp="2026-01-01") == {"a": 1}
    # Different stamp (day) is a miss.
    assert c.get("ns", "key", stamp="2026-01-02") is None
