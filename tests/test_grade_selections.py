"""grade_selections unit tests (offline)."""
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import grade_selections as gs
from test_outcomes import BREACH_HIST, WIN_HIST, FakeProvider


def _sel(ticker="WINNER", strike=92.0, premium=1.10, contracts=2,
         exp="2026-08-07", status="OPEN", **extra):
    return {
        "uid": f"2026-07-03|{ticker}|{exp}|{strike}|2026-07-05T14:00:00Z",
        "key": f"2026-07-03|{ticker}|{exp}|{strike}",
        "run_date": "2026-07-03",
        "ticker": ticker,
        "sector": "Technology",
        "strike": strike,
        "expiration": exp,
        "contracts": contracts,
        "entry_premium": premium,
        "collateral": strike * 100.0 * contracts,
        "selected_at": "2026-07-05T14:00:00Z",
        "status": status,
        "close": extra.pop("close", None),
        **extra,
    }


def _write(tmp_path, *selections):
    p = tmp_path / "selections.json"
    p.write_text(json.dumps({"schema_version": 1, "updated_at": None,
                             "selections": list(selections), "summary": None}))
    return p


def test_win_and_assigned_scaled_by_contracts(tmp_path):
    path = _write(tmp_path, _sel(), _sel(ticker="LOSER"))
    provider = FakeProvider({"WINNER": WIN_HIST, "LOSER": BREACH_HIST})
    doc = gs.grade_file(path, provider, today=date(2026, 8, 10))

    win, lose = doc["selections"]
    assert win["status"] == "EXPIRED_WIN"
    assert win["close"]["method"] == "expiry"
    assert win["close"]["expiry_close"] == 98.0
    # premium kept: 1.10 * 100 * 2 contracts
    assert win["close"]["pnl_usd"] == 220.0
    assert win["close"]["realized_roc"] == round(110.0 / 9200.0, 6)

    assert lose["status"] == "ASSIGNED"
    # (1.10 - (92 - 88)) * 100 * 2 = -580
    assert lose["close"]["pnl_usd"] == -580.0
    assert lose["close"]["win"] is False

    s = doc["summary"]
    assert s["open"]["n"] == 0
    # avg is over the stored (already-rounded) per-entry realized_roc values
    stored_avg = round((round(110.0 / 9200.0, 6) + round(-290.0 / 9200.0, 6)) / 2, 6)
    assert s["closed"] == {"n": 2, "wins": 1, "win_rate": 0.5,
                           "total_pnl_usd": -360.0,
                           "total_premium_collected_usd": 440.0,
                           "avg_realized_roc": stored_avg}
    assert s["equity_curve"] == [{"date": "2026-08-07", "cum_pnl_usd": 220.0},
                                 {"date": "2026-08-07", "cum_pnl_usd": -360.0}]


def test_annualized_from_selected_at_not_run_date(tmp_path):
    path = _write(tmp_path, _sel())
    doc = gs.grade_file(path, FakeProvider({"WINNER": WIN_HIST}),
                        today=date(2026, 8, 10))
    close = doc["selections"][0]["close"]
    held = (date(2026, 8, 7) - date(2026, 7, 5)).days  # selected_at, not run_date
    assert held == 33
    # annualized from the exact (unrounded) roc: 110/9200 * 365/33
    assert close["annualized_realized"] == round(
        (110.0 / 9200.0) * 365.0 / held, 6)


def test_not_yet_expired_untouched(tmp_path):
    path = _write(tmp_path, _sel(exp="2026-12-18"))
    provider = FakeProvider({})
    doc = gs.grade_file(path, provider, today=date(2026, 8, 10))
    assert doc["selections"][0]["status"] == "OPEN"
    assert provider.calls == []  # never fetched
    assert doc["summary"]["open"] == {"n": 1, "collateral": 18400.0,
                                      "premium_at_risk_usd": 220.0}


def test_early_closed_passthrough_and_summary(tmp_path):
    early = _sel(ticker="EARLY", status="EARLY_CLOSED",
                 close={"method": "early_close", "closed_at": "2026-07-20",
                        "buyback_price": 0.30, "pnl_usd": 160.0,
                        "realized_roc": 0.0087, "annualized_realized": 0.21,
                        "win": True})
    path = _write(tmp_path, early, _sel())
    provider = FakeProvider({"WINNER": WIN_HIST})
    doc = gs.grade_file(path, provider, today=date(2026, 8, 10))
    assert doc["selections"][0] == early  # byte-stable passthrough
    assert "EARLY" not in provider.calls
    assert doc["summary"]["closed"]["n"] == 2
    # Curve ordered by closed_at: early close first.
    assert [pt["cum_pnl_usd"] for pt in doc["summary"]["equity_curve"]] == [160.0, 380.0]


def test_idempotent_and_failed_fetch_retried(tmp_path):
    path = _write(tmp_path, _sel(), _sel(ticker="FLAKY"))
    p1 = FakeProvider({"WINNER": WIN_HIST, "FLAKY": RuntimeError("rate limited")})
    doc1 = gs.grade_file(path, p1, today=date(2026, 8, 10))
    assert doc1["selections"][0]["status"] == "EXPIRED_WIN"
    assert doc1["selections"][1]["status"] == "OPEN"

    p2 = FakeProvider({"WINNER": WIN_HIST, "FLAKY": WIN_HIST})
    doc2 = gs.grade_file(path, p2, today=date(2026, 8, 10))
    assert p2.calls == ["FLAKY"]  # WINNER terminal — never refetched
    assert doc2["selections"][0] == doc1["selections"][0]
    assert doc2["selections"][1]["status"] == "EXPIRED_WIN"


def test_missing_file_and_malformed_entries(tmp_path, capsys):
    assert gs.grade_file(tmp_path / "nope.json", FakeProvider({})) is None

    path = _write(tmp_path, {"status": "OPEN"}, _sel())
    doc = gs.grade_file(path, FakeProvider({"WINNER": WIN_HIST}),
                        today=date(2026, 8, 10))
    assert doc["selections"][1]["status"] == "EXPIRED_WIN"


def test_live_verified_entries_grade_alongside_old_shape(tmp_path):
    """New live-verified entries (with overridden contracts) grade like any
    other: P&L from the live strike/premium, verify block preserved verbatim."""
    verify = {
        "verified_at": "2026-07-05T14:00:00Z",
        "bid": 1.00, "ask": 1.10, "spot": 101.0,
        "iv": 0.28, "iv_source": "solved",
        "abs_delta": 0.21, "implied_move": 0.086,
        "score": 1.234, "score_mode": "risk_adjusted", "screener_score": 1.301,
        "verdict": "amber",
        "gates": [{"code": "oi_unknown", "status": "flag",
                   "message": "different contract than screened"}],
        "contract_overridden": True,
        "screener_contract": {"strike": 92.0, "expiration": "2026-08-07"},
    }
    # Live strike 90 (screener screened 92), live conservative premium 1.0375.
    verified = _sel(strike=90.0, premium=1.0375, contracts=1,
                    live_verified=True, verify=verify,
                    entry_premium_basis="conservative")
    path = _write(tmp_path, _sel(ticker="LOSER"), verified)
    provider = FakeProvider({"WINNER": WIN_HIST, "LOSER": BREACH_HIST})
    doc = gs.grade_file(path, provider, today=date(2026, 8, 10))

    old, new = doc["selections"]
    assert old["status"] == "ASSIGNED"          # old-shape entry unaffected
    assert "verify" not in old
    assert new["status"] == "EXPIRED_WIN"       # expiry close 98 > live strike 90
    # P&L uses the LIVE premium and strike, not the screener's.
    assert new["close"]["pnl_usd"] == round(1.0375 * 100, 2)
    # The verify block survives grading byte-for-byte.
    assert new["verify"] == verify
    assert new["live_verified"] is True
    assert doc["summary"]["closed"]["n"] == 2


def test_account_block_and_manual_edits_survive_grading(tmp_path):
    """The dashboard's capital block and manually corrected closes round-trip
    through grading untouched (terminal entries are never re-examined)."""
    account = {"total_capital": 62000, "updated_at": "2026-07-17T14:05:00Z",
               "history": [{"total_capital": 62000,
                            "changed_at": "2026-07-17T14:05:00Z", "note": "deposit"}]}
    manual = _sel(ticker="MANUAL", status="ASSIGNED",
                  close={"method": "manual_expiry", "closed_at": "2026-08-07",
                         "expiry_close": 88.0, "pnl_usd": -580.0,
                         "realized_roc": -0.031522, "annualized_realized": -0.348674,
                         "win": False, "edited_at": "2026-08-09T10:00:00Z"})
    edited_early = _sel(ticker="EDITED", status="EARLY_CLOSED",
                        close={"method": "early_close", "closed_at": "2026-07-20",
                               "buyback_price": 0.42, "pnl_usd": 136.0,
                               "realized_roc": 0.00739, "annualized_realized": 0.1799,
                               "win": True, "edited_at": "2026-08-01T10:00:00Z"})
    p = tmp_path / "selections.json"
    p.write_text(json.dumps({"schema_version": 2, "updated_at": None,
                             "account": account,
                             "selections": [manual, edited_early, _sel()],
                             "summary": None}))
    provider = FakeProvider({"WINNER": WIN_HIST})
    doc = gs.grade_file(p, provider, today=date(2026, 8, 10))

    assert doc["account"] == account            # capital block round-trips
    assert doc["selections"][0] == manual       # manual override never re-graded
    assert doc["selections"][1] == edited_early
    assert provider.calls == ["WINNER"]         # terminal tickers never fetched
    assert doc["selections"][2]["status"] == "EXPIRED_WIN"
    assert doc["summary"]["closed"]["n"] == 3
    # And the block survives a rewrite on disk, not just in the return value.
    assert json.loads(p.read_text())["account"] == account


def test_main_cli_with_injected_provider(tmp_path, capsys):
    path = _write(tmp_path, _sel())
    rc = gs.main(["--selections", str(path), "--today", "2026-08-10"],
                 provider=FakeProvider({"WINNER": WIN_HIST}))
    assert rc == 0
    assert "1 newly graded" in capsys.readouterr().out
    doc = json.loads(path.read_text())
    assert doc["selections"][0]["status"] == "EXPIRED_WIN"
    assert doc["updated_at"] is not None
