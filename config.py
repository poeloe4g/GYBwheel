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
    fmp_api_key: str | None = None


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
    """Load optional secrets from .env / environment.

    Option-chain data now comes from yfinance, which needs no credentials, so no
    secret is required. ``FMP_API_KEY`` remains optional for enhanced fundamentals.
    """
    if load_dotenv is not None:
        # load_dotenv is a no-op if the file doesn't exist.
        load_dotenv(env_path or (ROOT / ".env"))

    return Secrets(
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
