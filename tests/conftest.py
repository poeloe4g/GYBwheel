import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def chain():
    from data import normalize_option

    raw = json.loads((FIXTURES / "tradier_chain.json").read_text())
    options = raw["options"]["option"]
    return [normalize_option(o, "2099-07-18") for o in options]


@pytest.fixture
def fundamentals():
    return json.loads((FIXTURES / "yf_fundamentals.json").read_text())


@pytest.fixture
def config():
    from config import load_config

    return load_config(ROOT / "config.yaml")
