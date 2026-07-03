import json
from pathlib import Path

from cache import DiskCache
from universe import build_universe

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class FakeProvider:
    def __init__(self, fundamentals, cache):
        self._f = fundamentals
        self.cache = cache

    def get_fundamentals(self, ticker):
        return self._f[ticker]


def _provider(tmp_path):
    fundamentals = json.loads((FIXTURES / "yf_fundamentals.json").read_text())
    return FakeProvider(fundamentals, DiskCache(tmp_path / "cache"))


def test_megacap_passes_others_dropped(tmp_path, config):
    provider = _provider(tmp_path)
    passing, rejects = build_universe(["MEGA", "TINY", "REDINK"], provider, config)
    tickers = {p["ticker"] for p in passing}
    assert "MEGA" in tickers       # qualifies
    assert "TINY" not in tickers   # too small market cap / volume
    assert "REDINK" not in tickers # unprofitable + negative FCF
    assert {r["ticker"] for r in rejects} == {"TINY", "REDINK"}
    assert all(r["code"] == "universe" and r["message"] for r in rejects)


def test_ban_list_drops_name(tmp_path, config):
    cfg = {**config, "universe": {**config["universe"], "ban_list": ["MEGA"]}}
    provider = _provider(tmp_path)
    passing, rejects = build_universe(["MEGA"], provider, cfg)
    assert passing == []
    assert rejects[0]["message"] == "on ban list"


def test_allow_list_restricts(tmp_path, config):
    cfg = {**config, "universe": {**config["universe"], "allow_list": ["MEGA"]}}
    provider = _provider(tmp_path)
    passing, _ = build_universe(["MEGA", "TINY"], provider, cfg)
    assert {p["ticker"] for p in passing} == {"MEGA"}


def test_universe_cache_reused(tmp_path, config):
    provider = _provider(tmp_path)
    build_universe(["MEGA"], provider, config)
    cached = provider.cache.get("universe", "passing")
    assert cached is not None
    assert cached["names"][0]["ticker"] == "MEGA"
    # Same candidate list -> served from cache, including the recorded rejects.
    passing, rejects = build_universe(["MEGA"], provider, config)
    assert passing[0]["ticker"] == "MEGA"
    assert rejects == []


def test_universe_cache_invalidated_by_candidate_change(tmp_path, config):
    provider = _provider(tmp_path)
    build_universe(["MEGA"], provider, config)
    # A different candidate list must not be served the stale cached universe.
    passing, _ = build_universe(["MEGA", "TINY"], provider, config)
    cached = provider.cache.get("universe", "passing")
    assert {p["ticker"] for p in passing} == {"MEGA"}
    assert cached["candidates_hash"] is not None


def test_has_options_tristate(tmp_path, config):
    fundamentals = json.loads((FIXTURES / "yf_fundamentals.json").read_text())
    no_opts = {**fundamentals["MEGA"], "ticker": "NOOPT", "has_options": False}
    unknown = {**fundamentals["MEGA"], "ticker": "UNK", "has_options": None}
    provider = FakeProvider(
        {**fundamentals, "NOOPT": no_opts, "UNK": unknown}, DiskCache(tmp_path / "cache")
    )
    passing, rejects = build_universe(["NOOPT", "UNK"], provider, config)
    # Definite False rejects; unknown (None) passes through.
    assert {p["ticker"] for p in passing} == {"UNK"}
    assert rejects[0]["ticker"] == "NOOPT"
    assert "options" in rejects[0]["message"]
