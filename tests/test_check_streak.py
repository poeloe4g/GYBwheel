"""check_streak unit tests (offline)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_streak


def _run(date, light="GREEN", rows=0, demo=False, **extra):
    return {"date": date, "light": light, "row_count": rows, "demo": demo, **extra}


def test_streak_counts_trailing_zero_runs():
    index = {"runs": [_run("d1", rows=3), _run("d2"), _run("d3"), _run("d4")]}
    assert check_streak.zero_candidate_streak(index) == 3


def test_streak_broken_by_candidates():
    index = {"runs": [_run("d1"), _run("d2", rows=2), _run("d3")]}
    assert check_streak.zero_candidate_streak(index) == 1


def test_streak_skips_red_and_demo_runs():
    # RED days and demo seeds are neither zero-streak evidence nor breakers.
    index = {"runs": [_run("d1"), _run("d2", light="RED"), _run("d3"),
                      _run("d4", rows=5, demo=True), _run("d5")]}
    assert check_streak.zero_candidate_streak(index) == 3


def test_streak_skips_untrusted_offhours_runs():
    # Zero candidates on off-hours (stale-quote) runs is expected, not evidence;
    # pre-v3 runs without the field still count.
    index = {"runs": [_run("d1"), _run("d2", quotes_trusted=False),
                      _run("d3", quotes_trusted=True), _run("d4")]}
    assert check_streak.zero_candidate_streak(index) == 3


def test_empty_index_is_no_streak():
    assert check_streak.zero_candidate_streak({"runs": []}) == 0
    assert check_streak.zero_candidate_streak({}) == 0


def test_untrusted_streak_counts_trailing_offhours_runs():
    index = {"runs": [_run("d1", quotes_trusted=True),
                      _run("d2", quotes_trusted=False),
                      _run("d3", quotes_trusted=False),
                      _run("d4", quotes_trusted=False)]}
    assert check_streak.untrusted_streak(index) == 3


def test_untrusted_streak_skips_unstamped_and_demo_runs():
    # Pre-v3 runs never stamped a session; they are neither evidence nor breakers.
    index = {"runs": [_run("d1", quotes_trusted=True),
                      _run("d2", quotes_trusted=False),
                      _run("d3"),  # unstamped
                      _run("d4", quotes_trusted=True, demo=True),
                      _run("d5", quotes_trusted=False)]}
    assert check_streak.untrusted_streak(index) == 2


def test_untrusted_streak_broken_by_trusted_run():
    index = {"runs": [_run("d1", quotes_trusted=False),
                      _run("d2", quotes_trusted=True),
                      _run("d3", quotes_trusted=False)]}
    assert check_streak.untrusted_streak(index) == 1
    assert check_streak.untrusted_streak({"runs": []}) == 0


def test_main_untrusted_mode_exit_codes(tmp_path):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"runs": [
        _run("d1", rows=5, quotes_trusted=False),
        _run("d2", rows=5, quotes_trusted=False),
        _run("d3", rows=5, quotes_trusted=False)]}))
    assert check_streak.main(["--index", str(idx), "--threshold", "3",
                              "--mode", "untrusted"]) == 1
    # Runs produced candidates, so zero mode stays quiet (and skips untrusted).
    assert check_streak.main(["--index", str(idx), "--threshold", "3"]) == 0


def test_main_exit_codes(tmp_path, capsys):
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps({"runs": [_run("d1"), _run("d2"), _run("d3")]}))
    assert check_streak.main(["--index", str(idx), "--threshold", "3"]) == 1
    assert check_streak.main(["--index", str(idx), "--threshold", "4"]) == 0
    # Missing index must not false-alarm.
    assert check_streak.main(["--index", str(tmp_path / "nope.json")]) == 0
