# BORING Wheel Screener — Analysis, Verification & Improved Build Plan

> Status: **Planning / review** — no screener code has been written yet. This document is the
> analysis of the original spec plus a corrected build plan, for review before building.

## Context

A detailed spec was written for a "boring wheel" cash-secured-put (CSP) / covered-call screener.
This document **analyzes, verifies, and improves** that spec. The strategy logic, the conservative
defaults, and the "enforce discipline mechanically" framing are all sound — the issues below are
about **correctness, feasibility, and gaps**, not direction.

Decisions made during review:
- **Sizing contradiction:** *flag + suggest minimum account size* (keep the caps honest).
- **Data:** *free stack + hardening* (Tradier sandbox + yfinance, plus caching/retry/static universe).

---

## Part A — Verification of the spec's technical claims

| Claim in spec | Verdict | Detail |
|---|---|---|
| Tradier free sandbox gives option greeks (delta/IV) directly | ✅ True, with caveat | Greeks/IV come via ORATS and are returned in the chain. **But sandbox data is 15-min delayed**; real-time needs a *funded* Tradier brokerage account. For an evening "next-day candidates" run, 15-min delay is fine. |
| yfinance for fundamentals + prices + MAs | ⚠️ Works but fragile | yfinance scrapes Yahoo; it hits **HTTP 429 rate limits** and breaks when Yahoo changes layout. Pulling ~500 S&P names (breadth) + fundamentals for a 100+ universe **every run** is the main reliability risk. Mitigate with caching + backoff + a static cached universe. |
| Compute delta with `py_vollib` if no greeks feed | ✅ True | Black-Scholes via `py_vollib` works given price, IV, strike, DTE, r. Only a fallback — Tradier greeks are preferred. |
| Breadth = % of S&P 500 above 50-DMA, computable yourself | ✅ Feasible but heavy | 500 history pulls/run via yfinance is the heaviest call. Cache daily; consider a prebuilt breadth source later. |

**Bottom line:** the free data stack is viable *for this use case* (evening batch, not intraday).
The 15-min delay and yfinance fragility are acceptable with hardening.

Sources consulted:
- Tradier docs — Options Chains / Market Data / Sandbox FAQ (sandbox = 15-min delayed; greeks via ORATS).
- yfinance reliability discussions and PyPI notes (429 rate limits, scraping fragility); FMP as a sturdier fundamentals alternative.

---

## Part B — Substantive issues in the spec (with fixes)

### B1. Sizing math contradicts the default account (HIGH)
With `total_capital: 50000` and `max_pct_per_name: 0.05`, the per-name cap is **$2,500** of
collateral. One CSP contract = `strike × 100`. Almost every qualifying mega-cap (≥$20B,
liquid) trades well above $25/share, so **a single contract breaches the 5% cap** — COST (~$900)
needs ~$90k collateral = 180% of the whole account. The spec's own example ("$200 stock =
$20k collateral … ~14% on a $146k account") quietly assumes a six-figure account.
- **Fix:** `size.py` still computes and displays the trade, but marks
  `breaches_per_name_cap = true` and reports **`min_account_for_1_contract = collateral / max_pct_per_name`**.
  Never silently suggest a breaching trade. Add a startup sanity check that warns when
  `total_capital` is too small for the universe's typical strike prices.

### B2. No earnings-date filter (HIGH)
Selling a 30–45 DTE put across an earnings date is a binary-gap risk that the whole "boring"
thesis is meant to avoid. The spec never mentions earnings.
- **Fix:** add `quality.avoid_earnings_before_expiry: true`. Pull the next earnings date
  (yfinance `get_earnings_dates`, cached) and **reject** any contract whose expiry is after the
  next earnings report. Configurable (some traders sell earnings IV deliberately — default = avoid).

### B3. No current-portfolio input, yet caps need it (HIGH)
Per-sector (25%), total-deployed (50%), and the header's "current % deployed / remaining
deployable cash" **cannot be computed without knowing existing open positions**. The config has
no positions input.
- **Fix:** add a `positions.yaml` (or `positions.csv`): `ticker, sector, collateral, opened, expiry`.
  `size.py` loads it, computes current per-sector and total deployed, and only offers new trades
  within remaining headroom. If absent, treat as empty (greenfield) and say so in the header.

### B4. Scoring formula is dimensionally fragile (MEDIUM)
`score = annualized_yield × distance_to_strike ÷ implied_move`. `implied_move` can be ~0 for
low-IV names → blow-up/teleporting ranks; and `annualized_yield` already embeds IV, so dividing
by `implied_move` double-counts vol.
- **Fix:** guard the denominator (`max(implied_move, floor)`), and display all three components.
  Optionally offer a `score_mode` config (`blended` vs `annualized_yield_only`) so ranking is
  inspectable. Keep it simple — it's a heuristic, not alpha.

### B5. Formulas are under-specified — pin them down (MEDIUM)
Define once, in code, with tests:
- **Delta handling:** puts have negative delta; compare on `abs(delta)` against `target/min/max`.
- **Implied move (contract life):** `IV_annual × sqrt(DTE/365)` (1σ). The 8% cap is a 1σ move;
  cross-check internal consistency with `min_distance_to_strike` (a 0.20-delta strike sits ~0.8σ
  out, so ~5% distance ≈ ~6–7% implied move — consistent).
- **ROC:** `premium / collateral`. **Annualized:** `ROC × (365 / DTE)`. **Collateral:** `strike × 100`.
- **Premium/mid:** `(bid + ask) / 2`; spread% = `(ask − bid) / mid`.

### B6. Secrets in `config.yaml` (MEDIUM)
The Tradier API token must **not** live in `config.yaml` (spec'd to hold "ALL thresholds").
- **Fix:** read `TRADIER_TOKEN` (and any FMP key) from `.env` / environment via `python-dotenv`;
  keep `config.yaml` for thresholds only; add `.env` to `.gitignore` and ship `.env.example`.

### B7. Rate-limit / batching strategy missing (MEDIUM)
Universe-wide chain pulls + 500-name breadth will trip Tradier (≈120 req/min) and yfinance 429s.
- **Fix:** central `data.py` cache layer (on-disk, keyed by date) + exponential backoff + a
  **static cached universe** refreshed weekly (not every run), so daily runs only pull chains for
  names that already passed fundamentals. Tradier chains support per-expiry `greeks=true`; batch
  by expiry and reuse.

### B8. Regime VIX rule is ambiguous (LOW)
"VIX > 30 *and* SPY falling → reduce activity" isn't mapped cleanly into GREEN/YELLOW/RED.
- **Fix:** make the regime a count of three boolean signals — `spy_below_200dma`,
  `breadth_below_floor`, `vix_high_and_spy_falling` — then 0→GREEN, 1→YELLOW, ≥2→RED, exactly as
  the light table intends. Log which signals tripped.

### B9. Minor gaps (LOW)
- No **risk-free rate** source for annualization / BS fallback → add `quality.risk_free_rate` (or
  pull `^IRX`), default ~0.04.
- Covered-call / assignment logic (1.6, 3.4) is **manual-only** in the spec — keep it that way for
  v1 (screener flags, human executes); document as out-of-scope for automation.
- Add a `--paper`/dry-run flag and make it the default; the tool never places trades, only reports.

---

## Part C — Improved build plan

Keep the spec's module layout (`config.yaml`, `universe.py`, `data.py`, `regime.py`, `screen.py`,
`size.py`, `score.py`, `report.py`, `main.py`). Changes vs spec:

**Architecture additions**
- `data.py` becomes an **abstracted, cached data layer** (so a source swap — e.g. FMP later — is
  a one-file change): `get_fundamentals`, `get_price_history`, `get_option_chain(greeks=True)`,
  `get_nearest_delta_put`, `get_next_earnings`, `get_breadth`, `get_vix`. On-disk cache keyed by
  date; retry with exponential backoff.
- New `positions.yaml` input (B3) and `.env` for secrets (B6).
- New `universe_cache.json` refreshed weekly (B7).

**Corrected `config.yaml` (additions over the spec)**
```yaml
account:
  total_capital: 50000
  max_pct_per_name: 0.05
  max_pct_per_sector: 0.25
  max_pct_deployed: 0.50
quality:
  # ...existing thresholds...
  avoid_earnings_before_expiry: true   # B2
  risk_free_rate: 0.04                  # B9
  score_denominator_floor: 0.01        # B4 guard
scoring:
  mode: blended                        # blended | annualized_yield_only  (B4)
data:
  universe_refresh_days: 7             # B7
  cache_dir: .cache
```
(`.env`: `TRADIER_TOKEN=...`; `.env.example` committed.)

**Pipeline (`main.py`)** — spec order, with the fixes folded in:
1. Regime check → if RED, print light + manage-only message, stop (B8 counting).
2. Load `positions.yaml`; compute current per-sector + total deployed (B3).
3. Build/refresh universe (weekly cache) → apply 1.1 filters + ban list.
4. Per ticker: nearest-target-delta put at target DTE (abs(delta), B5).
5. Quality filters (1.3) **+ earnings filter** (B2).
6. Sizing (1.4): collateral, ROC, annualized; flag `breaches_per_name_cap` and
   `min_account_for_1_contract` instead of silently dropping (B1); respect sector/deployed headroom.
7. Score (guarded formula, components shown, B4) + rank.
8. Report: header (regime light, capital, % deployed, remaining cash) + ranked table → CSV +
   console. Notion/Telegram push left as optional stubs.

---

## Part D — Verification / testing approach (for the build)

- **Unit tests with fixtures:** save one captured Tradier chain JSON + one yfinance fundamentals
  blob as fixtures; test `get_nearest_delta_put`, all quality filters, sizing caps (esp. the B1
  breach flag), the earnings filter, and the regime counting — **no live network in tests**.
- **Formula tests:** assert ROC, annualized, implied-move, delta-sign handling against
  hand-computed values (B5).
- **Golden-path integration (manual, `--paper`):** run against ~3 known names (e.g. NVDA, a
  lower-priced qualifying name, plus one ban-list name to confirm it's dropped) and eyeball the CSV.
- **Regime sanity:** force inputs (SPY<200DMA, breadth<40%, VIX>30) and assert GREEN/YELLOW/RED.

---

## Next step
Build in the spec's 7 slices **plus** the fixes above, starting with the cached/abstracted
`data.py` and `config.yaml`/`.env` scaffolding, testing each slice against fixtures before moving on.
