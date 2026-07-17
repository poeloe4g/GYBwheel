"""Golden-path / RED short-circuit integration (F12), fully offline."""
import argparse
import json
from pathlib import Path

from cache import DiskCache
import main as main_mod
import report


class FakeProvider:
    """Stand-in DataProvider with deterministic, network-free data."""

    def __init__(self, config, secrets, cache=None, *, falling=False):
        self.config = config
        self.cache = cache or DiskCache(".cache_test")
        self.falling = falling
        self._funds = {
            "MEGA": {"ticker": "MEGA", "market_cap": 5e11, "avg_volume": 5e6,
                     "net_income": 3e10, "free_cash_flow": 2.5e10,
                     "sector": "Technology", "has_options": True, "price": 100.0},
            "BAN": {"ticker": "BAN", "market_cap": 5e11, "avg_volume": 5e6,
                    "net_income": 3e10, "free_cash_flow": 2.5e10,
                    "sector": "Energy", "has_options": True, "price": 50.0},
            # WIDE fails the spread gate; NOIV has no IV from the feed (soft flag).
            "WIDE": {"ticker": "WIDE", "market_cap": 5e11, "avg_volume": 5e6,
                     "net_income": 3e10, "free_cash_flow": 2.5e10,
                     "sector": "Financials", "has_options": True, "price": 100.0},
            "NOIV": {"ticker": "NOIV", "market_cap": 5e11, "avg_volume": 5e6,
                     "net_income": 3e10, "free_cash_flow": 2.5e10,
                     "sector": "Healthcare", "has_options": True, "price": 100.0},
            # NOEARN passes every gate but the earnings date is unknown.
            "NOEARN": {"ticker": "NOEARN", "market_cap": 5e11, "avg_volume": 5e6,
                       "net_income": 3e10, "free_cash_flow": 2.5e10,
                       "sector": "Industrials", "has_options": True, "price": 100.0},
            # RESCUE's delta-nearest contract fails OI; an adjacent one passes.
            "RESCUE": {"ticker": "RESCUE", "market_cap": 5e11, "avg_volume": 5e6,
                       "net_income": 3e10, "free_cash_flow": 2.5e10,
                       "sector": "Utilities", "has_options": True, "price": 100.0},
            # PRICEY passes every gate but one contract needs $80k collateral.
            "PRICEY": {"ticker": "PRICEY", "market_cap": 5e11, "avg_volume": 5e6,
                       "net_income": 3e10, "free_cash_flow": 2.5e10,
                       "sector": "Technology", "has_options": True, "price": 1000.0},
        }

    def get_price_history(self, ticker, period="1y"):
        if self.falling:
            closes = [200.0 - i * 0.1 for i in range(260)]   # steadily declining
        else:
            closes = [100.0 + i * 0.1 for i in range(260)]   # steadily rising
        return [{"date": f"2026-{(i % 12) + 1:02d}-01", "close": c} for i, c in enumerate(closes)]

    def get_vix(self):
        return 35.0 if self.falling else 14.0

    def get_breadth(self, members=None):
        return 0.30 if self.falling else 0.60

    def get_fundamentals(self, ticker):
        return self._funds[ticker]

    def _chain(self, ticker):
        put = {"option_type": "put", "strike": 18.0, "bid": 0.31, "ask": 0.33,
               "mid": 0.32, "delta": -0.20, "iv": 0.24,
               "open_interest": 2500, "volume": 800, "expiration": "2099-07-18",
               "dte": 35}
        if ticker == "WIDE":  # $0.90-wide market on a $0.55 mid -> spread reject
            put.update(bid=0.10, ask=1.00, mid=0.55)
        elif ticker == "NOIV":  # feed supplies no IV -> iv_missing flag
            put.update(iv=None)
        elif ticker == "RESCUE":
            # The delta-nearest strike is illiquid, but an adjacent in-band
            # strike passes every gate — filter-then-select must rescue it.
            return [
                {**put, "open_interest": 5},
                {**put, "strike": 17.0, "bid": 0.20, "ask": 0.22, "mid": 0.21,
                 "delta": -0.16, "open_interest": 3000},
            ]
        elif ticker == "PRICEY":  # clean gates, $80k collateral per contract;
            # rich premium so its blended score beats MEGA's and the
            # prefer_affordable re-ordering is actually exercised.
            put.update(strike=800.0, bid=88.0, ask=92.0, mid=90.0)
        return [put]

    def get_put_candidate(self, ticker, spot, *, quality, next_earnings=None, **kw):
        import screen
        result = screen.evaluate_puts(
            self._chain(ticker), spot,
            target_delta=kw["target_delta"], delta_min=kw["delta_min"],
            delta_max=kw["delta_max"], quality=quality, next_earnings=next_earnings,
        )
        if result["selected"] is None and result["fallback"] is None:
            result["reason"] = "no_put_in_band"
        return result

    def get_next_earnings(self, ticker):
        if ticker == "NOEARN":
            return None  # feed failure -> earnings_unknown flag
        return "2099-12-31"  # far away — never blocks


def _args(tmp_path, **over):
    base = dict(config="config.yaml", positions=str(tmp_path / "none.yaml"),
                selections=str(tmp_path / "none.json"),
                output=str(tmp_path / "out.csv"), json_out=None, tickers="MEGA",
                sp500_file=None, max_rows=25, paper=True, verbose=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_red_regime_short_circuits(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c"), falling=True))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, json_out=str(json_out)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "RED" in out
    assert "manage" in out.lower()
    assert not (tmp_path / "out.csv").exists()  # stopped before writing
    # RED days still appear on the dashboard timeline, with no candidates.
    doc = json.loads(json_out.read_text())
    assert doc["schema_version"] == report.SCHEMA_VERSION
    assert doc["regime"]["light"] == "RED"
    assert doc["rows"] == []
    assert doc["near_misses"] == []
    assert doc["meta"]["candidate_count"] == 0
    assert doc["meta"]["rejections_by_reason"] == {}


def test_green_end_to_end_writes_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c"), falling=False))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, json_out=str(json_out)))
    out = capsys.readouterr().out
    assert rc == 0
    assert "GREEN" in out
    csv_path = tmp_path / "out.csv"
    assert csv_path.exists()
    text = csv_path.read_text()
    assert "ticker" in text  # header row
    assert "MEGA" in text

    # JSON snapshot mirrors the run for the dashboard.
    doc = json.loads(json_out.read_text())
    assert doc["schema_version"] == report.SCHEMA_VERSION
    assert doc["regime"]["light"] == "GREEN"
    assert set(doc["regime"]["signals"]) == {
        "spy_below_200dma", "breadth_below_floor", "vix_high_and_spy_falling"}
    assert doc["meta"]["candidate_count"] == len(doc["rows"]) >= 1
    row = doc["rows"][0]
    for key in ("ticker", "score", "annualized_yield", "distance_to_strike",
                "max_contracts", "sector"):
        assert key in row
    assert doc["header"]["total_capital"] == 50000
    # Snapshots are stamped with the (approximate) market session.
    assert doc["meta"]["market_session"] in ("regular", "closed")
    assert doc["meta"]["quotes_trusted"] == (doc["meta"]["market_session"] == "regular")
    assert doc["meta"]["capital_warning"] is None  # strike 18 fits the $2.5k cap


def test_capital_override_flows_through_run(tmp_path, monkeypatch, capsys):
    """A dashboard-set account.total_capital in selections.json overrides
    config.yaml for sizing, the header, and the published thresholds."""
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c"), falling=False))
    sel_path = tmp_path / "selections.json"
    sel_path.write_text(json.dumps({
        "schema_version": 2,
        "account": {"total_capital": 4000, "updated_at": "2026-07-17T14:05:00Z"},
        "selections": [], "summary": None}))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, json_out=str(json_out),
                            selections=str(sel_path)))
    assert rc == 0
    assert "Capital $4,000" in capsys.readouterr().out
    doc = json.loads(json_out.read_text())
    assert doc["header"]["total_capital"] == 4000
    assert doc["header"]["capital_source"] == "dashboard"
    assert doc["header"]["deployed_positions"] == 0.0
    assert doc["header"]["deployed_selections"] == 0.0
    assert "capital override $4,000" in doc["header"]["positions_source"]
    # Published caps (client-side verify math) also scale off the override.
    assert doc["thresholds"]["account"]["total_capital"] == 4000
    # Sizing used the override: per-name cap = 40% of $4k = $1,600 < $1,800
    # collateral, so MEGA's strike-18 put breaches the per-name cap.
    row = doc["rows"][0]
    assert row["breaches_per_name_cap"] is True
    assert row["max_contracts"] == 0


def test_market_session_from_utc():
    from datetime import datetime, timezone

    utc = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
    assert main_mod._market_session(utc(2026, 7, 3, 14, 0)) == "regular"   # Fri 10am ET
    assert main_mod._market_session(utc(2026, 7, 3, 11, 10)) == "closed"   # pre-market
    assert main_mod._market_session(utc(2026, 7, 3, 21, 0)) == "closed"    # after close
    assert main_mod._market_session(utc(2026, 7, 4, 14, 0)) == "closed"    # Saturday


def test_session_stamped_at_both_ends(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    main_mod.run(_args(tmp_path, json_out=str(json_out)))
    meta = json.loads(json_out.read_text())["meta"]
    assert meta["market_session"] in ("regular", "closed")
    assert meta["market_session_end"] in ("regular", "closed")
    # Quotes fetched over the whole run are trusted only when the run both
    # started AND finished inside regular hours.
    assert meta["quotes_trusted"] == (
        meta["market_session"] == "regular" and meta["market_session_end"] == "regular")


def test_effective_premium_bases():
    live = {"bid": 0.90, "ask": 1.10, "mid": 1.00, "quote_quality": "live"}
    assert main_mod._effective_premium(live, "conservative") == {
        "premium_used": 0.95, "premium_basis": "conservative"}
    assert main_mod._effective_premium(live, "bid") == {
        "premium_used": 0.90, "premium_basis": "bid"}
    assert main_mod._effective_premium(live, "mid") == {
        "premium_used": 1.00, "premium_basis": "mid"}
    # Indicative quote (mid degraded to last trade): basis falls back to mid.
    stale = {"bid": 0.0, "ask": 0.0, "mid": 1.05, "quote_quality": "last_price"}
    assert main_mod._effective_premium(stale, "conservative") == {
        "premium_used": 1.05, "premium_basis": "mid"}
    # No quote_quality key (fixture/legacy rows) is treated as live.
    legacy = {"bid": 0.90, "mid": 1.00}
    assert main_mod._effective_premium(legacy, "conservative")["premium_used"] == 0.95


def test_yields_use_conservative_premium(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    main_mod.run(_args(tmp_path, json_out=str(json_out)))
    row = json.loads(json_out.read_text())["rows"][0]
    # MEGA: bid .31 / mid .32 -> conservative fill .315; ROC = 0.315/18.
    assert row["premium_used"] == 0.315
    assert row["premium_basis"] == "conservative"
    assert abs(row["roc"] - 0.315 / 18.0) < 1e-9
    assert row["pop"] == 0.80  # 1 - |delta 0.20|
    assert row["score_mode"] == "risk_adjusted"


def test_near_misses_captured_with_reasons(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c"), falling=False))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="MEGA,WIDE,NOIV", json_out=str(json_out)))
    assert rc == 0

    doc = json.loads(json_out.read_text())
    assert [r["ticker"] for r in doc["rows"]] == ["MEGA"]

    near = {r["ticker"]: r for r in doc["near_misses"]}
    assert set(near) == {"WIDE", "NOIV"}
    # WIDE: hard spread rejection, fully sized/scored anyway.
    assert [e["code"] for e in near["WIDE"]["rejection_reasons"]] == ["spread"]
    assert near["WIDE"]["data_flags"] == []
    assert "score" in near["WIDE"] and "max_contracts" in near["WIDE"]
    # NOIV: no hard rejection, just the missing-IV data flag.
    assert near["NOIV"]["rejection_reasons"] == []
    assert [e["code"] for e in near["NOIV"]["data_flags"]] == ["iv_missing"]

    counts = doc["meta"]["rejections_by_reason"]
    assert counts == {"spread": 1, "iv_missing": 1}
    assert doc["meta"]["near_miss_count"] == 2
    assert doc["meta"]["candidate_count"] == 1
    # Near misses never leak into the CSV.
    csv_text = (tmp_path / "out.csv").read_text()
    assert "WIDE" not in csv_text and "NOIV" not in csv_text


def test_filter_then_select_rescues_adjacent_strike(tmp_path, monkeypatch):
    """A ticker whose delta-nearest contract fails a gate is not lost when an
    adjacent in-band strike qualifies."""
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="RESCUE", json_out=str(json_out)))
    assert rc == 0
    doc = json.loads(json_out.read_text())
    assert [r["ticker"] for r in doc["rows"]] == ["RESCUE"]
    assert doc["rows"][0]["strike"] == 17.0  # the qualifying adjacent strike
    assert doc["near_misses"] == []
    assert doc["meta"]["rejections_by_reason"] == {}
    # Per-contract counters expose what the whole band looked like.
    assert doc["meta"]["contracts_evaluated"] == 2
    assert doc["meta"]["contract_gate_failures"] == {"open_interest": 1}


def _config_with(tmp_path, section, **overrides):
    """The shipped config with one section's keys overridden."""
    import yaml

    cfg = yaml.safe_load(Path("config.yaml").read_text())
    cfg[section].update(overrides)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    return str(path)


def _config_with_policy(tmp_path, policy):
    return _config_with(tmp_path, "quality", unknown_earnings_policy=policy)


def test_affordability_annotated_and_ranked_first(tmp_path, monkeypatch):
    """Default: unaffordable clean rows stay visible but rank below affordable
    ones (prefer_affordable), with the capital warning surfaced in meta."""
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="PRICEY,MEGA", json_out=str(json_out)))
    assert rc == 0
    doc = json.loads(json_out.read_text())
    rows = {r["ticker"]: r for r in doc["rows"]}
    assert set(rows) == {"MEGA", "PRICEY"}
    assert rows["MEGA"]["affordable"] is True
    assert rows["PRICEY"]["affordable"] is False
    assert rows["PRICEY"]["breaches_per_name_cap"] is True
    # PRICEY's blended score is higher, but the affordable name ranks first.
    assert rows["PRICEY"]["score"] > rows["MEGA"]["score"]
    assert [r["ticker"] for r in doc["rows"]] == ["MEGA", "PRICEY"]
    # The 'affordable' flag reaches the CSV.
    header, *lines = (tmp_path / "out.csv").read_text().splitlines()
    assert "affordable" in header
    # B1 heuristic: the (upper-)median strike of [18, 800] breaches the cap.
    assert "small" in doc["meta"]["capital_warning"]


def test_require_affordable_demotes_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="PRICEY", json_out=str(json_out),
                            config=_config_with(tmp_path, "account", require_affordable=True)))
    assert rc == 0
    doc = json.loads(json_out.read_text())
    assert doc["rows"] == []
    near = doc["near_misses"][0]
    assert near["ticker"] == "PRICEY"
    assert [e["code"] for e in near["rejection_reasons"]] == ["unaffordable"]
    assert doc["meta"]["rejections_by_reason"] == {"unaffordable": 1}
    # All sized rows breach -> the B1 capital warning reaches the snapshot.
    assert "small" in doc["meta"]["capital_warning"]


def test_unknown_earnings_promoted_with_flag(tmp_path, monkeypatch):
    """Default policy=flag: a clean row with only earnings_unknown is a candidate."""
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="MEGA,NOEARN", json_out=str(json_out)))
    assert rc == 0

    doc = json.loads(json_out.read_text())
    assert doc["schema_version"] == report.SCHEMA_VERSION
    rows = {r["ticker"]: r for r in doc["rows"]}
    assert set(rows) == {"MEGA", "NOEARN"}
    assert rows["MEGA"]["data_flags"] == []
    assert [e["code"] for e in rows["NOEARN"]["data_flags"]] == ["earnings_unknown"]
    assert rows["NOEARN"]["spot"] == 100.0
    assert doc["near_misses"] == []
    # Promoted flags are accounted as flags, not rejections.
    assert doc["meta"]["rejections_by_reason"] == {}
    assert doc["meta"]["flags_by_reason"] == {"earnings_unknown": 1}
    assert doc["thresholds"]["unknown_earnings_policy"] == "flag"
    # The flag is visible in the CSV.
    csv_rows = (tmp_path / "out.csv").read_text().splitlines()
    assert "flags" in csv_rows[0]
    assert any("NOEARN" in ln and "earnings_unknown" in ln for ln in csv_rows[1:])


def test_unknown_earnings_policy_near_miss_demotes(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="NOEARN", json_out=str(json_out),
                            config=_config_with_policy(tmp_path, "near_miss")))
    assert rc == 0
    doc = json.loads(json_out.read_text())
    assert doc["rows"] == []
    near = doc["near_misses"][0]
    assert near["ticker"] == "NOEARN"
    assert near["rejection_reasons"] == []
    assert [e["code"] for e in near["data_flags"]] == ["earnings_unknown"]
    assert doc["meta"]["rejections_by_reason"] == {"earnings_unknown": 1}


def test_unknown_earnings_policy_reject_hard_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="NOEARN", json_out=str(json_out),
                            config=_config_with_policy(tmp_path, "reject")))
    assert rc == 0
    doc = json.loads(json_out.read_text())
    assert doc["rows"] == []
    near = doc["near_misses"][0]
    assert [e["code"] for e in near["rejection_reasons"]] == ["earnings_unknown"]
    assert near["data_flags"] == []


def test_unknown_earnings_policy_bad_value_falls_back(tmp_path, monkeypatch):
    """An invalid policy value degrades to strict near_miss, not a crash."""
    monkeypatch.setattr(main_mod, "DataProvider",
                        lambda c, s, cache=None: FakeProvider(c, s, DiskCache(tmp_path / "c")))
    json_out = tmp_path / "run.json"
    rc = main_mod.run(_args(tmp_path, tickers="NOEARN", json_out=str(json_out),
                            config=_config_with_policy(tmp_path, "bogus")))
    assert rc == 0
    doc = json.loads(json_out.read_text())
    assert doc["rows"] == []
    assert doc["near_misses"][0]["ticker"] == "NOEARN"
