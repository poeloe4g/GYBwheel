"""Golden-path / RED short-circuit integration (F12), fully offline."""
import argparse
import json

from cache import DiskCache
import main as main_mod


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

    def get_nearest_delta_put(self, ticker, spot, **kw):
        return {"option_type": "put", "strike": 18.0, "bid": 0.31, "ask": 0.33,
                "mid": 0.32, "delta": -0.20, "abs_delta": 0.20, "iv": 0.24,
                "open_interest": 2500, "volume": 800, "expiration": "2099-07-18",
                "dte": 35}

    def get_next_earnings(self, ticker):
        return "2099-12-31"  # far away — never blocks


def _args(tmp_path, **over):
    base = dict(config="config.yaml", positions=str(tmp_path / "none.yaml"),
                output=str(tmp_path / "out.csv"), json_out=None, tickers="MEGA",
                sp500_file=None, max_rows=25, paper=True, verbose=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_red_regime_short_circuits(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRADIER_TOKEN", "x")
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
    assert doc["schema_version"] == 1
    assert doc["regime"]["light"] == "RED"
    assert doc["rows"] == []
    assert doc["meta"]["candidate_count"] == 0


def test_green_end_to_end_writes_csv(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TRADIER_TOKEN", "x")
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
    assert doc["schema_version"] == 1
    assert doc["regime"]["light"] == "GREEN"
    assert set(doc["regime"]["signals"]) == {
        "spy_below_200dma", "breadth_below_floor", "vix_high_and_spy_falling"}
    assert doc["meta"]["candidate_count"] == len(doc["rows"]) >= 1
    row = doc["rows"][0]
    for key in ("ticker", "score", "annualized_yield", "distance_to_strike",
                "max_contracts", "sector"):
        assert key in row
    assert doc["header"]["total_capital"] == 50000
