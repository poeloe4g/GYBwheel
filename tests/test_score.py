import score


def _blended(config):
    return {**config, "scoring": {**config.get("scoring", {}), "mode": "blended"}}


def test_blended_score_components_exposed(config):
    row = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.22}
    scored = score.score_candidate(row, _blended(config), spot=100.0)
    assert scored["score_mode"] == "blended"
    assert scored["distance_to_strike"] > 0
    assert scored["implied_move"] > 0
    # blended = ann * dist / denom
    expected = 0.22 * scored["distance_to_strike"] / scored["score_denominator"]
    assert abs(scored["score"] - expected) < 1e-9


def test_risk_adjusted_is_default_and_weights_by_pop(config):
    row = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.22,
           "abs_delta": 0.20}
    scored = score.score_candidate(row, config, spot=100.0)
    assert scored["score_mode"] == "risk_adjusted"
    assert scored["pop"] == 0.80
    expected = 0.22 * 0.80 * scored["distance_to_strike"] / scored["score_denominator"]
    assert abs(scored["score"] - expected) < 1e-9


def test_risk_adjusted_prefers_better_odds_over_fatter_premium(config):
    # Same cushion-in-sigma; the knife-edge strike has a fatter premium but
    # far worse odds — risk_adjusted must rank the safer one higher.
    risky = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.30,
             "abs_delta": 0.45}
    safer = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.25,
             "abs_delta": 0.18}
    s_risky = score.score_candidate(risky, config, spot=100.0)["score"]
    s_safer = score.score_candidate(safer, config, spot=100.0)["score"]
    assert s_safer > s_risky
    # Legacy blended would have ranked them the other way.
    b_risky = score.score_candidate(risky, _blended(config), spot=100.0)["score"]
    b_safer = score.score_candidate(safer, _blended(config), spot=100.0)["score"]
    assert b_risky > b_safer


def test_risk_adjusted_without_delta_degrades_to_blended(config):
    row = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.22}
    scored = score.score_candidate(row, config, spot=100.0)
    assert scored["pop"] is None
    expected = 0.22 * scored["distance_to_strike"] / scored["score_denominator"]
    assert abs(scored["score"] - expected) < 1e-9


def test_outlier_iv_swapped_for_band_median(config):
    # Contract IV is 4x the band median (> iv_outlier_mult=2.5): the implied
    # move must come from the median, and iv_used must say so.
    row = {"strike": 95.0, "dte": 35, "iv": 0.96, "iv_band_median": 0.24,
           "annualized_yield": 0.22, "abs_delta": 0.20}
    scored = score.score_candidate(row, config, spot=100.0)
    assert scored["iv_used"] == 0.24
    sane = score.score_candidate({**row, "iv": 0.24}, config, spot=100.0)
    assert abs(scored["score"] - sane["score"]) < 1e-9


def test_missing_iv_falls_back_to_band_median(config):
    row = {"strike": 95.0, "dte": 35, "iv": None, "iv_band_median": 0.24,
           "annualized_yield": 0.22, "abs_delta": 0.20}
    scored = score.score_candidate(row, config, spot=100.0)
    assert scored["iv_used"] == 0.24
    assert scored["implied_move"] > 0


def test_sane_iv_kept_over_band_median(config):
    row = {"strike": 95.0, "dte": 35, "iv": 0.30, "iv_band_median": 0.24,
           "annualized_yield": 0.22, "abs_delta": 0.20}
    assert score.score_candidate(row, config, spot=100.0)["iv_used"] == 0.30


def test_denominator_floor_prevents_blowup(config):
    # Near-zero implied move would explode without the floor.
    row = {"strike": 95.0, "dte": 35, "iv": 0.0, "annualized_yield": 0.22}
    scored = score.score_candidate(row, config, spot=100.0)
    assert scored["score_denominator"] == config["quality"]["score_denominator_floor"]
    assert scored["score"] < 1e6  # bounded


def test_annualized_only_mode(config):
    cfg = {**config, "scoring": {"mode": "annualized_yield_only"}}
    row = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.22}
    scored = score.score_candidate(row, cfg, spot=100.0)
    assert scored["score"] == 0.22


def test_rank_orders_descending():
    rows = [{"score": 1.0}, {"score": 3.0}, {"score": 2.0}]
    assert [r["score"] for r in score.rank(rows)] == [3.0, 2.0, 1.0]


def test_rank_prefer_affordable_boosts_tradeable_rows():
    rows = [{"score": 3.0, "affordable": False},
            {"score": 1.0, "affordable": True},
            {"score": 2.0, "affordable": True}]
    ranked = score.rank(rows, prefer_affordable=True)
    assert [r["score"] for r in ranked] == [2.0, 1.0, 3.0]
    # Default ranking is untouched (near-miss ordering relies on it).
    assert [r["score"] for r in score.rank(rows)] == [3.0, 2.0, 1.0]


def test_rank_prefer_live_quotes_ranks_indicative_rows_lower():
    rows = [{"score": 3.0, "quote_quality": "last_price"},
            {"score": 1.0, "quote_quality": "live"},
            {"score": 2.0}]  # missing quote_quality treated as live
    ranked = score.rank(rows, prefer_live_quotes=True)
    assert [r["score"] for r in ranked] == [2.0, 1.0, 3.0]


def test_rank_affordable_tier_outranks_quote_tier():
    rows = [{"score": 5.0, "affordable": False, "quote_quality": "live"},
            {"score": 1.0, "affordable": True, "quote_quality": "last_price"},
            {"score": 2.0, "affordable": True, "quote_quality": "live"}]
    ranked = score.rank(rows, prefer_affordable=True, prefer_live_quotes=True)
    assert [r["score"] for r in ranked] == [2.0, 1.0, 5.0]


def test_rank_prefer_two_sided_ranks_thin_call_rows_lower():
    rows = [{"score": 3.0, "thin_call_side": True},
            {"score": 1.0, "thin_call_side": False},
            {"score": 2.0},          # unmeasured call side never sinks a row
            {"score": 1.5, "thin_call_side": None}]
    ranked = score.rank(rows, prefer_two_sided=True)
    assert [r["score"] for r in ranked] == [2.0, 1.5, 1.0, 3.0]
    # Off by default: pure score order.
    assert [r["score"] for r in score.rank(rows)] == [3.0, 2.0, 1.5, 1.0]


def test_rank_two_sided_is_the_weakest_tier():
    # Affordability and quote quality both dominate the call-side sanity tier.
    rows = [{"score": 5.0, "affordable": True, "quote_quality": "last_price",
             "thin_call_side": False},
            {"score": 1.0, "affordable": True, "quote_quality": "live",
             "thin_call_side": True},
            {"score": 2.0, "affordable": False, "quote_quality": "live",
             "thin_call_side": False}]
    ranked = score.rank(rows, prefer_affordable=True, prefer_live_quotes=True,
                        prefer_two_sided=True)
    assert [r["score"] for r in ranked] == [1.0, 5.0, 2.0]
