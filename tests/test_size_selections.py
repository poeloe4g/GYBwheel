"""load_positions merge of positions.yaml + dashboard selections.json."""
import json

import size


def _sel(ticker="ACN", sector="Technology", collateral=12500.0, status="OPEN", **extra):
    return {"uid": f"2026-07-15|{ticker}|2026-08-21|125.0|2026-07-16T14:03:22Z",
            "ticker": ticker, "sector": sector, "collateral": collateral,
            "status": status, **extra}


def _write_selections(tmp_path, *selections):
    p = tmp_path / "selections.json"
    p.write_text(json.dumps({"schema_version": 1, "selections": list(selections),
                             "summary": None}))
    return p


def _write_yaml(tmp_path):
    p = tmp_path / "positions.yaml"
    p.write_text(
        "positions:\n"
        "  - ticker: KO\n    sector: Consumer Staples\n    collateral: 6000\n"
    )
    return p


def test_selections_only(tmp_path):
    sel_path = _write_selections(tmp_path, _sel(), _sel(ticker="KO",
                                                       sector="Consumer Staples",
                                                       collateral=6000.0))
    account = size.load_positions(tmp_path / "nope.yaml", sel_path)
    assert account.positions_loaded is True
    assert account.total_deployed == 18500.0
    assert account.per_ticker == {"ACN": 12500.0, "KO": 6000.0}
    assert account.per_sector == {"Technology": 12500.0, "Consumer Staples": 6000.0}
    assert account.source == "2 open selections"


def test_yaml_and_selections_merge(tmp_path):
    yaml_path = _write_yaml(tmp_path)
    sel_path = _write_selections(tmp_path, _sel(ticker="KO", sector="Consumer Staples",
                                                collateral=4000.0))
    account = size.load_positions(yaml_path, sel_path)
    # Same ticker in both sources sums (they represent distinct positions).
    assert account.total_deployed == 10000.0
    assert account.per_ticker == {"KO": 10000.0}
    assert account.per_sector == {"Consumer Staples": 10000.0}
    assert account.source == f"{yaml_path} + 1 open selection"


def test_only_open_selections_count(tmp_path):
    sel_path = _write_selections(
        tmp_path,
        _sel(status="OPEN"),
        _sel(ticker="KO", status="EXPIRED_WIN"),
        _sel(ticker="PEP", status="ASSIGNED"),
        _sel(ticker="WMT", status="EARLY_CLOSED"),
    )
    account = size.load_positions(tmp_path / "nope.yaml", sel_path)
    assert account.total_deployed == 12500.0
    assert list(account.per_ticker) == ["ACN"]


def test_empty_selections_file_stays_greenfield(tmp_path):
    sel_path = _write_selections(tmp_path)
    account = size.load_positions(tmp_path / "nope.yaml", sel_path)
    assert account.positions_loaded is False
    assert "greenfield" in account.source


def test_malformed_selections_file_ignored(tmp_path):
    p = tmp_path / "selections.json"
    p.write_text("{not json")
    account = size.load_positions(tmp_path / "nope.yaml", p)
    assert account.positions_loaded is False
    assert account.total_deployed == 0.0


def test_malformed_entry_skipped_others_kept(tmp_path):
    sel_path = _write_selections(tmp_path, {"status": "OPEN"},  # no ticker/collateral
                                 _sel(collateral="oops"), _sel())
    account = size.load_positions(tmp_path / "nope.yaml", sel_path)
    assert account.total_deployed == 12500.0
    assert account.source == "1 open selection"


def test_both_absent_is_greenfield(tmp_path):
    account = size.load_positions(tmp_path / "nope.yaml", tmp_path / "nope.json")
    assert account.positions_loaded is False
    assert account.total_deployed == 0.0
    assert "greenfield" in account.source
    assert account.total_capital_override is None
    assert account.deployed_positions == 0.0


def _write_doc(tmp_path, doc):
    p = tmp_path / "selections.json"
    p.write_text(json.dumps(doc))
    return p


def test_capital_override_parsed_from_account_block(tmp_path):
    p = _write_doc(tmp_path, {
        "schema_version": 2,
        "account": {"total_capital": 62000,
                    "updated_at": "2026-07-17T14:05:00Z",
                    "history": [{"total_capital": 62000,
                                 "changed_at": "2026-07-17T14:05:00Z",
                                 "note": "deposit"}]},
        "selections": [_sel()], "summary": None})
    account = size.load_positions(tmp_path / "nope.yaml", p)
    assert account.total_capital_override == 62000.0
    assert account.positions_loaded is True
    assert account.source == "1 open selection + capital override $62,000"


def test_capital_override_alone_counts_as_loaded(tmp_path):
    p = _write_doc(tmp_path, {"schema_version": 2,
                              "account": {"total_capital": 30000},
                              "selections": [], "summary": None})
    account = size.load_positions(tmp_path / "nope.yaml", p)
    assert account.total_capital_override == 30000.0
    assert account.positions_loaded is True
    assert account.source == "capital override $30,000"


def test_capital_override_absent_is_none(tmp_path):
    sel_path = _write_selections(tmp_path, _sel())
    account = size.load_positions(tmp_path / "nope.yaml", sel_path)
    assert account.total_capital_override is None


def test_capital_override_invalid_values_ignored(tmp_path):
    # A bad override must never fail or distort the run — warn and fall back.
    for bad in ("oops", -5000, 0, None, [50000]):
        p = _write_doc(tmp_path, {"schema_version": 2,
                                  "account": {"total_capital": bad},
                                  "selections": [_sel()], "summary": None})
        account = size.load_positions(tmp_path / "nope.yaml", p)
        assert account.total_capital_override is None, bad
        assert account.total_deployed == 12500.0  # selections still counted
        assert account.source == "1 open selection"


def test_deployed_positions_split(tmp_path):
    yaml_path = _write_yaml(tmp_path)                       # 6000 from yaml
    sel_path = _write_selections(tmp_path, _sel())          # 12500 OPEN pick
    account = size.load_positions(yaml_path, sel_path)
    assert account.deployed_positions == 6000.0
    assert account.total_deployed == 18500.0
