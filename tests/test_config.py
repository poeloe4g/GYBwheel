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


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("TRADIER_TOKEN", raising=False)
    with pytest.raises(config_mod.ConfigError) as exc:
        config_mod.load_secrets(env_path="/nonexistent/.env")
    assert "TRADIER_TOKEN" in str(exc.value)


def test_token_loaded_from_env(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "abc123")
    secrets = config_mod.load_secrets(env_path="/nonexistent/.env")
    assert secrets.tradier_token == "abc123"
    assert "sandbox" in secrets.tradier_base_url


def test_secret_in_config_rejected(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("quality:\n  tradier_token: leaked\n")
    with pytest.raises(config_mod.ConfigError):
        config_mod.load_config(bad)


def test_no_secret_read_from_config(config):
    # The shipped config.yaml must not carry secret-looking keys.
    assert config is not None  # load_config would have raised otherwise
