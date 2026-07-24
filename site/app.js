"use strict";

// Single source of truth: read the design tokens straight from styles.css so
// the JS palette can never drift from the CSS one.
const cssVar = (name, fallback) => {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
};
const COLORS = {
  green: cssVar("--green", "#1a9850"),
  yellow: cssVar("--yellow", "#e0a800"),
  red: cssVar("--red", "#d73027"),
  accent: cssVar("--accent", "#4a9eff"),
  muted: cssVar("--muted", "#8b98a5"),
  grid: cssVar("--grid", "#212a35"),
  axis: cssVar("--axis", "#3a4653"),
  panel: cssVar("--panel", "#1a2029"),
};
// CVD-safe categorical palette (validated against the panel surface). Status
// green/yellow/red are intentionally kept out of these slots.
const SERIES = [
  cssVar("--series-1", "#3987e5"), cssVar("--series-2", "#008300"),
  cssVar("--series-3", "#d55181"), cssVar("--series-4", "#c98500"),
  cssVar("--series-5", "#199e70"), cssVar("--series-6", "#d95926"),
  cssVar("--series-7", "#9085e9"), cssVar("--series-8", "#e66767"),
];
// Semi-transparent tint of a hex color (for area/bubble fills).
const tint = (hex, a) => {
  const n = parseInt(hex.replace("#", ""), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
};

// Plain-language framing for the regime traffic light.
const REGIME_PLAIN = {
  GREEN: "Market conditions look calm — normal conditions for new trades.",
  YELLOW: "One caution signal is on — be extra selective with new trades.",
  RED: "Market stress detected — no new trades suggested; manage existing positions only.",
};
const SIGNAL_PLAIN = {
  spy_below_200dma: "S&P 500 is below its 200-day average",
  breadth_below_floor: "most stocks are in downtrends",
  vix_high_and_spy_falling: "volatility is high while the market falls",
};

// Short human labels for machine reason/flag codes. The exact code + message
// stay in the hover tooltip for anyone who wants the detail.
const FRIENDLY_CODE = {
  earnings: "Earnings before expiry",
  earnings_unknown: "Earnings date unknown",
  implied_move: "Too volatile",
  spread: "Wide bid-ask spread",
  open_interest: "Thinly traded",
  distance: "Too little cushion",
  yield_30dte: "Premium too small",
  no_premium: "No usable price",
  missing_strike_dte: "Bad contract data",
  unaffordable: "Needs more cash than your limits allow",
  dte_stretched: "Longer expiry than usual",
  iv_outlier: "Suspicious volatility data",
  iv_missing: "No volatility data",
  spread_unknown: "No live quote",
  oi_unknown: "No liquidity data",
  quote_indicative: "Price from last trade, not a live quote",
  thin_call_side: "Call side thinly traded",
  universe: "Failed company-quality screen",
  no_spot: "No stock price",
  delta_band: "Odds outside the target band",
  expired: "Already expired",
  crossed_quote: "Bid above ask",
  one_sided_quote: "One-sided quote",
  iv_stale: "Using screener's volatility",
  no_put_in_band: "No suitable contract",
  no_expiry_in_window: "No expiry in the target window",
};
const friendly = (code) => FRIENDLY_CODE[code] || code;

const charts = {}; // id -> Chart instance, so we can destroy/redraw on run switch
// Default order is _rank: the screener's own ranking (tradeable ideas first,
// then by score) — clicking a header re-sorts on that column.
let tableState = { key: "_rank", dir: 1, rows: [] };

const $ = (sel) => document.querySelector(sel);
const fmtPct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const fmtPct0 = (x) => (x == null ? "—" : Math.round(x * 100) + "%");
const fmtUsd = (x) => (x == null ? "—" : "$" + Math.round(x).toLocaleString());
const fmtNum = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const badge = (cls) => (e) =>
  `<span class="${cls}" title="${esc((e.code || "?") + ": " + (e.message || ""))}">${esc(friendly(e.code))}</span>`;

// Derived, layman-facing fields shared by the tables and the top-pick card.
// Older snapshots lack premium_used/pop — degrade to mid / 1-|delta|.
function enrich(r, i) {
  const premium = r.premium_used ?? r.mid;
  return {
    ...r,
    _rank: i,
    _premium_usd: premium != null ? premium * 100 : null,
    _cash_needed: r.collateral_per_contract ?? (r.strike != null ? r.strike * 100 : null),
    _pop: r.pop ?? (r.abs_delta != null ? Math.max(0, Math.min(1, 1 - r.abs_delta)) : null),
  };
}

// If the Chart.js CDN failed, degrade to tables/cards instead of a blank page.
const HAS_CHART = typeof Chart !== "undefined";
if (HAS_CHART) {
  Chart.defaults.color = COLORS.muted;
  Chart.defaults.borderColor = COLORS.grid;
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 11;
  // Canvases live in a fixed-height .canvas-wrap, so let them fill it.
  Chart.defaults.maintainAspectRatio = false;
  // Calmer marks: thin rounded bars, hover-only points, 2px lines.
  Chart.defaults.elements.bar.borderRadius = 4;
  Chart.defaults.elements.bar.borderSkipped = false;
  Chart.defaults.elements.point.radius = 0;
  Chart.defaults.elements.point.hoverRadius = 5;
  Chart.defaults.elements.point.hitRadius = 14;
  Chart.defaults.elements.line.borderWidth = 2;
  Chart.defaults.elements.line.tension = 0.3;
  // Consistent, quiet tooltip.
  Object.assign(Chart.defaults.plugins.tooltip, {
    backgroundColor: "#0d1117", borderColor: COLORS.grid, borderWidth: 1,
    padding: 10, cornerRadius: 8, titleColor: "#e6edf3", bodyColor: "#c3c2b7",
    displayColors: false,
  });
}

// Recessive hairline axes shared by every cartesian chart.
const axisX = (extra = {}) => ({
  grid: { color: COLORS.grid, drawTicks: false },
  border: { display: false },
  ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 },
  ...extra,
});
const axisY = (extra = {}) => ({
  grid: { color: COLORS.grid, drawTicks: false },
  border: { display: false },
  ...extra,
});

// Tiny inline plugin: draw the value at the end of each horizontal bar. Keeps
// the headline bars readable without pulling in chartjs-plugin-datalabels.
const barValueLabels = (fmt) => ({
  id: "barValueLabels",
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const meta = chart.getDatasetMeta(0);
    ctx.save();
    ctx.fillStyle = COLORS.muted;
    ctx.font = "11px " + Chart.defaults.font.family;
    ctx.textAlign = "left";
    ctx.textBaseline = "middle";
    meta.data.forEach((el, i) => {
      const v = chart.data.datasets[0].data[i];
      if (v == null) return;
      ctx.fillText(fmt(v), el.x + 6, el.y);
    });
    ctx.restore();
  },
});

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
}

function showError(msg) {
  const el = $("#error");
  el.textContent = msg;
  el.classList.remove("hidden");
}

function destroyChart(id) {
  if (charts[id]) { charts[id].destroy(); delete charts[id]; }
}

// ---------------------------------------------------------------- run render
function renderRegime(doc) {
  const light = (doc.regime && doc.regime.light) || "UNKNOWN";
  const banner = $("#regime");
  banner.className = "regime-banner regime-" + light.toLowerCase();
  banner.querySelector(".regime-light").textContent = light;
  $("#regime-plain").textContent = REGIME_PLAIN[light] || "";
  const tripped = (doc.regime && doc.regime.tripped) || [];
  $("#regime-tripped").textContent = tripped.length
    ? "Why: " + tripped.map((s) => SIGNAL_PLAIN[s] || s).join("; ") + "."
    : "No warning signals are on.";
  const ts = doc.meta && doc.meta.generated_at;
  const el = $("#generated-at");
  el.textContent = ts ? "Generated " + new Date(ts).toLocaleString() : "";
  // Freshness badge: >96h covers a weekend + Monday holiday before crying stale.
  if (ts) {
    const ageHours = (Date.now() - new Date(ts).getTime()) / 3600e3;
    if (ageHours > 96) {
      el.insertAdjacentHTML("beforeend", `<span class="badge-stale" title="Last run is ${Math.round(ageHours / 24)} days old — the scheduled screener may be failing.">STALE</span>`);
    } else if (ageHours > 30) {
      el.insertAdjacentHTML("beforeend", `<span class="badge-not-today">not today</span>`);
    }
  }
  if (doc.meta && doc.meta.quotes_trusted === false) {
    el.insertAdjacentHTML("beforeend", `<span class="badge-stale" title="This run executed outside regular US market hours — option prices may be stale or zeroed, so treat today's numbers with suspicion.">OFF-HOURS DATA</span>`);
  }
}

function renderTopPick(doc) {
  const section = $("#top-pick");
  const light = (doc.regime && doc.regime.light) || "UNKNOWN";
  const rows = (doc.rows || []).map(enrich);
  // The table is already ranked; the top actionable row is the first that
  // fits the account. Hide the card on RED days or when nothing qualifies.
  const pick = rows.find((r) => (r.max_contracts ?? 0) >= 1) || null;
  if (light === "RED" || !pick) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");
  const cushion = pick.distance_to_strike != null ? ` (${fmtPct(pick.distance_to_strike)} below today's price)` : "";
  const odds = pick._pop != null ? ` Estimated odds of keeping the full premium: ~${fmtPct0(pick._pop)}.` : "";
  $("#top-pick-text").innerHTML =
    `Sell one <strong>${esc(pick.ticker)}</strong> put at strike ` +
    `<strong>${fmtUsd(pick.strike)}</strong>, expiring <strong>${esc(pick.expiration)}</strong> ` +
    `(${pick.dte} days). You'd collect about <strong>${fmtUsd(pick._premium_usd)}</strong> now ` +
    `and set aside <strong>${fmtUsd(pick._cash_needed)}</strong>. You keep the premium as long ` +
    `as ${esc(pick.ticker)} stays above ${fmtUsd(pick.strike)}${cushion}.${odds}`;
}

function renderCards(doc) {
  const h = doc.header || {};
  const rows = doc.rows || [];
  const nearMissCount = (doc.near_misses || []).length;
  const candidates = String(rows.length) +
    (nearMissCount ? ` (+${nearMissCount} near miss${nearMissCount > 1 ? "es" : ""})` : "");
  // Live capital state published by track.js (updateCapitalCards): the
  // header's numbers were baked in at run time and go stale the moment
  // capital or picks change on the dashboard. Prefer the live view; the
  // deployed split (v6 headers) avoids double-counting the OPEN picks
  // already baked into this snapshot.
  const live = window.GYBCapital || null;
  let total = h.total_capital;
  let deployed = h.deployed;
  let isLive = false;
  if (live) {
    if (live.total_capital != null && live.total_capital !== total) {
      total = live.total_capital;
      isLive = true;
    }
    if (h.deployed_positions != null) {
      const d = h.deployed_positions + live.open_collateral;
      if (d !== deployed) isLive = true;
      deployed = d;
    }
  }
  const pctDeployed = total ? deployed / total : 0;
  const remaining = total != null ? total - deployed : h.remaining_cash;
  const liveMark = isLive
    ? ` <span class="muted" title="Recomputed from your current picks file — the last run's snapshot is out of date.">(live)</span>`
    : "";
  const cards = [
    ["Account size", fmtUsd(total) + liveMark],
    ["Cash already committed", `${fmtUsd(deployed)} (${fmtPct(pctDeployed)})${liveMark}`],
    ["Cash available", fmtUsd(remaining) + liveMark],
    ["Ideas today", candidates],
    ["Your positions", h.positions_source && h.positions_source.startsWith("greenfield")
      ? "none loaded" : (h.positions_source || "—")],
  ];
  // Only v3+ rows carry `affordable`; skip the card for older snapshots.
  if (rows.some((r) => r.affordable != null)) {
    const n = rows.filter((r) => r.affordable).length;
    cards.splice(4, 0, ["Fit your account", rows.length
      ? `${n} of ${rows.length} ideas` : "—"]);
  }
  $("#capital-cards").innerHTML = cards
    .map(([l, v]) => `<div class="card"><div class="label">${l}</div><div class="value">${v}</div></div>`)
    .join("");
  const warnEl = $("#capital-warning");
  const warn = doc.meta && doc.meta.capital_warning;
  warnEl.textContent = warn ? `⚠ ${warn}` : "";
  warnEl.classList.toggle("hidden", !warn);
}

function renderTable() {
  const tbody = $("#candidates tbody");
  const { key, dir, rows } = tableState;
  const sorted = [...rows].sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "string") return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });
  tbody.innerHTML = sorted.map((r) => {
    const flag = (r.breaches_per_name_cap
      ? `<span class="badge-breach" title="One contract needs more cash than your per-stock limit. You'd need an account of at least ${fmtUsd(r.min_account_for_1_contract)}.">TOO BIG</span>`
      : "") + (r.data_flags || []).map(badge("badge-flag")).join("");
    const select = (r.max_contracts ?? 0) >= 1
      ? `<button type="button" class="btn-select" data-rank="${r._rank}">Select</button>`
      : `<span class="muted" title="One contract already needs more cash than your limits allow.">—</span>`;
    return `<tr>
      <td>${r.ticker ?? ""}</td>
      <td>${r.sector ?? ""}</td>
      <td>${r.expiration ?? ""}</td>
      <td class="num">${r.dte ?? ""}</td>
      <td class="num">${fmtNum(r.strike)}</td>
      <td class="num">${fmtUsd(r._premium_usd)}</td>
      <td class="num">${fmtUsd(r._cash_needed)}</td>
      <td class="num">${fmtPct0(r._pop)}</td>
      <td class="num">${fmtPct(r.annualized_yield)}</td>
      <td class="num">${fmtPct(r.call_yield_ann)}</td>
      <td class="num">${fmtPct(r.distance_to_strike)}</td>
      <td class="num">${fmtNum(r.score, 3)}</td>
      <td class="num">${r.max_contracts ?? ""}</td>
      <td>${flag}</td>
      <td>${select}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="15" class="muted">No ideas passed every safety check today. Check the near misses below to see what almost made it.</td></tr>`;
}

function wireSelectButtons() {
  $("#candidates tbody").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".btn-select");
    if (!btn) return;
    const row = tableState.rows.find((r) => r._rank === Number(btn.dataset.rank));
    if (row && typeof GYBTrack !== "undefined") GYBTrack.openSelectModal(row);
  });
}

function wireTableSort() {
  document.querySelectorAll("#candidates thead th").forEach((th) => {
    const key = th.dataset.key;
    if (!key || key === "flags") return;
    th.addEventListener("click", () => {
      tableState.dir = tableState.key === key ? -tableState.dir : -1;
      tableState.key = key;
      renderTable();
    });
  });
}

function renderNearMisses(doc) {
  const rows = (doc.near_misses || []).map(enrich);
  const section = $("#near-miss-section");
  if (!rows.length) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");
  $("#near-misses tbody").innerHTML = rows.map((r) => `<tr>
      <td>${r.ticker ?? ""}</td>
      <td>${r.sector ?? ""}</td>
      <td>${r.expiration ?? ""}</td>
      <td class="num">${r.dte ?? ""}</td>
      <td class="num">${fmtNum(r.strike)}</td>
      <td class="num">${fmtUsd(r._premium_usd)}</td>
      <td class="num">${fmtPct(r.annualized_yield)}</td>
      <td class="num">${fmtPct(r.distance_to_strike)}</td>
      <td class="num">${fmtNum(r.score, 3)}</td>
      <td>${(r.rejection_reasons || []).map(badge("badge-reject")).join("")}${(r.data_flags || []).map(badge("badge-flag")).join("")}</td>
    </tr>`).join("");
}

function renderRejectionChart(doc) {
  if (!HAS_CHART) return;
  const counts = (doc.meta && doc.meta.rejections_by_reason) || {};
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const card = $("#chart-rejections-card");
  destroyChart("chart-rejections");
  if (!entries.length) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  charts["chart-rejections"] = new Chart($("#chart-rejections"), {
    type: "bar",
    data: { labels: entries.map(([k]) => friendly(k)),
      datasets: [{ data: entries.map(([, v]) => v), backgroundColor: SERIES[0], maxBarThickness: 20 }] },
    options: { indexAxis: "y", layout: { padding: { right: 24 } },
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.raw} stock${c.raw === 1 ? "" : "s"}` } } },
      scales: { x: axisX({ beginAtZero: true, ticks: { precision: 0 } }), y: axisY({ grid: { display: false } }) } },
    plugins: [barValueLabels((v) => String(v))],
  });
}

// Headline numbers that lead the run analysis — the 3-4 figures that matter,
// so the section opens on an answer instead of four charts.
function renderKpis(doc) {
  const rows = (doc.rows || []).map(enrich);
  const fit = rows.filter((r) => (r.max_contracts ?? 0) >= 1);
  const best = rows.reduce((m, r) => (r.score != null && (m == null || r.score > m) ? r.score : m), null);
  const topPick = fit[0] || null; // rows arrive pre-ranked, best actionable first
  const pct = (doc.header && doc.header.pct_deployed) || 0;
  const pctW = Math.min(100, Math.max(0, Math.round(pct * 100)));
  const cards = [
    ["Best score", best != null ? fmtNum(best, 3) : "—", "Higher = better premium for the risk"],
    ["Ideas that fit", rows.length ? `${fit.length} of ${rows.length}` : "—", "Pass every check and fit your account"],
    ["Top-pick yield", topPick && topPick.annualized_yield != null ? fmtPct(topPick.annualized_yield) : "—",
      topPick ? `${esc(topPick.ticker)} · annualized` : "No idea fits today"],
    ["Cash committed", fmtPct(pct), `<div class="meter"><span style="width:${pctW}%"></span></div>`],
  ];
  $("#kpi-cards").innerHTML = cards.map(([l, v, sub]) =>
    `<div class="card"><div class="label">${l}</div><div class="value">${v}</div><div class="kpi-sub">${sub}</div></div>`
  ).join("");
}

function renderRunCharts(doc) {
  if (!HAS_CHART) return;
  const rows = (doc.rows || []).map(enrich);
  const topTicker = (rows.find((r) => (r.max_contracts ?? 0) >= 1) || {}).ticker;

  // Best ideas by score (horizontal bar) — single series, direct value labels.
  const top = [...rows].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 10);
  destroyChart("chart-top-score");
  charts["chart-top-score"] = new Chart($("#chart-top-score"), {
    type: "bar",
    data: { labels: top.map((r) => r.ticker),
      datasets: [{ data: top.map((r) => r.score), backgroundColor: SERIES[0], maxBarThickness: 20 }] },
    options: { indexAxis: "y", layout: { padding: { right: 34 } },
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => `Score ${fmtNum(c.raw, 3)}` } } },
      scales: { x: axisX({ beginAtZero: true }), y: axisY({ grid: { display: false } }) } },
    plugins: [barValueLabels((v) => fmtNum(v, 3))],
  });

  // Yield vs cushion (bubble; size ~ contracts that fit). The top pick is
  // highlighted so the eye lands on the actionable idea.
  destroyChart("chart-yield-distance");
  charts["chart-yield-distance"] = new Chart($("#chart-yield-distance"), {
    type: "bubble",
    data: { datasets: [{
      data: rows.map((r) => ({ x: (r.distance_to_strike || 0) * 100,
        y: (r.annualized_yield || 0) * 100, r: 5 + 2 * (r.max_contracts || 0), ticker: r.ticker })),
      backgroundColor: rows.map((r) => tint(r.ticker === topTicker ? SERIES[5] : SERIES[0], 0.55)),
      borderColor: COLORS.panel, borderWidth: 2 }] },
    options: { plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => `${c.raw.ticker}: ${c.raw.y.toFixed(1)}% yearly yield with a ${c.raw.x.toFixed(1)}% cushion` } } },
      scales: { x: axisX({ title: { display: true, text: "Safety cushion % (room to fall)" }, maxRotation: 0 }),
        y: axisY({ title: { display: true, text: "Yearly yield %" } }) } },
  });

  // Collateral by sector (doughnut) — categorical palette, 2px surface gaps.
  // Weight by the cash that would actually be committed (collateral × contracts
  // that fit). When nothing fits the account today that sum is zero and the
  // ring would vanish, so fall back to one contract of each idea — the sector
  // mix stays visible and the caption still reads true.
  const collat = (r) => r.collateral_per_contract || (r.strike != null ? r.strike * 100 : 0);
  const bySector = {};
  const add = (weight) => {
    Object.keys(bySector).forEach((k) => delete bySector[k]);
    rows.forEach((r) => {
      const s = r.sector || "Unknown";
      bySector[s] = (bySector[s] || 0) + weight(r);
    });
  };
  add((r) => collat(r) * (r.max_contracts || 0));
  let perContract = false;
  if (Object.values(bySector).reduce((a, b) => a + b, 0) === 0) {
    add((r) => collat(r));
    perContract = true;
  }
  destroyChart("chart-sector");
  charts["chart-sector"] = new Chart($("#chart-sector"), {
    type: "doughnut",
    data: { labels: Object.keys(bySector),
      datasets: [{ data: Object.values(bySector),
        backgroundColor: Object.keys(bySector).map((_, i) => SERIES[i % SERIES.length]),
        borderColor: COLORS.panel, borderWidth: 2, hoverOffset: 6 }] },
    options: { cutout: "58%",
      plugins: { legend: { position: "bottom", labels: { boxWidth: 10, boxHeight: 10, padding: 12, usePointStyle: true } },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${fmtUsd(c.raw)}${perContract ? " per contract" : ""}` } } } },
  });
}

function renderRun(doc) {
  window.__currentRun = doc; // the select modal reads run_date/demo/spot from here
  renderRegime(doc);
  renderTopPick(doc);
  renderCards(doc);
  tableState.rows = (doc.rows || []).map(enrich);
  renderTable();
  renderNearMisses(doc);
  renderKpis(doc);
  renderRejectionChart(doc);
  renderRunCharts(doc);

  const t = doc.thresholds || {};
  const dte = t.dte || {}, delta = t.delta || {};
  $("#thresholds-summary").textContent =
    `Screener settings — expiry window ${dte.min}-${dte.max} days (target ${dte.target}), ` +
    `|Δ| ${delta.min}-${delta.max} (target ${delta.target}), ranking: ${t.scoring_mode || "—"}` +
    (t.unknown_earnings_policy ? `, unknown earnings: ${t.unknown_earnings_policy}` : "") + ".";
}

// -------------------------------------------------------------- outcomes render
function renderOutcomes(doc) {
  const outcomes = Object.values((doc && doc.outcomes) || {});
  if (!outcomes.length) return; // section stays hidden until contracts resolve
  $("#outcomes-section").classList.remove("hidden");

  const s = doc.summary || {};
  const fmtAgg = (a) => (a && a.n
    ? `${fmtPct(a.win_rate)} win (${a.wins}/${a.n}), avg return ${fmtPct(a.avg_realized_roc)}`
    : "—");
  $("#outcome-cards").innerHTML = [
    ["Accepted ideas", fmtAgg(s.candidates)],
    ["Excluded ideas (near misses)", fmtAgg(s.near_misses)],
  ].map(([l, v]) => `<div class="card"><div class="label">${l}</div><div class="value">${v}</div></div>`)
    .join("");

  // Win rate per rejection code — the gate-calibration chart.
  const byCode = Object.entries(s.by_rejection_code || {}).filter(([, a]) => a.n);
  const card = $("#chart-outcome-winrate-card");
  if (HAS_CHART && byCode.length) {
    card.classList.remove("hidden");
    destroyChart("chart-outcome-winrate");
    charts["chart-outcome-winrate"] = new Chart($("#chart-outcome-winrate"), {
      type: "bar",
      data: { labels: byCode.map(([k, a]) => `${friendly(k)} (n=${a.n})`),
        datasets: [{ data: byCode.map(([, a]) => (a.win_rate || 0) * 100),
          backgroundColor: SERIES[0], maxBarThickness: 20 }] },
      options: { indexAxis: "y", layout: { padding: { right: 34 } },
        plugins: { legend: { display: false },
          tooltip: { callbacks: { label: (c) => `${c.raw.toFixed(1)}% win rate` } } },
        scales: { x: axisX({ min: 0, max: 100, title: { display: true, text: "Win rate %" } }),
          y: axisY({ grid: { display: false } }) } },
      plugins: [barValueLabels((v) => `${Math.round(v)}%`)],
    });
  }

  // Most recently resolved contracts.
  const recent = [...outcomes]
    .sort((a, b) => String(b.expiration).localeCompare(String(a.expiration)))
    .slice(0, 20);
  $("#outcomes tbody").innerHTML = recent.map((o) => `<tr>
      <td>${o.run_date ?? ""}</td>
      <td>${esc(o.ticker ?? "")}</td>
      <td>${o.expiration ?? ""}</td>
      <td class="num">${fmtNum(o.strike)}</td>
      <td class="num">${fmtNum(o.premium)}</td>
      <td class="num">${fmtNum(o.expiry_close)}</td>
      <td>${o.win
        ? `<span class="badge-flag" title="The stock stayed above the strike — the seller kept the full premium.">WIN</span>`
        : `<span class="badge-reject" title="The stock closed below the strike — the seller would have to buy the shares.">BREACH</span>`}</td>
      <td class="num">${fmtPct(o.realized_roc)}</td>
      <td>${o.group === "candidate" ? "accepted" : "near miss"}</td>
    </tr>`).join("");
}

// -------------------------------------------------------------- history render
function renderHistory(index) {
  // Demo seed runs would mix fake scores into the real time-series.
  const runs = (index.runs || []).filter((r) => r.date && !r.demo);
  const labels = runs.map((r) => r.date);

  // Regime traffic light as a status-strip timeline — one thin segment per run,
  // colored by light. Pure HTML, so it renders even if the Chart.js CDN fails.
  const strip = $("#hist-regime");
  if (strip) {
    strip.innerHTML = runs.map((r) => {
      const cls = (r.light || "unknown").toLowerCase();
      return `<div class="seg seg-${cls}" title="${r.date}: ${r.light || "—"}"></div>`;
    }).join("");
    const axis = $("#hist-regime-axis");
    if (axis) axis.innerHTML = labels.length
      ? `<span>${labels[0]}</span><span>${labels[labels.length - 1]}</span>` : "";
  }

  if (!HAS_CHART) return;

  const line = (id, data, label, color, fill = false) => {
    destroyChart(id);
    charts[id] = new Chart($(id), {
      type: "line",
      data: { labels, datasets: [{ label, data, borderColor: color,
        backgroundColor: fill ? tint(color, 0.14) : color, fill: fill ? "origin" : false,
        pointBackgroundColor: color }] },
      options: { plugins: { legend: { display: false } },
        scales: { x: axisX(), y: axisY() } },
    });
  };
  line("#hist-top-score", runs.map((r) => r.top_score), "Top score", SERIES[0], true);
  line("#hist-deployed", runs.map((r) => (r.pct_deployed || 0) * 100), "% committed", SERIES[3]);

  // Candidates + near misses share one chart; old index rows lack near_miss_count.
  destroyChart("#hist-count");
  charts["#hist-count"] = new Chart($("#hist-count"), {
    type: "line",
    data: { labels, datasets: [
      { label: "Ideas", data: runs.map((r) => r.row_count),
        borderColor: SERIES[0], backgroundColor: SERIES[0], pointBackgroundColor: SERIES[0] },
      { label: "Near misses", data: runs.map((r) => r.near_miss_count ?? null),
        borderColor: COLORS.muted, backgroundColor: COLORS.muted, pointBackgroundColor: COLORS.muted,
        borderDash: [6, 4] },
    ] },
    options: { plugins: { legend: { display: true, position: "bottom" } },
      scales: { x: axisX(), y: axisY({ ticks: { precision: 0 } }) } },
  });
}

// ------------------------------------------------------------------- bootstrap
function populatePicker(index, onPick) {
  const picker = $("#run-picker");
  const dates = (index.runs || []).map((r) => r.date).filter(Boolean).reverse();
  picker.innerHTML = dates.map((d) => `<option value="${d}">${d}</option>`).join("");
  picker.value = index.latest || dates[0] || "";
  picker.addEventListener("change", () => onPick(picker.value));
}

async function loadRun(date) {
  const path = date ? `data/runs/${date}.json` : "data/latest.json";
  try {
    const doc = await fetchJson(path);
    if (doc.meta && doc.meta.demo) $("#demo-notice").classList.remove("hidden");
    renderRun(doc);
  } catch (e) {
    showError(`Could not load run ${date || "(latest)"}: ${e.message}`);
  }
}

async function main() {
  wireTableSort();
  wireSelectButtons();
  let index;
  try {
    index = await fetchJson("data/index.json");
  } catch (e) {
    showError("No data yet — the screener has not produced a run. " +
      "Run `python main.py --json-out site/data/runs/$(date +%F).json` then " +
      "`python scripts/build_index.py`. (" + e.message + ")");
    return;
  }
  window.__latestRunDate = index.latest; // select modal warns on older runs
  renderHistory(index);
  populatePicker(index, loadRun);
  // Outcomes exist only after the first tracked contracts expire — 404 is fine.
  fetchJson("data/outcomes.json").then(renderOutcomes).catch(() => {});
  await loadRun(index.latest);
  if (typeof GYBTrack !== "undefined") GYBTrack.init();
}

main();
