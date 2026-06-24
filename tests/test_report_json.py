"""write_json + build_index unit tests (offline)."""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import report as report_mod
import build_index as index_mod


class _Regime:
    light = "GREEN"
    signals = {"spy_below_200dma": False, "breadth_below_floor": False,
               "vix_high_and_spy_falling": False}
    tripped: list[str] = []


_CONFIG = {
    "dte": {"target": 35, "min": 30, "max": 45},
    "delta": {"target": 0.20, "min": 0.15, "max": 0.30},
    "scoring": {"mode": "blended"},
    "regime": {"breadth_floor": 0.40, "vix_high": 30.0, "spy_falling_lookback": 5},
    "account": {"total_capital": 50000},
    "quality": {"avoid_earnings_before_expiry": True},
}


def _header(pct=0.0):
    return {"regime_light": "GREEN", "regime_tripped": [], "total_capital": 50000,
            "deployed": 0, "pct_deployed": pct, "remaining_cash": 50000,
            "positions_source": "greenfield (no positions.yaml)"}


def test_write_json_sanitizes_infinity(tmp_path):
    rows = [{"ticker": "AAA", "score": 2.27, "min_account_for_1_contract": math.inf,
             "annualized_yield": 0.18}]
    out = report_mod.write_json(
        _header(), rows, _Regime(), _CONFIG, tmp_path / "run.json",
        meta_extra={"data_source": "yfinance"},
        generated_at=datetime(2026, 6, 21, 21, 5, tzinfo=timezone.utc),
    )
    # Raw text must be valid JSON (no Infinity literal the browser rejects).
    text = out.read_text()
    assert "Infinity" not in text
    doc = json.loads(text)
    assert doc["rows"][0]["min_account_for_1_contract"] is None
    assert doc["meta"]["run_date"] == "2026-06-21"
    assert doc["meta"]["data_source"] == "yfinance"
    assert doc["thresholds"]["scoring_mode"] == "blended"


def test_build_index_summarizes_runs(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    for date_str, light, scores, pct in [
        ("2026-06-19", "GREEN", [1.0, 2.27], 0.0),
        ("2026-06-20", "YELLOW", [1.9], 0.1),
    ]:
        rows = [{"score": s} for s in scores]
        report_mod.write_json(
            _header(pct), rows, _Regime(), {**_CONFIG}, runs / f"{date_str}.json",
            generated_at=datetime.fromisoformat(f"{date_str}T21:05:00+00:00"),
        )
        # overwrite regime light per case
        doc = json.loads((runs / f"{date_str}.json").read_text())
        doc["regime"]["light"] = light
        (runs / f"{date_str}.json").write_text(json.dumps(doc))

    index = index_mod.build_index(tmp_path)
    assert index["latest"] == "2026-06-20"
    assert [r["date"] for r in index["runs"]] == ["2026-06-19", "2026-06-20"]
    assert index["runs"][0]["top_score"] == 2.27
    assert index["runs"][1]["light"] == "YELLOW"
    assert (tmp_path / "latest.json").exists()
    latest = json.loads((tmp_path / "latest.json").read_text())
    assert latest["meta"]["run_date"] == "2026-06-20"
