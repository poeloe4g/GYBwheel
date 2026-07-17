// JS<->Python parity check for site/verify.js.
//
// Loads tests/fixtures/verify_parity.json (generated and pinned by
// tests/test_verify_parity.py, so its expectations always equal what
// formulas.py / score.py compute) and asserts GYBVerify's functions agree
// within 1e-6 — 1e-5 for delta and delta-derived values, covering the
// Abramowitz-Stegun erf approximation.
//
//     node scripts/check_verify_parity.mjs

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const require = createRequire(import.meta.url);
const V = require(join(root, "site", "verify.js"));
const fixture = JSON.parse(
  readFileSync(join(root, "tests", "fixtures", "verify_parity.json"), "utf8"));

let failures = 0;
function check(name, key, actual, expected, tol) {
  if (!(Math.abs(actual - expected) <= tol)) {
    failures++;
    console.error(`FAIL ${name}.${key}: js=${actual} py=${expected} (tol ${tol})`);
  }
}

for (const { name, inputs: c, expected: e } of fixture.cases) {
  const m = V.mid(c.bid, c.ask);
  check(name, "mid", m, e.mid, 1e-6);
  check(name, "spread_pct", V.spreadPct(c.bid, c.ask), e.spread_pct, 1e-6);
  const pu = V.premiumUsed(c.bid, c.ask, "conservative");
  check(name, "premium_conservative", pu.premium, e.premium_conservative, 1e-6);
  check(name, "yield_30dte_mid", V.yield30dte(m, c.strike, c.dte), e.yield_30dte_mid, 1e-6);
  check(name, "annualized_yield",
    V.annualizedYield(pu.premium, c.strike, c.dte), e.annualized_yield, 1e-6);
  check(name, "implied_move", V.impliedMove(c.iv, c.dte), e.implied_move, 1e-6);
  check(name, "distance_to_strike",
    V.distanceToStrike(c.spot, c.strike), e.distance_to_strike, 1e-6);
  const delta = V.bsPutDelta(c.spot, c.strike, c.dte, c.iv, c.r);
  check(name, "put_delta", delta, e.put_delta, 1e-5);
  // score.score_candidate, risk_adjusted — same expression computeVerification uses.
  const pop = Math.max(0, Math.min(1, 1 - Math.abs(delta)));
  const denom = Math.max(V.impliedMove(c.iv, c.dte), c.floor);
  const score = e.annualized_yield * pop * V.distanceToStrike(c.spot, c.strike) / denom;
  check(name, "score_risk_adjusted", score, e.score_risk_adjusted, 1e-5);
}

// Non-finite edges the fixture can't hold (JSON has no Infinity).
if (V.spreadPct(0, 0) !== Infinity) { failures++; console.error("FAIL spreadPct(0,0) !== Infinity"); }
if (V.annualizedYield(1.0, 100.0, 0) !== 0) { failures++; console.error("FAIL annualizedYield dte=0 !== 0"); }
if (V.bsPutDelta(100, 90, 0, 0.3, 0.04) !== null) { failures++; console.error("FAIL bsPutDelta dte=0 !== null"); }

// IV solve round-trip: price a put at a known sigma, solve it back.
{
  const sigma = 0.31, spot = 140, strike = 130, dte = 35, r = 0.04;
  const price = V.bsPutPrice(spot, strike, dte, sigma, r);
  const solved = V.impliedVolFromMid(price, spot, strike, dte, r);
  check("iv_roundtrip", "sigma", solved, sigma, 1e-4);
  // Below intrinsic value → no root.
  if (V.impliedVolFromMid(0.0001, 100, 130, 35, r) !== null) {
    failures++; console.error("FAIL impliedVolFromMid below-intrinsic !== null");
  }
}

// computeVerification smoke test with a clean live quote (expiration built
// relative to today so the DTE lands mid-window).
{
  const exp = new Date(Date.now() + 35 * 86400e3).toISOString().slice(0, 10);
  const thresholds = {
    delta: { min: 0.15, max: 0.30, target: 0.20 },
    scoring_mode: "risk_adjusted", premium_basis: "conservative",
    quality: {
      min_yield_30dte: 0.005, max_implied_move: 0.15, max_spread_pct: 0.15,
      max_spread_abs: 0.10, min_open_interest: 50, min_distance_to_strike: 0.03,
      risk_free_rate: 0.04, score_denominator_floor: 0.01,
    },
  };
  const row = { strike: 130, expiration: exp, open_interest: 800, iv: 0.28,
    data_flags: [], score: 1.5, pop: 0.8 };
  const res = V.computeVerification(
    { bid: 2.7, ask: 2.9, spot: 140, strike: 130, expiration: exp }, row, thresholds);
  if (!res || res.verdict !== "green") {
    failures++;
    console.error(`FAIL computeVerification clean quote: expected green, got ${res && res.verdict}`,
      res && res.gates.filter((g) => g.status !== "pass"));
  }
  const red = V.computeVerification(
    { bid: 2.7, ask: 2.9, spot: 128, strike: 130, expiration: exp }, row, thresholds);
  if (!red || red.verdict !== "red" || !red.gates.some((g) => g.code === "distance" && g.status === "fail")) {
    failures++;
    console.error("FAIL computeVerification strike-above-spot should fail the distance gate");
  }
  const expired = V.computeVerification(
    { bid: 2.7, ask: 2.9, spot: 140, strike: 130, expiration: "2020-01-17" }, row, thresholds);
  if (!expired || !expired.gates.some((g) => g.code === "expired" && g.status === "fail")) {
    failures++;
    console.error("FAIL computeVerification past expiration should fail the expired gate");
  }
}

if (failures) {
  console.error(`\n${failures} parity failure(s)`);
  process.exit(1);
}
console.log(`OK — ${fixture.cases.length} fixture cases + edge checks agree with Python`);
