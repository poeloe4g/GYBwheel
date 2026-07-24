# Call-Side (Covered-Call) Quality in Put Selection — Ideation & Plan

> Status: **Ideation / for review** — no code yet. This document analyzes whether
> the screener's put selection should account for how good a target would be to
> sell covered calls on *if assigned* (the second leg of the wheel), and — since
> the answer is a qualified yes — proposes a phased integration that fits the
> repo's existing conventions (visible flags over silent drops, inspectable
> scores, outcomes-based gate calibration).

## The gap

The wheel has two legs: sell a cash-secured put; if assigned, own 100 shares
and sell covered calls at/above cost basis until called away. The screener
prices leg 1 only. A name can be a *great* CSP target but a *poor* wheel
target in three distinct ways:

1. **Thin call side.** Liquidity is not symmetric. A name can have tight,
   deep put markets (passes every current gate) while its call side at the
   strikes you'd actually sell — between the put strike and spot — is wide
   and low-OI. If assigned, you'd exit through an illiquid door.
2. **Steep put skew.** In names with rich put skew, the put premium the
   screener rewards is *not* matched by call premium at the mirror delta.
   The first leg pays well; the second leg pays badly. Skew is exactly the
   part of call-side quality that put-side metrics cannot see.
3. **Poor recoverability.** Assignment is conditional on a drop. A stock
   that gaps down and stays down forces the classic wheel trap: sell calls
   below basis (lock a loss) or hold dead money. Momentum/drawdown behavior
   of the *equity* is invisible to any option-chain metric.

## Why a naive integration would be wrong (the caveats that shaped this plan)

- **Today's call chain is a weak proxy for the post-assignment chain.**
  Assignment implies spot fell to ≤ strike, and IV at that point is usually
  higher than today's. The call you'd actually sell (struck near basis,
  30–45 DTE, ~4–8 weeks from now) does not exist in today's data. Anything
  we measure now is a *structural* signal (is the call side liquid? is skew
  steep?), not a forecast of second-leg yield. Keep the modeling humble.
- **Raw call yield double-counts vol.** Call premium and put premium are
  driven by the same IV surface. Multiplying the score by "call yield" is
  the same dimensional mistake the plan already fixed once for the put side
  (B4 in `wheel-screener-plan.md`): it re-rewards IV that
  `annualized_yield` already embeds. The *orthogonal* information is
  **skew** (put IV vs call IV), **call liquidity**, and **equity
  recoverability** — those, not raw call premium, are what's worth adding.
- **Much of "willing to own" is already enforced.** The universe stage
  (≥$20B, profitable, FCF-positive, liquid) is the biggest call-side filter
  the tool already has. The marginal value is per-chain structure plus a
  couple of slow-moving equity stats — not a second fundamentals screen.
- **New hard gates contradict the calibration loop.** The outcomes pipeline
  (`evaluate_outcomes.py`) exists precisely to test whether gates earn
  their keep (win-rate by rejection reason). A brand-new hard gate with
  zero outcome history would be uncalibrated by construction. New signals
  should enter as *visible flags and columns first*, gates only if the data
  later justifies it.

**Verdict: it makes sense — in a limited, measure-first form.** The case is
strengthened by a happy implementation accident: the call data is already
being fetched and thrown away (next section).

## Data cost: approximately zero

- `data.py:get_option_chain` calls `yf.Ticker(t).option_chain(expiration)`,
  which returns **both** `.calls` and `.puts` in the same HTTP response —
  the code currently keeps `.puts` and discards the calls. Caching both
  sides costs no additional request. This is the single biggest reason the
  integration is cheap: the chain-level metrics below need no new API
  surface, only a wider cache record.
- `dividendYield` is one more key on the same `info` blob
  `normalize_yf_fundamentals` already parses — also free.
- Per-name price history (for drawdown/recoverability stats) **would** add
  one `history` request per universe name. That's the only genuinely new
  load, which is why it's the last, optional phase — and it belongs in the
  weekly universe cache, not the daily run.

## Proposed metrics (per candidate row)

All computed from the *same expiration* as the selected put, same
conservative premium basis, attached to the row the way `pop`/`iv_used`
already are — components always visible.

| Field | Definition | What it captures |
|---|---|---|
| `call_yield_ann` | Annualized yield of the ~0.25Δ call (mirror of `delta.target`), conservative premium basis | Display/context only — never a raw score input (double-counts IV) |
| `skew_25d` | IV(25Δ put) − IV(25Δ call), interpolated from in-band contracts | Steep skew ⇒ put leg rich, call leg poor |
| `call_oi`, `call_spread_pct` | OI and spread at strikes in [put strike, spot] — the strikes you'd sell post-assignment | Exit-door liquidity |
| `thin_call_side` (flag) | `call_oi < call_side.min_open_interest` or spread beyond thresholds | The gate-able summary signal |
| `dividend_yield` (universe stage) | from fundamentals | Paid while wheeling; also flags ex-div early-assignment awareness |

Call deltas need a `bs_call_delta` sibling in `formulas.py`
(`N(d1)`; the put fallback already computes `N(d1) − 1`, so this is a
two-line, unit-tested addition).

Deliberately **excluded** for now: any "projected second-leg yield" model
(pretends to know the post-assignment IV surface), and raw call yield as a
score input (double-counts vol).

## Integration options, least → most invasive

**A. Report-only columns + flag (recommended first).** Fetch calls
alongside puts, compute the block above, render columns/badges in the
report and dashboard, and add the fields to the JSON snapshot (schema is
additive by design). No ranking change. Users see, e.g., "top-ranked put,
but skew is 12 vol points and the call side is thin" and decide.

**B. Tier preference (recommended with A).** A `scoring.prefer_two_sided`
boolean, exactly parallel to `prefer_affordable` / `prefer_live_quotes` in
`score.rank`: within the same actionability tier, rows *without*
`thin_call_side` sort above flagged ones. No formula change, trivially
inspectable, config-off-able.

**C. Score mode (later, config-gated).** A `scoring.mode: wheel_adjusted`
that multiplies `risk_adjusted` by a bounded wheelability factor in
[0.5, 1] derived from normalized skew + call liquidity — bounded so it
re-ranks but can never zero a candidate, components exposed like
`pop`/`iv_used`. Ship only after A/B has produced enough snapshots to
sanity-check the factor against outcomes.

**D. Hard gate (not now).** `thin_call_side` as a rejection. Only if the
outcomes data ever shows flagged names actually resolving worse — the same
evidentiary bar every existing gate is held to.

## Calibration hook

Add the call-side fields to run snapshots from day one (Phase A), so
`outcomes.json` analysis can eventually answer: *do `thin_call_side` /
steep-skew candidates that get assigned behave worse?* That's cheap now
and is the evidence Phase C/D would need. Full second-leg outcome tracking
(what covered calls actually yielded post-assignment) stays out of scope —
assignment handling is manual in v1 per the README, and My Picks records
closes, not follow-on legs.

## Config sketch

```yaml
call_side:
  enabled: true
  target_delta: 0.25        # mirror call to measure
  min_open_interest: 25     # thin_call_side threshold (calls often ~half put OI)
  max_spread_pct: 0.20      # looser than the put gate; it's a flag, not a gate
  max_skew: null            # optional: flag when IV_put25 - IV_call25 exceeds this

scoring:
  prefer_two_sided: true    # Phase B tier preference
  # mode: wheel_adjusted    # Phase C, later
```

## Open questions (answers change the plan)

1. **Ranking vs. visibility first?** This plan recommends visibility + tier
   preference (A+B) before any score change. If you'd rather the score
   itself reflect call-side quality immediately, Phase C moves up — but it
   starts uncalibrated.
2. **Which failure mode worries you most?** Thin call markets, poor call
   premium (skew), or stock-stays-down risk? The first two are free
   (chain-derived). The third needs per-name price history — the only part
   with real API cost — so it's worth building only if that's the risk you
   actually care about.
3. **Should a thin call side ever hard-reject** a name, or is a visible
   flag + lower tier enough? (Plan says flag until outcomes prove more.)
4. **Dashboard treatment:** extra columns in the candidates table, or a
   per-row expandable "call side" detail (keeps the table narrow)?

## Decisions (2026-07-24) — Phase A+B implemented

Guiding constraint from review: **the call-side check must never reduce the
set of put candidates shown** — it is a sanity check, not a filter. Concretely:

- **Phases A+B shipped**; C (`wheel_adjusted` score mode) and D (hard gate)
  deferred until the outcomes data can justify them.
- **Advisory flags, structurally unable to demote.** `thin_call_side` lives in
  `main.ADVISORY_FLAG_CODES`: routing and the earnings-promotion logic look
  only at *gating* flags, so a thin call side can never send a row to
  near-miss. The flag stays in `data_flags` for badges and the outcomes
  `by_flag_code` calibration.
- **Lenient thresholds** (`min_open_interest: 10`, spread flagged only beyond
  BOTH 25% and $0.15 — the same absolute-spread rescue as the put gate), and
  the flag fires only on *definitive* evidence: missing calls, missing OI/IV,
  or indicative (off-hours) quotes produce null fields and **no flag at all**.
- **Weakest rank tier.** `scoring.prefer_two_sided` sorts flagged rows last
  *within* the affordability and live-quote tiers; unmeasured call sides count
  as clean.
- **`call_yield_ann` uses the raw call mid** on put-strike collateral
  (comparable to the put's `annualized_yield`); the conservative premium basis
  is not applied — it is a context metric that never feeds the score.
- **Skew** compares the put row's raw IV (or band median) with the mirror
  call's IV; the score's later `iv_used` substitution can diverge in the rare
  IV-outlier case (accepted).
- Snapshot schema bumped to **v7** (this doc previously said v4/v6 — see
  `report.py` for the authoritative version log); chain cache namespace bumped
  to `chain2` and the CI actions cache key to `v3` because cached chain
  records now carry both sides.
- Dashboard: one context-only "Call yield" column plus the friendly
  `thin_call_side` badge; near-miss table unchanged.
