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


def test_normalize_zeroed_bid_falls_back_to_last_price():
    # Off-hours yfinance rows often zero the bid; mid must come from the last
    # trade, marked indicative — not (0 + ask)/2.
    raw = {"strike": 52.0, "bid": 0.0, "ask": 2.25, "lastPrice": 1.10,
           "lastTradeDate": "2026-07-02 15:59:00", "impliedVolatility": 0.30}
    opt = data.normalize_yf_option(raw, "2099-07-18")
    assert opt["mid"] == 1.10
    assert opt["quote_quality"] == "last_price"
    assert opt["last_price"] == 1.10
    assert opt["last_trade_date"] == "2026-07-02 15:59:00"


def test_normalize_crossed_quote_falls_back_to_last_price():
    raw = {"strike": 52.0, "bid": 1.50, "ask": 1.00, "lastPrice": 1.20}
    opt = data.normalize_yf_option(raw, "2099-07-18")
    assert opt["mid"] == 1.20
    assert opt["quote_quality"] == "last_price"


def test_normalize_dead_quote_has_no_mid():
    raw = {"strike": 52.0, "bid": 0.0, "ask": 0.0, "lastPrice": 0.0}
    opt = data.normalize_yf_option(raw, "2099-07-18")
    assert opt["mid"] is None
    assert opt["quote_quality"] == "none"  # rejected downstream as no_premium


def test_normalize_live_quote_marked_live():
    raw = {"strike": 95.0, "bid": 0.95, "ask": 1.05, "lastPrice": 0.99}
    opt = data.normalize_yf_option(raw, "2099-07-18")
    assert opt["mid"] == 1.0
    assert opt["quote_quality"] == "live"


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


def test_get_put_candidate_dte_stretch(tmp_path):
    from datetime import date, timedelta

    exp = (date.today() + timedelta(days=50)).isoformat()  # outside 30-45

    class _Provider(data.DataProvider):
        def get_expirations(self, ticker):
            return [exp]

        def get_option_chain(self, ticker, expiration):
            return [{"option_type": "put", "strike": 92.0, "bid": 1.0, "ask": 1.1,
                     "mid": 1.05, "delta": -0.20, "iv": 0.24, "open_interest": 500,
                     "expiration": expiration, "dte": data.dte_for(expiration)}]

    quality = {"min_yield_30dte": 0.005, "max_implied_move": 0.15,
               "max_spread_pct": 0.15, "max_spread_abs": 0.10,
               "min_open_interest": 50, "min_distance_to_strike": 0.03,
               "avoid_earnings_before_expiry": True, "iv_outlier_mult": 2.5}
    p = _Provider({"data": {"cache_dir": str(tmp_path)}}, None)
    base = dict(dte_min=30, dte_max=45, target_delta=0.20, delta_min=0.15,
                delta_max=0.30, quality=quality, next_earnings="2099-12-31")

    # Default: the empty window is reported, never silently widened.
    assert p.get_put_candidate("X", 110.0, **base)["reason"] == "no_expiry_in_window"

    # Opt-in stretch finds the contract but marks it visibly.
    res = p.get_put_candidate("X", 110.0, **base, dte_stretch_max=56)
    assert res["selected"] is not None
    assert any(e["code"] == "dte_stretched" for e in res["selected"]["flags"])

    # A stretch cap short of the expiry still reports the empty window.
    assert p.get_put_candidate("X", 110.0, **base,
                               dte_stretch_max=48)["reason"] == "no_expiry_in_window"


def test_cache_roundtrip_and_date_keying(tmp_path):
    c = DiskCache(tmp_path)
    c.set("ns", "key", {"a": 1}, stamp="2026-01-01")
    assert c.get("ns", "key", stamp="2026-01-01") == {"a": 1}
    # Different stamp (day) is a miss.
    assert c.get("ns", "key", stamp="2026-01-02") is None


def test_normalize_yf_option_call_type():
    raw = {"contractSymbol": "XYZ250718C00115000", "strike": 115.0,
           "bid": 1.15, "ask": 1.25, "impliedVolatility": 0.22, "openInterest": 800}
    opt = data.normalize_yf_option(raw, "2099-07-18", "call")
    assert opt["option_type"] == "call"
    assert opt["mid"] == 1.20


def test_normalize_yf_fundamentals_dividend_yield_passthrough():
    info = {"marketCap": 3e12, "dividendYield": 0.0055}
    assert data.normalize_yf_fundamentals("MEGA", info)["dividend_yield"] == 0.0055
    assert data.normalize_yf_fundamentals("MEGA", {})["dividend_yield"] is None


def test_get_option_chain_keeps_both_sides(tmp_path, monkeypatch):
    import sys
    import types

    class _DF:
        def __init__(self, records):
            self._records = records

        def to_dict(self, orient):
            assert orient == "records"
            return self._records

    class _ChainTicker:
        calls_made = 0

        def __init__(self, symbol):
            self.symbol = symbol

        def option_chain(self, expiration):
            _ChainTicker.calls_made += 1
            return types.SimpleNamespace(
                puts=_DF([{"contractSymbol": "P", "strike": 95.0, "bid": 0.95,
                           "ask": 1.05}]),
                calls=_DF([{"contractSymbol": "C", "strike": 115.0, "bid": 1.15,
                            "ask": 1.25}]),
            )

    monkeypatch.setitem(sys.modules, "yfinance",
                        types.SimpleNamespace(Ticker=_ChainTicker))
    provider = data.DataProvider({"data": {"cache_dir": str(tmp_path),
                                           "max_retries": 0}}, None)
    chain = provider.get_option_chain("XYZ", "2099-07-18")
    assert [o["option_type"] for o in chain] == ["put", "call"]
    # One HTTP fetch served both sides; namespace chain2 (old puts-only
    # "chain" records must never be read); second call is a cache hit.
    assert _ChainTicker.calls_made == 1
    assert provider.cache.get("chain2", "XYZ:2099-07-18") == chain
    assert provider.cache.get("chain", "XYZ:2099-07-18") is None
    assert provider.get_option_chain("XYZ", "2099-07-18") == chain
    assert _ChainTicker.calls_made == 1


def test_get_put_candidate_attaches_call_side(tmp_path):
    from datetime import date, timedelta

    exp = (date.today() + timedelta(days=35)).isoformat()

    class _Provider(data.DataProvider):
        def get_expirations(self, ticker):
            return [exp]

        def get_option_chain(self, ticker, expiration):
            base = {"expiration": expiration, "dte": data.dte_for(expiration),
                    "iv": 0.24, "volume": 100}
            return [
                {**base, "option_type": "put", "strike": 92.0, "bid": 1.0,
                 "ask": 1.1, "mid": 1.05, "delta": -0.20, "open_interest": 500},
                {**base, "option_type": "call", "strike": 115.0, "bid": 1.15,
                 "ask": 1.25, "mid": 1.20, "delta": 0.26, "open_interest": 3},
            ]

    quality = {"min_yield_30dte": 0.005, "max_implied_move": 0.15,
               "max_spread_pct": 0.15, "max_spread_abs": 0.10,
               "min_open_interest": 50, "min_distance_to_strike": 0.03,
               "avoid_earnings_before_expiry": True, "iv_outlier_mult": 2.5}
    call_side = {"enabled": True, "target_delta": 0.25, "min_open_interest": 10,
                 "max_spread_pct": 0.25, "max_spread_abs": 0.15}
    p = _Provider({"data": {"cache_dir": str(tmp_path)}, "quality": quality,
                   "call_side": call_side}, None)
    res = p.get_put_candidate("X", 110.0, dte_min=30, dte_max=45,
                              target_delta=0.20, delta_min=0.15, delta_max=0.30,
                              quality=quality, next_earnings="2099-12-31")
    sel = res["selected"]
    assert sel["call_oi"] == 3
    assert sel["thin_call_side"] is True
    assert any(e["code"] == "thin_call_side" for e in sel["flags"])
    assert sel["call_yield_ann"] is not None

    # Feature off: rows simply lack the call fields.
    p2 = _Provider({"data": {"cache_dir": str(tmp_path / "off")},
                    "quality": quality}, None)
    res2 = p2.get_put_candidate("X", 110.0, dte_min=30, dte_max=45,
                                target_delta=0.20, delta_min=0.15,
                                delta_max=0.30, quality=quality,
                                next_earnings="2099-12-31")
    assert "thin_call_side" not in res2["selected"]
