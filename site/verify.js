"use strict";

// Live-data verification math for the Select dialog (loaded between app.js and
// track.js). Pure functions, no DOM: track.js calls computeVerification() with
// the user's live broker quote and renders the result.
//
// Every function here mirrors the Python screener line-for-line so the
// client-side re-check agrees with the batch pipeline. Parity is pinned by
// tests/fixtures/verify_parity.json: the fixture's expected values are
// computed by formulas.py/score.py (tests/test_verify_parity.py) and asserted
// against this file under Node (scripts/check_verify_parity.mjs).
//
//   mid, spreadPct        <-> formulas.mid / formulas.spread_pct
//   collateral, roc       <-> formulas.collateral / formulas.roc
//   annualizedYield       <-> formulas.annualized_yield
//   yield30dte            <-> formulas.yield_30dte
//   impliedMove           <-> formulas.implied_move
//   distanceToStrike      <-> formulas.distance_to_strike
//   bsPutDelta            <-> formulas.bs_put_delta (closed form N(d1)-1)
//   premiumUsed           <-> main._effective_premium
//   score in computeVerification <-> score.score_candidate
//   gates in computeVerification <-> screen.apply_quality_filters
//
// Thresholds are NEVER hardcoded here — they come from the run snapshot's
// thresholds block (report.py writes thresholds.quality since schema v5).

const GYBVerify = (() => {
  // ---------------------------------------------------------------- formulas
  const mid = (bid, ask) => (bid + ask) / 2;

  function spreadPct(bid, ask) {
    const m = mid(bid, ask);
    if (m <= 0) return Infinity;
    return (ask - bid) / m;
  }

  const collateral = (strike, contracts = 1) => strike * 100 * contracts;

  function roc(premium, strike) {
    const coll = collateral(strike, 1);
    if (coll <= 0) return 0;
    return (premium * 100) / coll;
  }

  const annualizedYield = (premium, strike, dte) =>
    dte <= 0 ? 0 : roc(premium, strike) * (365 / dte);

  const yield30dte = (premium, strike, dte) =>
    dte <= 0 ? 0 : roc(premium, strike) * (30 / dte);

  const impliedMove = (ivAnnual, dte) =>
    ivAnnual == null || dte <= 0 ? 0 : ivAnnual * Math.sqrt(dte / 365);

  const distanceToStrike = (spot, strike) =>
    spot <= 0 ? 0 : (spot - strike) / spot;

  // Abramowitz & Stegun 7.1.26 rational approximation (max abs error ~1.5e-7,
  // far below display precision; Python uses the exact math.erf).
  function erf(x) {
    const sign = x < 0 ? -1 : 1;
    const ax = Math.abs(x);
    const t = 1 / (1 + 0.3275911 * ax);
    const y = 1 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
      - 0.284496736) * t + 0.254829592) * t * Math.exp(-ax * ax);
    return sign * y;
  }

  const normCdf = (x) => 0.5 * (1 + erf(x / Math.SQRT2));

  // Put delta = N(d1) - 1 (negative). Same closed form as py_vollib's
  // analytical delta and formulas._bs_put_delta_native.
  function bsPutDelta(spot, strike, dte, iv, r) {
    if (!spot || !strike || !iv || dte <= 0) return null;
    const t = dte / 365;
    const d1 = (Math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / (iv * Math.sqrt(t));
    return normCdf(d1) - 1;
  }

  // Black-Scholes European put price: K*e^(-rT)*N(-d2) - S*N(-d1).
  function bsPutPrice(spot, strike, dte, iv, r) {
    const t = dte / 365;
    const sq = iv * Math.sqrt(t);
    const d1 = (Math.log(spot / strike) + (r + 0.5 * iv * iv) * t) / sq;
    const d2 = d1 - sq;
    return strike * Math.exp(-r * t) * normCdf(-d2) - spot * normCdf(-d1);
  }

  // Solve the annualized IV whose BS price matches the live mid (bisection —
  // put price is monotonic in sigma). Returns null when no root exists in
  // [0.005, 5.0], e.g. a mid at/below intrinsic value.
  function impliedVolFromMid(targetMid, spot, strike, dte, r) {
    if (!(targetMid > 0) || !(spot > 0) || !(strike > 0) || !(dte > 0)) return null;
    let lo = 0.005, hi = 5.0;
    if (bsPutPrice(spot, strike, dte, lo, r) > targetMid) return null;
    if (bsPutPrice(spot, strike, dte, hi, r) < targetMid) return null;
    for (let i = 0; i < 80; i++) {
      const sigma = (lo + hi) / 2;
      const price = bsPutPrice(spot, strike, dte, sigma, r);
      if (Math.abs(price - targetMid) < 1e-6) return sigma;
      if (price < targetMid) lo = sigma; else hi = sigma;
    }
    return (lo + hi) / 2;
  }

  // The premium yields/score are computed from — mirror of
  // main._effective_premium. The user's typed bid/ask IS a live two-sided
  // quote, so only bid <= 0 degrades to the raw mid.
  function premiumUsed(bid, ask, basis) {
    const m = mid(bid, ask);
    if (bid > 0) {
      if (basis === "bid") return { premium: bid, basis: "bid" };
      if (basis === "conservative") return { premium: (bid + m) / 2, basis: "conservative" };
    }
    return { premium: m, basis: "mid" };
  }

  // Whole calendar days until "YYYY-MM-DD", against the user's LOCAL calendar
  // date — matches Python's (exp - date.today()).days for the person sitting
  // at their broker. Both sides go through Date.UTC so the local-vs-UTC
  // midnight off-by-one west of Greenwich can't happen.
  function calcDte(expiration) {
    const parts = String(expiration || "").split("-").map(Number);
    if (parts.length !== 3 || parts.some((p) => !Number.isFinite(p))) return null;
    const now = new Date();
    return Math.round((Date.UTC(parts[0], parts[1] - 1, parts[2])
      - Date.UTC(now.getFullYear(), now.getMonth(), now.getDate())) / 86400e3);
  }

  // ------------------------------------------------------------ verification
  // live: {bid, ask, spot, strike, expiration} — the user's broker numbers.
  // row: the screener candidate row. thresholds: the run's thresholds block
  // (requires thresholds.quality, schema v5+).
  // Returns {metrics, gates: [{code, status: pass|fail|flag, message}],
  //          verdict: green|amber|red, contract_overridden} or null when the
  //          inputs/thresholds are unusable.
  function computeVerification(live, row, thresholds) {
    const q = (thresholds || {}).quality;
    if (!q) return null;
    const bid = Number(live.bid), ask = Number(live.ask), spot = Number(live.spot);
    const strike = Number(live.strike);
    if (![bid, ask, spot, strike].every(Number.isFinite) || spot <= 0 || strike <= 0
        || bid < 0 || ask < 0) return null;
    const dte = calcDte(live.expiration);
    if (dte == null) return null;

    const r = q.risk_free_rate != null ? q.risk_free_rate : 0.04;
    const deltaBand = thresholds.delta || {};
    const overridden = strike !== Number(row.strike) || live.expiration !== row.expiration;

    const gates = [];
    const gate = (code, status, message) => gates.push({ code, status, message });
    const num = (x, d = 4) => (x == null ? "?" : Number(x).toFixed(d));

    const m = mid(bid, ask);
    const pu = premiumUsed(bid, ask, thresholds.premium_basis || "conservative");
    const metrics = {
      dte, mid: m, premium_used: pu.premium, premium_basis: pu.basis,
      spread_pct: null, spread_abs: ask - bid,
      yield_30dte: null, annualized_yield: null, distance_to_strike: null,
      iv: null, iv_source: null, implied_move: null, abs_delta: null, pop: null,
      score: null, score_mode: thresholds.scoring_mode || "risk_adjusted",
      collateral: collateral(strike),
    };

    // Hard short-circuits, mirroring apply_quality_filters' early returns.
    if (dte <= 0) {
      gate("expired", "fail", `expiration ${live.expiration} is not in the future (DTE ${dte})`);
      return { metrics, gates, verdict: "red", contract_overridden: overridden };
    }
    if (m <= 0) {
      gate("no_premium", "fail", "no valid premium (mid <= 0)");
      return { metrics, gates, verdict: "red", contract_overridden: overridden };
    }

    if (bid > ask) {
      gate("crossed_quote", "flag",
        `bid ${num(bid, 2)} > ask ${num(ask, 2)} — crossed quote; spread math is unreliable (typo, or an off-hours book)`);
    } else if (bid <= 0) {
      gate("one_sided_quote", "flag",
        "bid is 0 — one-sided quote; the mid and yields below assume more than the market currently pays");
    }

    // yield_30dte gates on the raw mid (screen.py:203); the displayed yields
    // use the effective premium, like the screener table.
    const y30gate = yield30dte(m, strike, dte);
    metrics.yield_30dte = yield30dte(pu.premium, strike, dte);
    metrics.annualized_yield = annualizedYield(pu.premium, strike, dte);
    if (q.min_yield_30dte != null && y30gate < q.min_yield_30dte) {
      gate("yield_30dte", "fail", `yield/30DTE ${num(y30gate)} < ${q.min_yield_30dte}`);
    } else {
      gate("yield_30dte", "pass", `yield/30DTE ${num(y30gate)}`);
    }

    const sp = spreadPct(bid, ask);
    metrics.spread_pct = Number.isFinite(sp) ? sp : null;
    const spreadAbs = ask - bid;
    const maxAbs = q.max_spread_abs != null ? q.max_spread_abs : Infinity;
    if (q.max_spread_pct != null && sp > q.max_spread_pct && spreadAbs > maxAbs) {
      gate("spread", "fail", `spread ${num(sp)} > ${q.max_spread_pct}`);
    } else {
      gate("spread", "pass", `spread ${num(sp)} of the mid (${num(spreadAbs, 2)} absolute)`);
    }

    const dist = distanceToStrike(spot, strike);
    metrics.distance_to_strike = dist;
    if (q.min_distance_to_strike != null && dist < q.min_distance_to_strike) {
      gate("distance", "fail", `distance ${num(dist)} < ${q.min_distance_to_strike}`);
    } else {
      gate("distance", "pass", `cushion ${num(dist)} below the live price`);
    }

    // IV: solve from the live mid so delta/implied-move are consistent with
    // the quote just typed; fall back to the (possibly stale) screener IV.
    let iv = impliedVolFromMid(m, spot, strike, dte, r);
    let ivSource = "solved";
    if (iv == null) {
      iv = row.iv_used != null ? row.iv_used : (row.iv != null ? row.iv : row.iv_band_median);
      ivSource = iv != null ? "screener" : null;
      if (iv != null) {
        gate("iv_stale", "flag",
          "could not solve IV from your quote — using the screener's (possibly stale) IV instead");
      }
    }
    metrics.iv = iv != null ? iv : null;
    metrics.iv_source = ivSource;

    if (iv == null) {
      gate("iv_missing", "flag", "no usable IV — implied-move and delta gates not evaluated");
    } else {
      const im = impliedMove(iv, dte);
      metrics.implied_move = im;
      if (q.max_implied_move != null && im > q.max_implied_move) {
        gate("implied_move", "fail", `implied move ${num(im)} > ${q.max_implied_move}`);
      } else {
        gate("implied_move", "pass", `implied move ${num(im)} over the contract's life`);
      }

      const delta = bsPutDelta(spot, strike, dte, iv, r);
      const absDelta = delta != null ? Math.abs(delta) : null;
      metrics.abs_delta = absDelta;
      if (absDelta == null) {
        gate("delta_band", "flag", "delta could not be computed");
      } else if ((deltaBand.min != null && absDelta < deltaBand.min)
              || (deltaBand.max != null && absDelta > deltaBand.max)) {
        gate("delta_band", "fail",
          `|delta| ${num(absDelta)} outside the ${deltaBand.min}-${deltaBand.max} band`);
      } else {
        gate("delta_band", "pass", `|delta| ${num(absDelta)} inside the ${deltaBand.min}-${deltaBand.max} band`);
      }
    }

    // Per-contract facts the user isn't typing: reusable only for the
    // screener's own contract.
    if (overridden) {
      gate("oi_unknown", "flag", "different contract than screened — check open interest at your broker");
    } else if (row.open_interest == null) {
      gate("oi_unknown", "flag", "no open interest from feed — OI gate not evaluated");
    } else if (q.min_open_interest != null && row.open_interest < q.min_open_interest) {
      gate("open_interest", "fail", `OI ${row.open_interest} < ${q.min_open_interest}`);
    } else {
      gate("open_interest", "pass", `OI ${row.open_interest} (from the screener run)`);
    }

    const laterExpiry = overridden && String(live.expiration) > String(row.expiration);
    const earningsUnknown = (row.data_flags || []).some((f) => f.code === "earnings_unknown");
    if (laterExpiry) {
      gate("earnings_unknown", "flag",
        "expiration moved later than screened — the earnings check was not re-run; verify the earnings date");
    } else if (earningsUnknown) {
      gate("earnings_unknown", "flag", "earnings date unknown at screen time — verify it before trading");
    } else {
      gate("earnings", "pass", "no earnings before expiry (checked at screen time)");
    }

    // Score, mirroring score.score_candidate on the live numbers.
    const floor = q.score_denominator_floor != null ? q.score_denominator_floor : 0.01;
    const imMove = metrics.implied_move != null ? metrics.implied_move : 0;
    const denom = Math.max(imMove, floor);
    const pop = metrics.abs_delta != null
      ? Math.max(0, Math.min(1, 1 - metrics.abs_delta)) : null;
    metrics.pop = pop;
    const ann = metrics.annualized_yield;
    if (metrics.score_mode === "annualized_yield_only") {
      metrics.score = ann;
    } else if (metrics.score_mode === "risk_adjusted") {
      metrics.score = ann * (pop != null ? pop : 1) * dist / denom;
    } else { // blended
      metrics.score = ann * dist / denom;
    }

    const verdict = gates.some((g) => g.status === "fail") ? "red"
      : gates.some((g) => g.status === "flag") ? "amber" : "green";
    return { metrics, gates, verdict, contract_overridden: overridden };
  }

  return {
    mid, spreadPct, collateral, roc, annualizedYield, yield30dte, impliedMove,
    distanceToStrike, erf, normCdf, bsPutDelta, bsPutPrice, impliedVolFromMid,
    premiumUsed, calcDte, computeVerification,
  };
})();

// Node (parity tests) — invisible to the browser.
if (typeof module !== "undefined" && module.exports) module.exports = GYBVerify;
