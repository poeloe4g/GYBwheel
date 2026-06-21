"""Config & secrets loading (F02 / B6).

Thresholds come from config.yaml. Secrets come from the environment / .env.
No secret is ever read from config.yaml.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:  # python-dotenv is optional at import time; required for .env files
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = ROOT / "config.yaml"


class ConfigError(RuntimeError):
    """Raised for missing/invalid configuration or secrets."""


@dataclass
class Secrets:
    tradier_token: str
    tradier_env: str = "sandbox"
    fmp_api_key: str | None = None

    @property
    def tradier_base_url(self) -> str:
        if self.tradier_env.lower() == "production":
            return "https://api.tradier.com/v1"
        return "https://sandbox.tradier.com/v1"


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Load thresholds from config.yaml. Raises if the file is missing/empty."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        raise ConfigError(f"Config file not found: {cfg_path}")
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not data:
        raise ConfigError(f"Config file is empty: {cfg_path}")
    _assert_no_secrets(data)
    return data


def load_secrets(env_path: str | os.PathLike[str] | None = None) -> Secrets:
    """Load secrets from .env / environment. Missing TRADIER_TOKEN is fatal."""
    if load_dotenv is not None:
        # load_dotenv is a no-op if the file doesn't exist.
        load_dotenv(env_path or (ROOT / ".env"))

    token = (os.environ.get("TRADIER_TOKEN") or "").strip()
    if not token:
        raise ConfigError(
            "TRADIER_TOKEN is missing or empty. Copy .env.example to .env and set "
            "your Tradier token (https://developer.tradier.com/), or export "
            "TRADIER_TOKEN in your environment."
        )
    return Secrets(
        tradier_token=token,
        tradier_env=(os.environ.get("TRADIER_ENV") or "sandbox").strip(),
        fmp_api_key=(os.environ.get("FMP_API_KEY") or "").strip() or None,
    )


# Keys that look like secrets and must never appear in config.yaml.
_SECRET_MARKERS = ("token", "secret", "api_key", "apikey", "password")


def _assert_no_secrets(node: Any, path: str = "") -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SECRET_MARKERS):
                raise ConfigError(
                    f"Secret-looking key '{path}{key}' found in config.yaml. "
                    "Secrets belong in .env, not config.yaml (B6)."
                )
            _assert_no_secrets(value, f"{path}{key}.")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _assert_no_secrets(item, f"{path}{i}.")
