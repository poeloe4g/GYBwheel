import screen


def test_selector_picks_nearest_target_delta(chain):
    # target 0.20 within [0.15, 0.30]; the 95 strike has abs(delta)=0.20.
    pick = screen.select_nearest_delta_put(
        chain, spot=100.0, target_delta=0.20, delta_min=0.15, delta_max=0.30
    )
    assert pick is not None
    assert pick["strike"] == 95.0
    assert pick["abs_delta"] == 0.20


def test_selector_ignores_calls_and_out_of_band(chain):
    # Narrow band excludes everything but the 0.20 put.
    pick = screen.select_nearest_delta_put(
        chain, spot=100.0, target_delta=0.20, delta_min=0.18, delta_max=0.22
    )
    assert pick["strike"] == 95.0
    assert pick["option_type"] == "put"


def test_selector_returns_none_when_none_qualify(chain):
    pick = screen.select_nearest_delta_put(
        chain, spot=100.0, target_delta=0.50, delta_min=0.45, delta_max=0.55
    )
    assert pick is None


def test_selector_uses_bs_fallback_when_no_greeks():
    opt = {"option_type": "put", "strike": 95.0, "iv": 0.24, "dte": 35,
           "delta": None, "mid": 1.0, "bid": 0.95, "ask": 1.05}
    pick = screen.select_nearest_delta_put(
        [opt], spot=100.0, target_delta=0.20, delta_min=0.10, delta_max=0.30
    )
    assert pick is not None
    assert 0.15 < pick["abs_delta"] < 0.25


def test_earnings_filter_rejects_span_and_toggles():
    avoid_off = screen.passes_earnings_filter("2099-07-18", "2099-07-10", avoid=False)
    assert avoid_off[0] is True
    rejected = screen.passes_earnings_filter("2099-07-18", "2099-07-10", avoid=True)
    assert rejected[0] is False
    accepted = screen.passes_earnings_filter("2099-07-18", "2099-08-01", avoid=True)
    assert accepted[0] is True


def test_earnings_filter_missing_data_degrades(caplog):
    ok, reason = screen.passes_earnings_filter("2099-07-18", None, avoid=True)
    assert ok is True
    assert "unknown" in reason


def _quality(config):
    return config["quality"]


def test_quality_passes_good_contract(config):
    good = {"strike": 95.0, "mid": 1.35, "dte": 35, "iv": 0.24,
            "bid": 1.30, "ask": 1.40, "open_interest": 2500}
    assert screen.apply_quality_filters(good, spot=100.0, quality=_quality(config)) == []


def test_quality_rejects_each_failure(config):
    q = _quality(config)
    # wide spread
    bad_spread = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 0.24,
                  "bid": 0.50, "ask": 1.50, "open_interest": 2500}
    assert any("spread" in r for r in screen.apply_quality_filters(bad_spread, 100.0, q))
    # low OI
    bad_oi = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 0.24,
              "bid": 0.95, "ask": 1.05, "open_interest": 10}
    assert any("OI" in r for r in screen.apply_quality_filters(bad_oi, 100.0, q))
    # too close to strike (strike 99 vs spot 100 -> 1% < 5%)
    too_close = {"strike": 99.0, "mid": 1.0, "dte": 35, "iv": 0.24,
                 "bid": 0.95, "ask": 1.05, "open_interest": 2500}
    assert any("distance" in r for r in screen.apply_quality_filters(too_close, 100.0, q))
    # huge implied move
    big_iv = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 1.5,
              "bid": 0.95, "ask": 1.05, "open_interest": 2500}
    assert any("implied move" in r for r in screen.apply_quality_filters(big_iv, 100.0, q))
