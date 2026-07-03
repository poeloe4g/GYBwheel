"""Candidate-list resolution and ticker-file parsing (offline)."""
import argparse
from pathlib import Path

import main as main_mod


def _args(**over):
    base = dict(tickers=None, tickers_file=None)
    base.update(over)
    return argparse.Namespace(**base)


def test_explicit_tickers_beat_file(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("MSFT\nKO\n")
    args = _args(tickers="aapl, nvda", tickers_file=str(f))
    assert main_mod._resolve_candidates(args) == ["AAPL", "NVDA"]


def test_tickers_file_used_when_present(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("# header comment\nmsft\nKO  # inline comment\n\n  \n")
    args = _args(tickers_file=str(f))
    assert main_mod._resolve_candidates(args) == ["MSFT", "KO"]


def test_missing_file_falls_back_to_seed(tmp_path):
    args = _args(tickers_file=str(tmp_path / "nope.txt"))
    assert main_mod._resolve_candidates(args) == main_mod.DEFAULT_CANDIDATES


def test_default_tickers_file_is_the_sp100_list(monkeypatch):
    # The CLI default points at the checked-in S&P 100 universe (repo-relative).
    monkeypatch.chdir(Path(main_mod.__file__).resolve().parent)
    parsed = main_mod.build_arg_parser().parse_args([])
    assert parsed.tickers_file == main_mod.DEFAULT_TICKERS_FILE
    candidates = main_mod._resolve_candidates(parsed)
    assert len(candidates) >= 90
    assert "BRK-B" in candidates       # yfinance symbol notation
    assert "GOOG" not in candidates    # deduped in favor of GOOGL
