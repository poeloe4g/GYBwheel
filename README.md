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

cp positions.example.yaml positions.yaml   # optional: your open positions

python main.py --help
python main.py --tickers AAPL,MSFT,KO -v   # screen a few names -> candidates.csv
```

Option-chain and fundamentals data come from yfinance, which needs no
credentials, so no API key is required to run. All thresholds live in
`config.yaml`; if a keyed data source is ever added, its secret goes in `.env`
(gitignored — see `.env.example`), never in `config.yaml`.

## Pipeline

`regime → positions → universe → select → filter → size → score → report`

1. **Regime light** (`regime.py`) — counts `spy_below_200dma`,
   `breadth_below_floor`, `vix_high_and_spy_falling` → 0 GREEN / 1 YELLOW /
   ≥2 RED. RED short-circuits the run (manage-only).
2. **Positions** (`size.py`) — loads `positions.yaml` for current deployed
   capital; absent ⇒ greenfield, stated in the header.
3. **Universe** (`universe.py`) — 1.1 fundamental filters + ban/allow list,
   cached weekly.
4. **Select + filter** (`screen.evaluate_puts`) — every put in the delta band
   (`abs(delta)`; Black-Scholes fallback when greeks are absent) is gated
   first — earnings avoidance against each contract's own expiration, plus
   yield/implied-move/spread/OI/distance quality gates — then the qualifying
   contract nearest `target_delta` wins, so one illiquid strike no longer
   rejects a ticker whose neighbors pass. Every rejection is logged with a
   reason. Off-hours zeroed/crossed quotes degrade to last-trade mids
   (`quote_indicative` flag, spread gate skipped); junk per-contract IVs get an
   `iv_outlier` flag instead of a bogus implied-move rejection. A clean row
   whose only flag is `earnings_unknown` reaches the main table (visibly
   flagged) under `quality.unknown_earnings_policy: flag`. The call side of
   the same chain (fetched in the same request) feeds wheel-second-leg
   context — `call_yield_ann`, put-call `skew`, mirror-call liquidity, and an
   advisory `thin_call_side` flag that is **never** a gate: it cannot demote a
   candidate or shrink the output (`call_side` section in `config.yaml`).
5. **Size** (`size.py`) — collateral/ROC/annualized; per-name, per-sector,
   total-deployed caps. Over-cap names are flagged (`breaches_per_name_cap`,
   `affordable: false`) with `min_account_for_1_contract`, never silently
   dropped; `account.require_affordable: true` demotes them instead. Smaller
   accounts can screen `data/universe_affordable.txt` via `--tickers-file`.
6. **Score** (`score.py`) — default `risk_adjusted`: `annualized_yield ×
   (1 − |Δ|) × distance ÷ max(implied_move, floor)` — expected yield (premium
   weighted by the probability of keeping it) per unit of cushion-adjusted
   risk. `blended` (no probability weight) and `annualized_yield_only` remain
   available; components (`pop`, `iv_used`, `implied_move`) always exposed.
   Yields are computed from a conservative fill (`scoring.premium_basis:
   conservative` = halfway between bid and mid) rather than the optimistic raw
   mid; junk per-contract IVs fall back to the in-band median for the implied
   move. `scoring.prefer_affordable` ranks tradeable candidates first;
   `prefer_live_quotes` ranks live-quote rows above last-trade-priced ones;
   `prefer_two_sided` (weakest tier) ranks `thin_call_side` rows last — a
   sanity check on the wheel's covered-call leg, never a score change.
7. **Report** (`report.py`) — header + ranked table to console and CSV.
8. **Outcomes** (`scripts/evaluate_outcomes.py`, run by CI) — once contracts
   expire, candidates *and* near-misses are scored (win = expired above
   strike; realized ROC approximates assignment at the expiry close) into
   `site/data/outcomes.json`. Win-rate by rejection reason is the calibration
   signal: a gate whose rejects win as often as the candidates is too tight.

## Modules

| File | Role |
|---|---|
| `config.py` / `config.yaml` / `.env` | thresholds + secrets loading (B6) |
| `cache.py` | on-disk cache keyed by date (B7) |
| `data.py` | abstracted, cached, retrying data layer (yfinance) |
| `formulas.py` | pinned-down, unit-tested formulas (B5) |
| `screen.py` `universe.py` `regime.py` `size.py` `score.py` `report.py` | pipeline stages |
| `main.py` | orchestration + CLI (`--paper` default) |
| `tests/` | offline fixture-based tests (no live network) |

## Dashboard (GitHub Actions → GitHub Pages)

**Live at <https://poeloe4g.github.io/GYBwheel/>.**

The screener runs unattended in CI and publishes its results — recommendations,
analysis, and graphs — to a static webpage.

- **Compute:** `.github/workflows/screen.yml` runs the screener on a weekday
  market-hours cron (17:30 UTC — early enough that GitHub's routinely-late
  scheduler plus the run itself still finish before the 20:00 UTC close; runs
  are stamped `quotes_trusted` only when both the start *and* end of the run
  fall in regular hours), then commits a dated JSON snapshot. CI alerts (as
  GitHub issues) fire on workflow failure, on a 3+-run zero-candidate streak,
  and on a 3+-run off-hours (`quotes_trusted: false`) streak.
- **View:** a no-build static site in `site/` (Chart.js via CDN) reads the JSON and
  renders the regime banner, capital summary (incl. a "Tradeable: N of M" card
  and the B1 capital warning), a ranked candidates table (flag badges), a
  near-miss table (sized/scored rows that failed a gate, with reason badges),
  per-run charts (top scores, yield-vs-distance, sector allocation, deployment
  gauge, rejections by reason), an Outcomes section (win-rate cards, win-rate
  by rejection reason, recent resolutions — appears once contracts expire), and
  history trends (regime, score, candidate + near-miss counts, % deployed over
  time). A freshness badge appears when the latest run is over a day (yellow)
  or four days (red STALE) old, and an OFF-HOURS DATA badge marks runs executed
  outside regular market hours (`meta.quotes_trusted: false` — such runs are
  also excluded from the zero-candidate alert streak).

The site opens with a plain-English "What is this?" guide and a "Today's top
idea" sentence card; table headers, badges, and rejection reasons are rendered
in lay terms (the machine codes stay in hover tooltips).

- **Track (My picks):** with a fine-grained GitHub token (Contents read/write on
  this repo only), the dashboard writes `site/data/selections.json` directly.
  You can select ideas, see all open picks (with an "If it ended today"
  estimate from the last screened price), close a pick early with your actual
  buyback price, **correct a recorded close** afterwards (price or date — an ✎
  marker shows corrected rows), and **override an auto-graded expiry** (the
  entered settlement price re-derives kept-the-cash vs own-the-shares;
  `close.method` becomes `manual_expiry` and the nightly grader never
  re-touches terminal entries). Open picks count against your cash limits on
  the next screener run.
- **Adjust capital:** "Adjust capital…" (My picks) stores your total capital in
  the selections file's `account` block with an append-only change history
  (amount, timestamp, optional note — git history doubles as the audit trail).
  The dashboard's capital cards update immediately (marked "(live)"); the
  screener sizes with the override from its next run, falling back to
  `config.yaml` when no override is set. Invalid values are ignored with a
  warning — a bad override never breaks a run.

`main.py --json-out PATH` writes one self-contained run snapshot (schema v4 —
versions are additive and readers never gate on the number; the field list is
documented in `report.py`). `scripts/build_index.py` rebuilds
`site/data/index.json` + `latest.json` from `site/data/runs/*.json`;
`scripts/evaluate_outcomes.py` appends resolved contracts to
`site/data/outcomes.json` idempotently.

### One-time repo setup
1. **Settings → Pages → Source = "GitHub Actions".**
2. **Settings → Actions → General → Workflow permissions = "Read and write".**

No Actions secrets are required — all data comes from yfinance.

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

- Option chains come from yfinance (Yahoo). yfinance has no Greeks feed, so put
  delta is computed via Black-Scholes (`py_vollib`) from IV/spot/strike/DTE —
  fine for an evening "next-day candidates" screen.
- yfinance is rate-limit-fragile; mitigated with caching + backoff + a weekly
  static universe.
- Covered-call / assignment logic is intentionally manual (out of scope for v1).
- Notion/Telegram push are optional stubs in `report.py`.
