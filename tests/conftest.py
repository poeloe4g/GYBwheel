import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def chain():
    """A normalized chain with explicit deltas for selector-logic tests.

    yfinance supplies no Greeks, but the selector consumes normalized option
    dicts and accepts a `delta` field; using explicit deltas keeps these tests
    deterministic. The no-greeks Black-Scholes fallback path has its own test
    (test_screen.test_selector_uses_bs_fallback_when_no_greeks).
    """
    from data import dte_for

    exp = "2099-07-18"

    def mk(symbol, strike, bid, ask, oi, vol, delta, iv, option_type="put"):
        return {
            "symbol": symbol, "option_type": option_type, "strike": strike,
            "bid": bid, "ask": ask, "mid": (bid + ask) / 2,
            "delta": delta, "iv": iv, "open_interest": oi, "volume": vol,
            "expiration": exp, "dte": dte_for(exp),
        }

    return [
        mk("XYZ250718P00090000", 90.0, 0.40, 0.50, 1200, 300, -0.10, 0.22),
        mk("XYZ250718P00095000", 95.0, 0.95, 1.05, 2500, 800, -0.20, 0.24),
        mk("XYZ250718P00097000", 97.0, 1.40, 1.55, 1800, 600, -0.27, 0.25),
        mk("XYZ250718P00100000", 100.0, 2.20, 2.40, 3000, 1500, -0.40, 0.27),
        mk("XYZ250718C00100000", 100.0, 2.10, 2.30, 2000, 900, 0.55, 0.26, "call"),
    ]


@pytest.fixture
def fundamentals():
    return json.loads((FIXTURES / "yf_fundamentals.json").read_text())


@pytest.fixture
def config():
    from config import load_config

    return load_config(ROOT / "config.yaml")
