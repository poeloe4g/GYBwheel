import score


def test_blended_score_components_exposed(config):
    row = {"strike": 95.0, "dte": 35, "iv": 0.24, "annualized_yield": 0.22}
    scored = score.score_candidate(row, config, spot=100.0)
    assert scored["score_mode"] == "blended"
    assert scored["distance_to_strike"] > 0
    assert scored["implied_move"] > 0
    # blended = ann * dist / denom
    expected = 0.22 * scored["distance_to_strike"] / scored["score_denominator"]
    assert abs(scored["score"] - expected) < 1e-9


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
