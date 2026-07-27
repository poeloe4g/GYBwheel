# Stock Blacklist — Ideation & Plan

> Status: **Implemented.** This document is the design record for the blacklist
> feature; the behaviour it describes is what shipped. Kept for the reasoning
> behind the cache decision, which is the non-obvious part.

## Context

A blacklist of stocks that never reach wheel candidates, whatever the screener says —
names not to trade for reasons no fundamental filter can express (ethics, a bad
experience, pending litigation, a business you don't understand).

A skeleton already exists: `config.yaml:20` declares `universe.ban_list: []` and
`universe.py:77-91` honors it. Three things stop it being usable:

1. **It silently doesn't work.** `build_universe` returns the weekly cache
   (`universe.py:56-75`) *before* applying ban/allow, and the cache key `_candidates_hash`
   (`universe.py:40`) hashes only the candidate tickers — not the ban list. Adding a ticker
   is a **no-op for up to 7 days** (`data.universe_refresh_days`). CI restores `.cache`
   across runs (`screen.yml:55-59`), so this bites in production, not just locally.
2. **It's git-only.** Every change is a hand edit plus a commit. You can't act on a name
   you're looking at in the dashboard, and not from your phone.
3. **It has no memory and no visibility.** Bare strings record no reason, so the list rots
   into a blob you're afraid to prune. Drops are counted under the generic `"universe"`
   code (`main.py:153-154`) and render as *"Failed company-quality screen"* (`app.js:65`) —
   actively misleading, since the name didn't fail anything.

Outcome: a blacklist maintained from the dashboard, where each entry remembers why it was
added, temporary bans release themselves, and an excluded name is visibly excluded *by you*
rather than mislabeled as a screen failure.

## Decisions

- **Primary surface: the dashboard.** A per-row "exclude" action writes to
  `site/data/selections.json` via the GitHub Contents API, mirroring the Adjust-capital
  modal. `config.yaml` remains a valid secondary source.
- **Rich entries:** `{ticker, reason, added, review_after?}`; bare strings still accepted.
  `review_after` auto-releases a temporary ban once the date passes.
- **Own rejection code** `blacklisted`, distinct from `universe`, with a plain-language label.

## The crux: cache invalidation

The ban list must never be baked into the cache. The cache exists to store the *expensive*
thing — the fundamental screen of the candidate universe. Blacklist membership is a free set
lookup that belongs **after** every cache read.

**Approach: keep `_candidates_hash` unchanged; move ban/allow out of the pre-fetch prefilter
and apply it to the result of both the cache-hit path and the fresh-build path.**

Rejected alternative: folding the blacklist into the cache hash. It is correct, but it
discards the whole fundamentals cache (~108 tickers of yfinance calls) on every edit — and
with a one-tap dashboard button, edits stop being rare.

The tradeoff being accepted: banned names still get their fundamentals fetched, so we spend
a handful of wasted calls per weekly rebuild. That is the price of the property that
matters most — **un-banning is instant**. If the ban were applied before fetching, a
removed name would not be in the cached passing list and could not reappear for up to
7 days, reintroducing the same class of bug at the other end. Waste is bounded by the size
of your blacklist, which is small by construction.

Secondary benefit: blacklist rejects are recomputed every run, so the reject message always
carries the *current* reason text rather than one frozen into the cache.

## Implementation

### 1. `blacklist.py` (new module)

Fits the repo's flat single-purpose module style (`regime.py`, `score.py`, `cache.py`).
Keeps `universe.py` focused on fundamentals.

- `normalize(entries, today=None) -> (active: dict[str, dict], expired: list[dict])` —
  accepts bare strings and dicts, uppercases tickers, drops malformed entries with a
  `log.warning`. An entry whose `review_after` is on or before `today` lands in `expired`
  and is **not** active.
- `from_selections(doc) -> list` — defensive extraction of `doc["blacklist"]`, following
  `size.py:105-124` `_capital_override` exactly: warn and ignore, never fail the run.
- `merge(config_entries, dashboard_entries) -> list` — union. When a ticker appears in
  both, the dashboard entry wins (it's the surface you can actually reach from a phone).

Parsing must be total: any bad input degrades to "not blacklisted", never an exception.
A malformed blacklist silently excluding your whole universe would be far worse than one
that fails open.

### 2. `universe.py`

- Delete the pre-fetch ban/allow prefilter at `:77-91`.
- Add `_apply_exclusions(passing, rejects, blacklist, allow) -> (passing, rejects)`, applied
  to **both** return paths — the cache hit at `:75` and the fresh build at `:102`.
- Blacklist takes precedence over fundamentals: a banned name that also fails the screen
  reports `blacklisted`, and its fundamental reject is dropped, so it is counted once.
- Rejects gain `{"code": "blacklisted", "message": <your reason>}`.
- `_candidates_hash` is untouched.

### 3. `main.py`

Mirror the capital-override merge at `:99-104`, before `build_universe` at `:130`:

```python
config["universe"]["ban_list"] = blacklist.merge(
    config["universe"].get("ban_list", []),
    blacklist.from_selections(selections_doc),
)
```

One mutation covers every downstream reader, including the snapshot echo. Then fix the
reject counting at `:153-154` to count each reject under its own `r["code"]` rather than
collapsing everything to `"universe"`.

### 4. `report.py` — schema v8

Bump `SCHEMA_VERSION` to 8 (`:51`) with a new comment block in the additive log at `:17-50`.
Add `thresholds.universe` to `write_json` (`:151`): the fundamental limits plus the active
blacklist (ticker, reason, review_after) and the expired-but-still-present entries, so the
dashboard can render the list and flag entries due for pruning.

### 5. Expiry policy

Expired entries are **ignored at runtime but left in the file**, and surfaced in the
management view for manual pruning. The screener runs unattended in CI; having a cron job
silently rewrite your `selections.json` is a surprising write, and you may well want to
re-up a ban rather than lose it.

### 6. UI — `site/`

`selections.json` gains a sibling block to `account`:

```json
"blacklist": [
  { "ticker": "XYZ", "reason": "burned me in March", "added": "2026-07-27",
    "review_after": "2027-01-01" }
]
```

- **Add path:** an "Exclude" action on each candidate and near-miss row. Modal collects a
  free-text reason (`maxlength="80"`, matching `#cap-note` at `index.html:387`) and an
  optional `<input type="date">` review date with presets (permanent / 3 months / 6 months).
- **Management view:** a header button "Excluded (N)" opening a dialog that lists current
  entries with reason, added date and review date, each with **Remove**; expired entries
  render greyed with Remove / Extend. This is the un-blacklist path — an add-only feature
  would rot exactly the way the current bare list does.
- All writes go through `saveSelections(mutate, message)` (`track.js:58`). Mutations must be
  appends or in-place patches so the retry-on-stale-sha path stays safe. Gate on
  `state.writable` (`track.js:684`).
- `app.js:65` — add `blacklisted: "You excluded this name"` to `FRIENDLY_CODE`.
- Excluding a name greys/strikes its row immediately client-side; the row only disappears on
  the next screener run. Reuse the wording pattern at `index.html:378-379` ("the cards above
  update immediately; the screener … from its next run").

### 7. Docs

Update `README.md:37` (the universe step) and commit the plan to `docs/`, matching the
convention set by `docs/call-side-integration-plan.md`.

## Not affected

`site/verify.js` re-runs only *contract-level* gates (delta band, spread, IV) against
`thresholds.quality`; it never touches the universe layer, and neither does
`scripts/check_verify_parity.mjs`. **The JS↔Python parity check gated in CI is untouched.**

## Verification

The UI was additionally driven end-to-end in headless Chromium against a mocked
GitHub Contents API (exclude → save → row greys out → manage → remove → re-render),
including the two cases that are easy to get wrong: a review date in the past is
refused rather than silently excluding nothing, and a lapsed entry stops excluding
while staying listed for pruning.

- `pytest -q` — full suite offline, no network.
- New `tests/test_blacklist.py`: bare-string and dict normalization; `review_after` in the
  past → inactive, future → active, absent → permanent; malformed entries (non-dict, missing
  ticker, bad date) ignored without raising; config/dashboard union and precedence.
- `tests/test_universe.py` — the regression tests that matter, extending the existing
  `test_universe_cache_reused` (`:55`) and `test_universe_cache_invalidated_by_candidate_change`
  (`:67`) pattern with `FakeProvider` + a real `DiskCache(tmp_path)`:
  - ban applied on a **cache hit** (the bug — fails before this change),
  - un-ban restores the name on a cache hit without a refetch (assert via `provider.calls`),
  - reject code is `blacklisted` and carries the reason,
  - blacklisted-and-also-fails-fundamentals is counted once, as `blacklisted`,
  - `allow_list` still restricts correctly post-cache.
- `tests/test_pipeline.py` — end-to-end with the `"BAN"` ticker already reserved at `:24`:
  excluded from candidates and present in `rejections_by_reason["blacklisted"]`.
- `tests/test_report_json.py` — `thresholds.universe` present, `schema_version == 8`.
- Manual: add a ticker via the dashboard, confirm `selections.json` commits, run
  `python main.py --json-out …` twice (second run hits the cache) and confirm the name is
  excluded **both** times; remove it and confirm it returns on the very next run.
