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

## Dashboard (GitHub Actions → GitHub Pages)

The screener can run unattended in CI and publish its results — recommendations,
analysis, and graphs — to a static webpage.

- **Compute:** `.github/workflows/screen.yml` runs the screener on a weekday-evening
  cron (and on-demand via *Run workflow*), then commits a dated JSON snapshot.
- **View:** a no-build static site in `site/` (Chart.js via CDN) reads the JSON and
  renders the regime banner, capital summary, a ranked candidates table, per-run
  charts (top scores, yield-vs-distance, sector allocation, deployment gauge), and
  history trends (regime, score, candidate count, % deployed over time).

`main.py --json-out PATH` writes one self-contained run snapshot (regime, header,
thresholds, full candidate rows). `scripts/build_index.py` rebuilds
`site/data/index.json` + `latest.json` from `site/data/runs/*.json`.

### One-time repo setup
1. **Settings → Pages → Source = "GitHub Actions".**
2. **Settings → Secrets and variables → Actions:** add `TRADIER_TOKEN` (required;
   optionally `TRADIER_ENV`, `FMP_API_KEY`).
3. **Settings → Actions → General → Workflow permissions = "Read and write".**

Breadth (the heavy full-S&P yfinance loop) is intentionally **off** in CI — it's
the most rate-limit-fragile step; the regime breadth signal degrades to N/A.

### Preview locally
```bash
python scripts/seed_demo.py        # one-time: clearly-marked DEMO snapshots
cd site && python -m http.server   # open http://localhost:8000
# or with live data:
python main.py --tickers AAPL,MSFT,KO --json-out site/data/runs/$(date +%F).json
python scripts/build_index.py
```

## Testing

```bash
pytest -q     # offline tests (pipeline, formulas, JSON export, index builder)
```

## Notes / scope

- Tradier **sandbox** data is 15-min delayed — fine for an evening
  "next-day candidates" run. A funded account gives real-time.
- yfinance is rate-limit-fragile; mitigated with caching + backoff + a weekly
  static universe.
- Covered-call / assignment logic is intentionally manual (out of scope for v1).
- Notion/Telegram push are optional stubs in `report.py`.
