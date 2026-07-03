# BORING Wheel Screener — Structured Feature Plan

A structured, trackable breakdown of the build into discrete features. Derived from the
7 build slices and the B-series fixes in [`wheel-screener-plan.md`](./wheel-screener-plan.md).

> **Superseded note (2026-07):** the Tradier option-chain source described in this historical document was replaced by yfinance (no `TRADIER_TOKEN` or any credential is needed); the optional FMP fundamentals key was likewise removed as it was never wired up. References to Tradier/FMP/`TRADIER_TOKEN` below are kept for historical context only.

**Legend**
- **Status:** `TODO` · `IN PROGRESS` · `BLOCKED` · `DONE`
- **Priority:** `P0` (must-have for a usable v1) · `P1` (important) · `P2` (nice-to-have)
- **Size:** `S` (<½ day) · `M` (~1 day) · `L` (multi-day)

---

## Milestone overview

| Milestone | Features | Goal |
|---|---|---|
| **M0 — Scaffolding** | F01, F02 | Project boots, config + secrets load, deps pinned |
| **M1 — Data layer** | F03, F04 | Cached, hardened access to fundamentals + chains |
| **M2 — Screening core** | F05, F06, F07 | Universe → contract selection → quality filters |
| **M3 — Sizing & regime** | F08, F09 | Account-aware sizing + GREEN/YELLOW/RED light |
| **M4 — Output** | F10, F11 | Scoring/rank + CSV/console report |
| **M5 — Orchestration** | F12, F13 | `main.py` pipeline + test suite |

---

## Feature backlog

### F01 — Project scaffolding & dependencies
- **Milestone:** M0 · **Priority:** P0 · **Size:** S · **Status:** DONE
- **Depends on:** —
- **Description:** Python project skeleton with the module layout from the spec
  (`config.yaml`, `universe.py`, `data.py`, `regime.py`, `screen.py`, `size.py`,
  `score.py`, `report.py`, `main.py`), `requirements.txt`/`pyproject`, and a `tests/` dir.
- **Acceptance criteria:**
  - [x] Repo installs cleanly into a fresh venv.
  - [x] `python main.py --help` runs and prints usage (even if no logic yet).
  - [x] Pinned deps: `pandas`, `requests`, `pyyaml`, `python-dotenv`, `py_vollib` (fallback), `pytest`.

### F02 — Config & secrets loading (B6)
- **Milestone:** M0 · **Priority:** P0 · **Size:** S · **Status:** DONE
- **Depends on:** F01
- **Description:** Load all thresholds from `config.yaml`; load `TRADIER_TOKEN` (+ optional FMP key)
  from `.env`/environment. Ship `.env.example`; add `.env` to `.gitignore`.
- **Acceptance criteria:**
  - [x] `config.yaml` includes the corrected fields (`avoid_earnings_before_expiry`,
        `risk_free_rate`, `score_denominator_floor`, `scoring.mode`, `data.universe_refresh_days`,
        `data.cache_dir`).
  - [x] Missing/empty `TRADIER_TOKEN` raises a clear, actionable error.
  - [x] No secret ever read from `config.yaml`.

### F03 — Abstracted, cached data layer (B7)
- **Milestone:** M1 · **Priority:** P0 · **Size:** L · **Status:** DONE
- **Depends on:** F02
- **Description:** `data.py` as the single source-swappable interface: `get_fundamentals`,
  `get_price_history`, `get_option_chain(greeks=True)`, `get_nearest_delta_put`,
  `get_next_earnings`, `get_breadth`, `get_vix`. On-disk cache keyed by date; exponential
  backoff (2s/4s/8s/16s) on transient/429 errors.
- **Acceptance criteria:**
  - [x] Tradier chain calls request `greeks=true` and parse delta/IV.
  - [x] yfinance calls are cached per-day and retried with backoff on 429.
  - [x] Cache hit avoids any network call; cache dir is configurable.

### F04 — Nearest-delta put selector (B5)
- **Milestone:** M1 · **Priority:** P0 · **Size:** M · **Status:** DONE
- **Depends on:** F03
- **Description:** Given a ticker + target DTE window, return the put whose `abs(delta)` is nearest
  `target_delta` within `[delta_min, delta_max]`. Fallback to `py_vollib` BS delta if greeks absent.
- **Acceptance criteria:**
  - [x] Negative put deltas handled via `abs(delta)`.
  - [x] Picks the expiry inside `[dte_min, dte_max]`; returns `None` if none qualify.
  - [x] BS fallback matches greeks-feed delta within tolerance on a fixture.

### F05 — Universe builder & ban list (Spec 1.1)
- **Milestone:** M2 · **Priority:** P0 · **Size:** M · **Status:** DONE
- **Depends on:** F03
- **Description:** Apply the 1.1 filters (market cap, profitable, positive FCF, min volume, has
  options, sector tagged), drop the ban list, honor optional allow list. Refresh weekly cache (B7).
- **Acceptance criteria:**
  - [x] A known mega-cap passes; an unprofitable/banned name is dropped with a logged reason.
  - [x] Universe cache reused within `universe_refresh_days`.

### F06 — Earnings filter (B2)
- **Milestone:** M2 · **Priority:** P0 · **Size:** S · **Status:** DONE
- **Depends on:** F04
- **Description:** Reject any contract whose expiry falls after the next earnings date when
  `avoid_earnings_before_expiry` is true. Cached earnings dates.
- **Acceptance criteria:**
  - [x] A contract spanning earnings is rejected (toggle off → accepted).
  - [x] Missing earnings data degrades gracefully (warn, don't crash).

### F07 — Trade-quality filters (Spec 1.3, B5)
- **Milestone:** M2 · **Priority:** P0 · **Size:** M · **Status:** DONE
- **Depends on:** F04
- **Description:** Apply min yield/30DTE, max implied move (`IV×sqrt(DTE/365)`), max spread%,
  min open interest, min distance-to-strike. Each rejection records a reason.
- **Acceptance criteria:**
  - [x] Each filter rejects a crafted failing fixture and passes a good one.
  - [x] Implied-move and yield formulas match hand-computed values (unit test).

### F08 — Account-aware sizing (B1, B3, Spec 1.4)
- **Milestone:** M3 · **Priority:** P0 · **Size:** L · **Status:** DONE
- **Depends on:** F07
- **Description:** Compute collateral/ROC/annualized; enforce per-name, per-sector, total-deployed
  caps using `positions.yaml` for current state. Instead of silently dropping over-cap names, flag
  `breaches_per_name_cap` and report `min_account_for_1_contract = collateral / max_pct_per_name`.
- **Acceptance criteria:**
  - [x] With `positions.yaml` absent, treated as empty and header says so.
  - [x] A name too big for the per-name cap is shown **with** the breach flag + min account size.
  - [x] Sector/deployed headroom respected; `max_contracts` computed correctly.

### F09 — Regime light (B8, Spec 1.5)
- **Milestone:** M3 · **Priority:** P0 · **Size:** M · **Status:** DONE
- **Depends on:** F03
- **Description:** Compute three boolean signals (`spy_below_200dma`, `breadth_below_floor`,
  `vix_high_and_spy_falling`); map count → 0 GREEN / 1 YELLOW / ≥2 RED. Log which tripped.
- **Acceptance criteria:**
  - [x] Forced inputs produce the expected light (unit tests for each case).
  - [x] RED short-circuits the pipeline (no screening, manage-only message).

### F10 — Scoring & ranking (B4, Spec 2.6)
- **Milestone:** M4 · **Priority:** P1 · **Size:** S · **Status:** DONE
- **Depends on:** F08
- **Description:** `score = annualized_yield × distance_to_strike ÷ max(implied_move, floor)`, with
  `scoring.mode` to switch to `annualized_yield_only`. Always expose the components.
- **Acceptance criteria:**
  - [x] Denominator floor prevents blow-ups on near-zero implied move.
  - [x] Output rows carry each component, not just the blended number.

### F11 — Report output (Spec 2.7)
- **Milestone:** M4 · **Priority:** P1 · **Size:** M · **Status:** DONE
- **Depends on:** F10
- **Description:** Header (regime light, total capital, % deployed, remaining cash) + ranked table
  to console and CSV. Notion/Telegram push left as optional stubs.
- **Acceptance criteria:**
  - [x] CSV columns match the spec's row format.
  - [x] Console table is readable; header reflects sizing state.

### F12 — Pipeline orchestration & `--paper` (B9, Spec 2.4)
- **Milestone:** M5 · **Priority:** P0 · **Size:** M · **Status:** DONE
- **Depends on:** F05, F06, F07, F08, F09, F10, F11
- **Description:** `main.py` runs regime → positions → universe → select → filter → size → score →
  report. `--paper` (default) only prints/writes, never trades.
- **Acceptance criteria:**
  - [x] RED regime stops before screening.
  - [x] End-to-end run on ~3 known names produces a sane CSV.

### F13 — Test suite & fixtures (Part D)
- **Milestone:** M5 · **Priority:** P0 · **Size:** M · **Status:** DONE
- **Depends on:** F12
- **Description:** Fixture-based unit tests (captured Tradier chain JSON + yfinance blob), formula
  tests, regime tests — **no live network in tests**. One manual golden-path integration check.
- **Acceptance criteria:**
  - [x] `pytest` passes offline.
  - [x] Coverage includes selector, each quality filter, the B1 breach flag, earnings filter,
        regime counting, and formula correctness.

---

## Dependency graph (build order)

```
F01 ─► F02 ─► F03 ─┬─► F04 ─┬─► F06
                   │        ├─► F07 ─► F08 ─► F10 ─► F11 ─┐
                   ├─► F05 ──────────────────────────────┤
                   └─► F09 ──────────────────────────────┤
                                                          ▼
                                                 F12 ─► F13
```

Suggested sequence: **F01 → F02 → F03 → F04 → F05 → F06 → F07 → F08 → F09 → F10 → F11 → F12 → F13**,
testing each feature against fixtures before moving on.
