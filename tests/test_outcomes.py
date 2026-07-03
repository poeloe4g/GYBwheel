"""evaluate_outcomes unit tests (offline)."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import evaluate_outcomes as eo


def _row(ticker, strike, mid, exp="2026-08-07", **extra):
    return {"ticker": ticker, "strike": strike, "mid": mid, "expiration": exp,
            "spot": extra.pop("spot", 100.0), **extra}


def _snapshot(run_date="2026-07-03", rows=(), near_misses=(), demo=False):
    return {"schema_version": 3,
            "meta": {"run_date": run_date, "demo": demo},
            "rows": list(rows), "near_misses": list(near_misses)}


class FakeProvider:
    """Deterministic daily closes per ticker; records fetch calls."""

    def __init__(self, histories):
        self.histories = histories
        self.calls = []

    def get_price_history(self, ticker, period="1y"):
        self.calls.append(ticker)
        if isinstance(self.histories.get(ticker), Exception):
            raise self.histories[ticker]
        return self.histories.get(ticker) or []


def _hist(closes_by_date):
    return [{"date": d, "close": c} for d, c in closes_by_date.items()]


WIN_HIST = _hist({"2026-07-03": 100.0, "2026-07-20": 96.0, "2026-08-07": 98.0,
                  "2026-08-10": 99.0})
BREACH_HIST = _hist({"2026-07-03": 100.0, "2026-07-20": 85.0, "2026-08-07": 88.0,
                     "2026-08-10": 90.0})


def _run_dir(tmp_path, *snapshots):
    runs = tmp_path / "runs"
    runs.mkdir()
    for i, snap in enumerate(snapshots):
        (runs / f"snap{i}.json").write_text(json.dumps(snap))
    return runs


def test_win_and_breach_outcomes(tmp_path):
    snap = _snapshot(rows=[_row("WINNER", 92.0, 1.10)],
                     near_misses=[{**_row("LOSER", 92.0, 1.10),
                                   "rejection_reasons": [{"code": "spread"}],
                                   "data_flags": []}])
    provider = FakeProvider({"WINNER": WIN_HIST, "LOSER": BREACH_HIST})
    doc = eo.evaluate_runs(_run_dir(tmp_path, snap), tmp_path / "out.json",
                           provider, today=date(2026, 8, 10))

    win = doc["outcomes"]["2026-07-03|WINNER|2026-08-07|92.0"]
    assert win["win"] is True and win["touched"] is False
    assert win["expiry_close"] == 98.0
    assert win["realized_roc"] == round(110.0 / 9200.0, 6)  # premium kept
    assert win["group"] == "candidate"

    lose = doc["outcomes"]["2026-07-03|LOSER|2026-08-07|92.0"]
    assert lose["win"] is False and lose["touched"] is True
    # premium - (strike - expiry close): 110 - (92-88)*100 = -290
    assert lose["realized_roc"] == round(-290.0 / 9200.0, 6)
    assert lose["group"] == "near_miss"

    s = doc["summary"]
    assert s["candidates"] == {"n": 1, "wins": 1, "win_rate": 1.0,
                               "avg_realized_roc": round(110.0 / 9200.0, 6)}
    assert s["near_misses"]["n"] == 1 and s["near_misses"]["wins"] == 0
    assert s["by_rejection_code"]["spread"]["win_rate"] == 0.0


def test_not_yet_expired_and_no_settlement_bar_skipped(tmp_path):
    snap = _snapshot(rows=[
        _row("FUTURE", 92.0, 1.10, exp="2026-12-18"),
        _row("FRESH", 92.0, 1.10, exp="2026-08-07"),
    ])
    # FRESH expired but history stops before expiry -> not evaluable yet.
    provider = FakeProvider({"FRESH": _hist({"2026-07-03": 100.0, "2026-08-05": 97.0})})
    doc = eo.evaluate_runs(_run_dir(tmp_path, snap), tmp_path / "out.json",
                           provider, today=date(2026, 8, 8))
    assert doc["outcomes"] == {}
    assert provider.calls == ["FRESH"]  # FUTURE never fetched


def test_weekend_expiry_uses_last_close_before(tmp_path):
    # No bar exactly on the (Saturday-like) expiry: last close <= expiry wins,
    # and a later bar proves the market has moved past it.
    hist = _hist({"2026-07-03": 100.0, "2026-08-06": 95.0, "2026-08-10": 60.0})
    snap = _snapshot(rows=[_row("HOLIDAY", 92.0, 1.10, exp="2026-08-08")])
    doc = eo.evaluate_runs(_run_dir(tmp_path, snap), tmp_path / "out.json",
                           FakeProvider({"HOLIDAY": hist}), today=date(2026, 8, 11))
    out = doc["outcomes"]["2026-07-03|HOLIDAY|2026-08-08|92.0"]
    assert out["expiry_close"] == 95.0  # not the post-expiry 60.0
    assert out["win"] is True


def test_idempotent_and_failed_fetch_retried(tmp_path):
    snap = _snapshot(rows=[_row("WINNER", 92.0, 1.10)],
                     near_misses=[{**_row("FLAKY", 92.0, 1.10),
                                   "rejection_reasons": [{"code": "open_interest"}]}])
    out = tmp_path / "out.json"

    p1 = FakeProvider({"WINNER": WIN_HIST, "FLAKY": RuntimeError("rate limited")})
    doc1 = eo.evaluate_runs(_run_dir(tmp_path, snap), out, p1, today=date(2026, 8, 10))
    assert set(doc1["outcomes"]) == {"2026-07-03|WINNER|2026-08-07|92.0"}

    # Second pass: WINNER cached in the file (no refetch), FLAKY retried.
    p2 = FakeProvider({"WINNER": WIN_HIST, "FLAKY": WIN_HIST})
    doc2 = eo.evaluate_runs(tmp_path / "runs", out, p2, today=date(2026, 8, 10))
    assert p2.calls == ["FLAKY"]
    assert len(doc2["outcomes"]) == 2
    # Recorded outcomes are byte-stable across passes.
    assert doc2["outcomes"]["2026-07-03|WINNER|2026-08-07|92.0"] == \
        doc1["outcomes"]["2026-07-03|WINNER|2026-08-07|92.0"]


def test_demo_snapshots_and_derived_spot(tmp_path):
    demo = _snapshot(run_date="2026-07-01", rows=[_row("FAKE", 92.0, 1.10)], demo=True)
    # Pre-v3 row: no spot, but distance_to_strike lets us derive it.
    old_row = {"ticker": "OLD", "strike": 92.0, "mid": 1.10,
               "expiration": "2026-08-07", "distance_to_strike": 0.08}
    old = _snapshot(rows=[old_row])
    provider = FakeProvider({"OLD": WIN_HIST})
    doc = eo.evaluate_runs(_run_dir(tmp_path, demo, old), tmp_path / "out.json",
                           provider, today=date(2026, 8, 10))
    assert list(doc["outcomes"]) == ["2026-07-03|OLD|2026-08-07|92.0"]
    assert doc["outcomes"]["2026-07-03|OLD|2026-08-07|92.0"]["spot"] == 100.0
    assert "FAKE" not in provider.calls  # demo rows never evaluated


def test_main_cli_with_injected_provider(tmp_path, capsys):
    runs = _run_dir(tmp_path, _snapshot(rows=[_row("WINNER", 92.0, 1.10)]))
    out = tmp_path / "outcomes.json"
    rc = eo.main(["--runs", str(runs), "--out", str(out), "--today", "2026-08-10"],
                 provider=FakeProvider({"WINNER": WIN_HIST}))
    assert rc == 0
    assert "1 resolved" in capsys.readouterr().out
    doc = json.loads(out.read_text())
    assert doc["schema_version"] == 1
    assert doc["summary"]["candidates"]["n"] == 1
