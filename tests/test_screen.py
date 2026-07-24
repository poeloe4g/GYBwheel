import pytest

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


def _reject_codes(opt, spot, q):
    rejections, _ = screen.apply_quality_filters(opt, spot, q)
    return [r["code"] for r in rejections]


def test_quality_passes_good_contract(config):
    good = {"strike": 95.0, "mid": 1.35, "dte": 35, "iv": 0.24,
            "bid": 1.30, "ask": 1.40, "open_interest": 2500}
    assert screen.apply_quality_filters(good, spot=100.0, quality=_quality(config)) == ([], [])


def test_quality_rejects_each_failure(config):
    q = _quality(config)
    # wide spread
    bad_spread = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 0.24,
                  "bid": 0.50, "ask": 1.50, "open_interest": 2500}
    assert "spread" in _reject_codes(bad_spread, 100.0, q)
    # low OI
    bad_oi = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 0.24,
              "bid": 0.95, "ask": 1.05, "open_interest": 10}
    assert "open_interest" in _reject_codes(bad_oi, 100.0, q)
    # too close to strike (strike 99 vs spot 100 -> 1% < 3%)
    too_close = {"strike": 99.0, "mid": 1.0, "dte": 35, "iv": 0.24,
                 "bid": 0.95, "ask": 1.05, "open_interest": 2500}
    assert "distance" in _reject_codes(too_close, 100.0, q)
    # huge implied move
    big_iv = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 1.5,
              "bid": 0.95, "ask": 1.05, "open_interest": 2500}
    assert "implied_move" in _reject_codes(big_iv, 100.0, q)


def test_quality_missing_data_becomes_flags_not_silent_pass(config):
    q = _quality(config)
    base = {"strike": 95.0, "mid": 1.35, "dte": 35, "iv": 0.24,
            "bid": 1.30, "ask": 1.40, "open_interest": 2500}
    for field, code in (("iv", "iv_missing"), ("bid", "spread_unknown"),
                        ("open_interest", "oi_unknown")):
        rejections, flags = screen.apply_quality_filters({**base, field: None}, 100.0, q)
        assert rejections == []
        assert [f["code"] for f in flags] == [code]


def test_quality_indicative_quote_skips_spread_gate(config):
    # Off-hours zeroed bid: the data layer degraded mid to the last trade and
    # marked the quote; the (meaningless) 200% spread must flag, not reject.
    q = _quality(config)
    opt = {"strike": 95.0, "mid": 1.10, "dte": 35, "iv": 0.24,
           "bid": 0.0, "ask": 2.25, "open_interest": 2500,
           "quote_quality": "last_price"}
    rejections, flags = screen.apply_quality_filters(opt, 110.0, q)
    assert "spread" not in [e["code"] for e in rejections]
    assert [e["code"] for e in flags] == ["quote_indicative"]


def test_quality_tight_absolute_spread_rescues_low_premium(config):
    # $0.08 wide on a $0.50 mid is 16% of mid (> max_spread_pct) but well inside
    # max_spread_abs — an acceptable market for a low-premium contract.
    q = _quality(config)
    opt = {"strike": 90.0, "mid": 0.50, "dte": 35, "iv": 0.20,
           "bid": 0.46, "ask": 0.54, "open_interest": 500}
    assert "spread" not in _reject_codes(opt, 100.0, q)


def test_quality_wide_absolute_spread_still_rejects(config):
    q = _quality(config)
    opt = {"strike": 95.0, "mid": 1.0, "dte": 35, "iv": 0.24,
           "bid": 0.75, "ask": 1.25, "open_interest": 500}
    assert "spread" in _reject_codes(opt, 100.0, q)


def _mk_put(strike, delta, *, bid=0.95, ask=1.05, oi=2500, iv=0.24,
            exp="2099-07-18", dte=35):
    return {"option_type": "put", "strike": strike, "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2, "delta": delta, "iv": iv,
            "open_interest": oi, "volume": 100, "expiration": exp, "dte": dte}


def test_evaluate_puts_rescues_adjacent_qualifying_strike(config):
    # The delta-nearest 95 fails OI; 97 (farther from target) passes cleanly.
    chain = [_mk_put(95.0, -0.20, oi=10), _mk_put(97.0, -0.27, bid=1.40, ask=1.55)]
    res = screen.evaluate_puts(chain, 110.0, target_delta=0.20, delta_min=0.15,
                               delta_max=0.30, quality=config["quality"],
                               next_earnings="2099-12-31")
    assert res["selected"]["strike"] == 97.0
    assert res["selected"]["rejections"] == []
    assert res["n_in_band"] == 2
    assert res["n_qualifying"] == 1
    assert res["gate_failures"] == {"open_interest": 1}


def test_evaluate_puts_falls_back_with_reasons_when_none_qualify(config):
    chain = [_mk_put(95.0, -0.20, oi=10), _mk_put(97.0, -0.27, oi=5)]
    res = screen.evaluate_puts(chain, 110.0, target_delta=0.20, delta_min=0.15,
                               delta_max=0.30, quality=config["quality"],
                               next_earnings="2099-12-31")
    assert res["selected"] is None
    # Fallback is the legacy delta-nearest pick, carrying its own reasons.
    assert res["fallback"]["strike"] == 95.0
    assert [e["code"] for e in res["fallback"]["rejections"]] == ["open_interest"]
    assert res["gate_failures"] == {"open_interest": 2}


def test_evaluate_puts_checks_earnings_per_expiration(config):
    # Earnings 2099-07-20 sits between the two expirations: the delta-nearest
    # (later) contract spans it, the earlier one doesn't and must win.
    near = _mk_put(95.0, -0.20, exp="2099-07-25", dte=42)
    early = _mk_put(94.0, -0.17, exp="2099-07-18", dte=35)
    res = screen.evaluate_puts([near, early], 110.0, target_delta=0.20,
                               delta_min=0.15, delta_max=0.30,
                               quality=config["quality"], next_earnings="2099-07-20")
    assert res["selected"]["expiration"] == "2099-07-18"
    assert res["gate_failures"] == {"earnings": 1}


def test_evaluate_puts_unknown_earnings_flags_every_contract(config):
    res = screen.evaluate_puts([_mk_put(95.0, -0.20)], 110.0, target_delta=0.20,
                               delta_min=0.15, delta_max=0.30,
                               quality=config["quality"], next_earnings=None)
    assert res["selected"] is not None
    assert [e["code"] for e in res["selected"]["flags"]] == ["earnings_unknown"]


def test_evaluate_puts_iv_outlier_swaps_reject_for_flag(config):
    # A junk 5.0 IV among sane ~0.24 IVs would fail implied_move; instead it is
    # flagged iv_outlier and the gate is skipped for that contract.
    chain = [_mk_put(93.0, -0.16, iv=0.22), _mk_put(94.0, -0.18, iv=0.24),
             _mk_put(95.0, -0.20, iv=5.0, oi=10)]
    res = screen.evaluate_puts(chain, 110.0, target_delta=0.20, delta_min=0.15,
                               delta_max=0.30, quality=config["quality"],
                               next_earnings="2099-12-31")
    assert "implied_move" not in res["gate_failures"]
    # The outlier still fails OI, so a sane-IV contract is selected.
    assert res["selected"]["iv"] == 0.24
    # Fallback preference: with everything failing, sane IV outranks outlier.
    all_fail = [{**c, "open_interest": 5} for c in chain]
    res2 = screen.evaluate_puts(all_fail, 110.0, target_delta=0.20, delta_min=0.15,
                                delta_max=0.30, quality=config["quality"],
                                next_earnings="2099-12-31")
    assert res2["selected"] is None
    assert res2["fallback"]["iv"] == 0.24  # delta-nearest sane-IV, not the outlier
    assert any(e["code"] == "iv_outlier" for e in res2["fallback"]["flags"]) is False
    assert res2["n_qualifying"] == 0


def test_evaluate_puts_empty_band(config):
    res = screen.evaluate_puts([_mk_put(95.0, -0.50)], 110.0, target_delta=0.20,
                               delta_min=0.15, delta_max=0.30,
                               quality=config["quality"], next_earnings="2099-12-31")
    assert res == {"selected": None, "fallback": None, "n_in_band": 0,
                   "n_qualifying": 0, "gate_failures": {}}


def test_quality_recalibrated_gates_pass_low_iv_megacap(config):
    # Regression pin for the recalibration: a KO-like low-IV name at ~0.2 delta
    # (strike 4% below spot, IV 16%, modest premium) must pass every gate.
    q = _quality(config)
    opt = {"strike": 96.0, "mid": 0.60, "dte": 35, "iv": 0.16,
           "bid": 0.55, "ask": 0.65, "open_interest": 500}
    assert screen.apply_quality_filters(opt, spot=100.0, quality=q) == ([], [])


# --- call-side context (evaluate_call_side / attach_call_side) --------------
# Advisory by contract: these metrics and the thin_call_side flag must never
# reject a put or shrink the candidate set — main.py routes on gating flags
# only. The tests below pin the "silence when unmeasurable" behavior hard.

CALL_CFG = {"enabled": True, "target_delta": 0.25, "min_open_interest": 10,
            "max_spread_pct": 0.25, "max_spread_abs": 0.15}


def _mk_call(strike, delta, *, bid=1.15, ask=1.25, oi=800, iv=0.22,
             exp="2099-07-18", dte=35, **extra):
    return {"option_type": "call", "strike": strike, "bid": bid, "ask": ask,
            "mid": (bid + ask) / 2, "delta": delta, "iv": iv,
            "open_interest": oi, "volume": 100, "expiration": exp, "dte": dte,
            **extra}


def test_call_side_picks_mirror_call_and_computes_fields():
    put = _mk_put(95.0, -0.20)
    chain = [put, _mk_call(112.0, 0.35, iv=0.20), _mk_call(115.0, 0.26, iv=0.21),
             _mk_call(120.0, 0.15, iv=0.23)]
    fields, flags = screen.evaluate_call_side(chain, 110.0, put, CALL_CFG)
    # Nearest 0.25 delta wins; yield is the call mid on the PUT-strike
    # collateral (the shares' cost basis if assigned) — comparable to the
    # put's own annualized_yield.
    assert fields["call_yield_ann"] == pytest.approx(1.20 / 95.0 * 365 / 35)
    assert fields["skew"] == pytest.approx(0.24 - 0.21)
    assert fields["call_oi"] == 800
    assert fields["call_spread_pct"] == pytest.approx(0.10 / 1.20)
    assert fields["thin_call_side"] is False
    assert flags == []


def test_call_side_thin_by_low_oi():
    put = _mk_put(95.0, -0.20)
    fields, flags = screen.evaluate_call_side(
        [put, _mk_call(115.0, 0.26, oi=3)], 110.0, put, CALL_CFG)
    assert fields["thin_call_side"] is True
    assert [e["code"] for e in flags] == ["thin_call_side"]


def test_call_side_thin_spread_needs_both_thresholds():
    put = _mk_put(95.0, -0.20)
    # 33% spread but only $0.10 wide: rescued by max_spread_abs, not thin.
    tight_abs = _mk_call(115.0, 0.26, bid=0.25, ask=0.35)
    assert screen.evaluate_call_side([tight_abs], 110.0, put, CALL_CFG)[1] == []
    # Wide in BOTH percent and dollars: thin.
    wide = _mk_call(115.0, 0.26, bid=0.80, ask=1.60)
    fields, flags = screen.evaluate_call_side([wide], 110.0, put, CALL_CFG)
    assert fields["thin_call_side"] is True
    assert [e["code"] for e in flags] == ["thin_call_side"]


def test_call_side_indicative_quote_never_thin():
    # Off-hours zeroed bids must not mint flags — no live market to judge.
    put = _mk_put(95.0, -0.20)
    indicative = _mk_call(115.0, 0.26, bid=0.80, ask=1.60,
                          quote_quality="last_price")
    fields, flags = screen.evaluate_call_side([indicative], 110.0, put, CALL_CFG)
    assert fields["thin_call_side"] is False
    assert fields["call_spread_pct"] is None
    assert flags == []


def test_call_side_missing_oi_never_thin():
    put = _mk_put(95.0, -0.20)
    fields, flags = screen.evaluate_call_side(
        [_mk_call(115.0, 0.26, oi=None)], 110.0, put, CALL_CFG)
    assert fields["thin_call_side"] is False
    assert flags == []


def test_call_side_no_calls_or_wrong_expiry_yields_nulls():
    put = _mk_put(95.0, -0.20)
    for chain in ([], [put], [_mk_call(115.0, 0.26, exp="2099-08-15", dte=63)]):
        fields, flags = screen.evaluate_call_side(chain, 110.0, put, CALL_CFG)
        assert fields == {"call_yield_ann": None, "skew": None, "call_oi": None,
                          "call_spread_pct": None, "thin_call_side": False}
        assert flags == []


def test_call_side_bs_fallback_delta():
    # No greeks feed (delta None): the BS call-delta fallback still finds the
    # ~0.25-delta strike from iv/spot/strike/dte.
    put = _mk_put(95.0, -0.20)
    far = _mk_call(140.0, None, iv=0.24)
    near = _mk_call(115.0, None, iv=0.24)
    fields, _ = screen.evaluate_call_side([far, near], 110.0, put, CALL_CFG)
    assert fields["call_oi"] == 800
    assert fields["call_yield_ann"] == pytest.approx(1.20 / 95.0 * 365 / 35)


def test_attach_call_side_annotates_selected_and_fallback(config):
    chain = [_mk_put(95.0, -0.20), _mk_put(97.0, -0.27, oi=5),
             _mk_call(115.0, 0.26, oi=3)]
    res = screen.evaluate_puts(chain, 110.0, target_delta=0.20, delta_min=0.15,
                               delta_max=0.30, quality=config["quality"],
                               next_earnings="2099-12-31")
    out = screen.attach_call_side(res, chain, 110.0, CALL_CFG)
    for key in ("selected", "fallback"):
        assert out[key]["thin_call_side"] is True
        assert [e["code"] for e in out[key]["flags"]].count("thin_call_side") == 1
    # Copy-not-mutate: the original result rows are untouched.
    assert "thin_call_side" not in res["selected"]


def test_attach_call_side_disabled_or_absent_is_noop(config):
    chain = [_mk_put(95.0, -0.20), _mk_call(115.0, 0.26)]
    res = screen.evaluate_puts(chain, 110.0, target_delta=0.20, delta_min=0.15,
                               delta_max=0.30, quality=config["quality"],
                               next_earnings="2099-12-31")
    assert screen.attach_call_side(res, chain, 110.0, None) is res
    assert screen.attach_call_side(res, chain, 110.0,
                                   {**CALL_CFG, "enabled": False}) is res
