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


def test_cache_roundtrip_and_date_keying(tmp_path):
    c = DiskCache(tmp_path)
    c.set("ns", "key", {"a": 1}, stamp="2026-01-01")
    assert c.get("ns", "key", stamp="2026-01-01") == {"a": 1}
    # Different stamp (day) is a miss.
    assert c.get("ns", "key", stamp="2026-01-02") is None
