import pytest

import data
from cache import DiskCache


def test_normalize_option_computes_mid_and_dte():
    raw = {"symbol": "X", "option_type": "put", "strike": 95.0, "bid": 0.95, "ask": 1.05,
           "open_interest": 2500, "volume": 800, "greeks": {"delta": -0.20, "mid_iv": 0.24}}
    opt = data.normalize_option(raw, "2099-07-18")
    assert opt["mid"] == 1.0
    assert opt["delta"] == -0.20
    assert opt["iv"] == 0.24
    assert opt["dte"] > 0


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
