import size


def test_b1_breach_flag_and_min_account(config):
    # $100 strike -> $10k collateral; per-name cap = 5% * 50k = $2,500.
    account = size.AccountState()  # greenfield
    cand = {"ticker": "BIG", "sector": "Technology", "strike": 100.0, "mid": 2.2, "dte": 35}
    sized = size.size_candidate(cand, account, config)
    assert sized["breaches_per_name_cap"] is True
    assert sized["min_account_for_1_contract"] == 200000.0  # 10000 / 0.05
    assert sized["max_contracts"] == 0


def test_affordable_name_within_cap(config):
    account = size.AccountState()
    cand = {"ticker": "SMALL", "sector": "Industrials", "strike": 20.0, "mid": 0.5, "dte": 35}
    sized = size.size_candidate(cand, account, config)
    assert sized["breaches_per_name_cap"] is False  # 2000 <= 2500
    assert sized["max_contracts"] == 1  # floor(2500 / 2000)


def test_sector_and_total_headroom(config):
    # Existing $11k deployed in Technology eats most of the 25% sector cap ($12.5k).
    account = size.AccountState(
        total_deployed=11000.0,
        per_sector={"Technology": 11000.0},
        per_ticker={"AAA": 11000.0},
        positions_loaded=True,
        source="test",
    )
    cand = {"ticker": "BBB", "sector": "Technology", "strike": 10.0, "mid": 0.3, "dte": 35}
    sized = size.size_candidate(cand, account, config)
    # sector headroom = 12500 - 11000 = 1500 -> floor(1500/1000) = 1
    assert sized["sector_headroom"] == 1500.0
    assert sized["max_contracts"] == 1


def test_load_positions_absent_is_greenfield(tmp_path):
    account = size.load_positions(tmp_path / "nope.yaml")
    assert account.positions_loaded is False
    assert account.total_deployed == 0.0
    assert "greenfield" in account.source


def test_load_positions_from_example():
    account = size.load_positions("positions.example.yaml")
    assert account.positions_loaded is True
    assert account.total_deployed == 22500.0  # 6000 + 16500
    assert account.per_sector["Consumer Staples"] == 22500.0


def test_capital_sanity_warns(config):
    warn = size.sanity_check_capital([100.0, 200.0, 300.0], config)
    assert warn is not None and "small" in warn
    assert size.sanity_check_capital([10.0, 12.0], config) is None
