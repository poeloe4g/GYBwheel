import math

import formulas


def test_mid_and_spread():
    assert formulas.mid(0.95, 1.05) == 1.0
    assert math.isclose(formulas.spread_pct(0.95, 1.05), 0.10, rel_tol=1e-9)


def test_collateral_roc_annualized():
    # $100 strike, $2.20 premium, 35 DTE
    assert formulas.collateral(100.0) == 10000.0
    assert math.isclose(formulas.roc(2.20, 100.0), 0.022, rel_tol=1e-9)
    assert math.isclose(
        formulas.annualized_yield(2.20, 100.0, 35), 0.022 * (365 / 35), rel_tol=1e-9
    )
    assert math.isclose(formulas.yield_30dte(2.20, 100.0, 35), 0.022 * (30 / 35), rel_tol=1e-9)


def test_implied_move():
    # IV 0.24, 35 DTE -> 0.24 * sqrt(35/365)
    assert math.isclose(formulas.implied_move(0.24, 35), 0.24 * math.sqrt(35 / 365), rel_tol=1e-9)
    assert formulas.implied_move(0.24, 0) == 0.0


def test_distance_to_strike():
    assert math.isclose(formulas.distance_to_strike(100.0, 95.0), 0.05, rel_tol=1e-9)


def test_bs_put_delta_sign_and_tolerance():
    # Matches the greeks-feed delta (-0.20) within tolerance on the fixture put.
    d = formulas.bs_put_delta(spot=100.0, strike=95.0, dte=35, iv=0.24, r=0.04)
    assert d is not None
    assert d < 0  # puts are negative
    assert abs(abs(d) - 0.20) < 0.05


def test_bs_put_delta_missing_inputs():
    assert formulas.bs_put_delta(0, 95, 35, 0.24) is None
    assert formulas.bs_put_delta(100, 95, 0, 0.24) is None
