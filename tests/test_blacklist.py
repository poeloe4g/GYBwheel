from datetime import date

import blacklist

TODAY = date(2026, 7, 27)


def _active(entries, today=TODAY):
    return blacklist.normalize(entries, today=today)[0]


def _expired(entries, today=TODAY):
    return blacklist.normalize(entries, today=today)[1]


# --- shapes ----------------------------------------------------------------
def test_bare_strings_still_supported():
    active = _active(["mega", " tiny "])
    assert set(active) == {"MEGA", "TINY"}
    assert active["MEGA"]["reason"] == ""


def test_dict_entry_keeps_reason_and_dates():
    active = _active([
        {"ticker": "xyz", "reason": "  burned me  ", "added": "2026-07-01",
         "review_after": "2027-01-01"},
    ])
    assert active["XYZ"] == {
        "ticker": "XYZ", "reason": "burned me",
        "added": "2026-07-01", "review_after": "2027-01-01",
    }


def test_mixed_shapes_in_one_list():
    assert set(_active(["AAA", {"ticker": "BBB", "reason": "no"}])) == {"AAA", "BBB"}


# --- expiry ----------------------------------------------------------------
def test_review_after_in_the_future_stays_active():
    assert set(_active([{"ticker": "AAA", "review_after": "2026-07-28"}])) == {"AAA"}


def test_review_after_in_the_past_expires():
    entries = [{"ticker": "AAA", "review_after": "2026-07-26"}]
    assert _active(entries) == {}
    assert [e["ticker"] for e in _expired(entries)] == ["AAA"]


def test_review_after_today_releases_the_ban():
    """"Review after the 27th" means the 27th is the day you look at it again."""
    assert _active([{"ticker": "AAA", "review_after": "2026-07-27"}]) == {}


def test_absent_review_after_is_permanent():
    active = _active([{"ticker": "AAA", "reason": "never"}])
    assert active["AAA"]["review_after"] is None
    assert _expired([{"ticker": "AAA"}]) == []


def test_unparseable_review_after_falls_back_to_permanent():
    """A typo'd date must not silently release a ban."""
    active = _active([{"ticker": "AAA", "review_after": "next tuesday"}])
    assert set(active) == {"AAA"}
    assert active["AAA"]["review_after"] is None


def test_real_date_object_accepted():
    assert _active([{"ticker": "AAA", "review_after": date(2027, 1, 1)}]).keys() == {"AAA"}


# --- failing open ----------------------------------------------------------
def test_malformed_entries_are_skipped_not_raised():
    active = _active(["", "  ", {"reason": "no ticker"}, {"ticker": ""}, 42, None, ["AAA"]])
    assert active == {}


def test_non_list_blacklist_ignored():
    assert _active("MEGA") == {}
    assert _active({"ticker": "MEGA"}) == {}
    assert _active(None) == {}


def test_later_duplicate_wins():
    active = _active([{"ticker": "AAA", "reason": "first"}, {"ticker": "AAA", "reason": "second"}])
    assert active["AAA"]["reason"] == "second"


# --- selections extraction -------------------------------------------------
def test_from_selections_reads_the_block():
    assert blacklist.from_selections({"blacklist": ["AAA"]}) == ["AAA"]


def test_from_selections_tolerates_absence_and_junk():
    assert blacklist.from_selections(None) == []
    assert blacklist.from_selections({}) == []
    assert blacklist.from_selections({"blacklist": None}) == []
    assert blacklist.from_selections({"blacklist": "AAA"}) == []


# --- merge -----------------------------------------------------------------
def test_merge_unions_both_sources():
    assert set(_active(blacklist.merge(["AAA"], ["BBB"]))) == {"AAA", "BBB"}


def test_dashboard_wins_on_conflict():
    merged = blacklist.merge(
        [{"ticker": "AAA", "reason": "from config"}],
        [{"ticker": "aaa", "reason": "from dashboard"}],
    )
    assert _active(merged)["AAA"]["reason"] == "from dashboard"


def test_merge_tolerates_non_list_sources():
    assert set(_active(blacklist.merge(None, ["AAA"]))) == {"AAA"}
    assert set(_active(blacklist.merge(["AAA"], "nonsense"))) == {"AAA"}


def test_merge_keeps_invalid_entries_for_normalize_to_report():
    """Dropping them here would hide the warning; normalize() is the one place
    that decides what a malformed entry means."""
    assert len(blacklist.merge([{"reason": "no ticker"}], [])) == 1
    assert _active(blacklist.merge([{"reason": "no ticker"}], [])) == {}


# --- describe --------------------------------------------------------------
def test_describe_always_says_who_excluded_it():
    assert blacklist.describe({"ticker": "AAA", "reason": ""}) == "blacklisted"
    assert blacklist.describe({"reason": "nope"}) == "blacklisted: nope"
    assert blacklist.describe(
        {"reason": "nope", "review_after": "2027-01-01"}
    ) == "blacklisted: nope (review after 2027-01-01)"
