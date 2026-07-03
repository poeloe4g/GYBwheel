import pytest

import config as config_mod


def test_load_config_has_corrected_fields(config):
    q = config["quality"]
    assert "avoid_earnings_before_expiry" in q
    assert "risk_free_rate" in q
    assert "score_denominator_floor" in q
    assert config["scoring"]["mode"] in ("blended", "annualized_yield_only")
    assert "universe_refresh_days" in config["data"]
    assert "cache_dir" in config["data"]


def test_load_secrets_requires_no_token():
    # All data comes from yfinance (no credentials); load must not raise.
    secrets = config_mod.load_secrets(env_path="/nonexistent/.env")
    assert secrets is not None


def test_secret_in_config_rejected(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("quality:\n  some_api_key: leaked\n")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config(bad)


def test_no_secret_read_from_config(config):
    # The shipped config.yaml must not carry secret-looking keys.
    assert config is not None  # load_config would have raised otherwise
