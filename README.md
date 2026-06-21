# GYBwheel — BORING Wheel Screener

A conservative cash-secured-put (CSP) candidate screener that mechanically
enforces "boring wheel" discipline: mega-cap quality universe, target-delta put
selection, earnings avoidance, account-aware sizing, a regime traffic light, and
a ranked report. **Paper-only — it never places trades.**

Built from [`docs/feature-plan.md`](docs/feature-plan.md) and
[`docs/wheel-screener-plan.md`](docs/wheel-screener-plan.md).

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # add your Tradier token
cp positions.example.yaml positions.yaml   # optional: your open positions

python main.py --help
python main.py --tickers AAPL,MSFT,KO -v   # screen a few names -> candidates.csv
```

Missing `TRADIER_TOKEN` produces a clear, actionable error. Secrets live in
`.env` (gitignored); all thresholds live in `config.yaml`.

## Pipeline

`regime → positions → universe → select → filter → size → score → report`

1. **Regime light** (`regime.py`) — counts `spy_below_200dma`,
   `breadth_below_floor`, `vix_high_and_spy_falling` → 0 GREEN / 1 YELLOW /
   ≥2 RED. RED short-circuits the run (manage-only).
2. **Positions** (`size.py`) — loads `positions.yaml` for current deployed
   capital; absent ⇒ greenfield, stated in the header.
3. **Universe** (`universe.py`) — 1.1 fundamental filters + ban/allow list,
   cached weekly.
4. **Select** (`screen.py`) — nearest-`target_delta` put in the DTE window
   (`abs(delta)`; Black-Scholes fallback when greeks are absent).
5. **Filters** (`screen.py`) — earnings avoidance + yield/implied-move/spread/
   OI/distance quality gates, each with a logged rejection reason.
6. **Size** (`size.py`) — collateral/ROC/annualized; per-name, per-sector,
   total-deployed caps. Over-cap names are flagged (`breaches_per_name_cap`)
   with `min_account_for_1_contract`, never silently dropped.
7. **Score** (`score.py`) — `annualized_yield × distance ÷ max(implied_move,
   floor)`, or `annualized_yield_only`; components always exposed.
8. **Report** (`report.py`) — header + ranked table to console and CSV.

## Modules

| File | Role |
|---|---|
| `config.py` / `config.yaml` / `.env` | thresholds + secrets loading (B6) |
| `cache.py` | on-disk cache keyed by date (B7) |
| `data.py` | abstracted, cached, retrying data layer (Tradier + yfinance) |
| `formulas.py` | pinned-down, unit-tested formulas (B5) |
| `screen.py` `universe.py` `regime.py` `size.py` `score.py` `report.py` | pipeline stages |
| `main.py` | orchestration + CLI (`--paper` default) |
| `tests/` | offline fixture-based tests (no live network) |

## Testing

```bash
pytest -q     # 43 tests, fully offline
```

## Notes / scope

- Tradier **sandbox** data is 15-min delayed — fine for an evening
  "next-day candidates" run. A funded account gives real-time.
- yfinance is rate-limit-fragile; mitigated with caching + backoff + a weekly
  static universe.
- Covered-call / assignment logic is intentionally manual (out of scope for v1).
- Notion/Telegram push are optional stubs in `report.py`.
