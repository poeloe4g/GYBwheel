"use strict";

// Colors mirror styles.css :root
const COLORS = {
  green: "#1a9850", yellow: "#e0a800", red: "#d73027",
  accent: "#4a9eff", muted: "#8b98a5", grid: "#2d3743",
};
const REGIME_COLOR = { GREEN: COLORS.green, YELLOW: COLORS.yellow, RED: COLORS.red };

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
}

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
      <td class="num">${fmtPct(r.distance_to_strike)}</td>
      <td class="num">${fmtNum(r.score, 3)}</td>
      <td class="num">${r.max_contracts ?? ""}</td>
      <td>${flag}</td>
      <td>${select}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="14" class="muted">No ideas passed every safety check today. Check the near misses below to see what almost made it.</td></tr>`;
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
      datasets: [{ data: entries.map(([, v]) => v), backgroundColor: COLORS.yellow }] },
    options: { indexAxis: "y", plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => `${c.raw} stock${c.raw === 1 ? "" : "s"}` } } },
      scales: { x: { grid: { color: COLORS.grid }, ticks: { precision: 0 } },
        y: { grid: { display: false } } } },
  });
}

function renderRunCharts(doc) {
  if (!HAS_CHART) return;
  const rows = (doc.rows || []).map(enrich);

  // Top candidates by score (horizontal bar)
  const top = [...rows].sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 10);
  destroyChart("chart-top-score");
  charts["chart-top-score"] = new Chart($("#chart-top-score"), {
    type: "bar",
    data: { labels: top.map((r) => r.ticker),
      datasets: [{ data: top.map((r) => r.score), backgroundColor: COLORS.accent }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } },
      scales: { x: { grid: { color: COLORS.grid } }, y: { grid: { display: false } } } },
  });

  // Yield vs distance scatter (bubble size ~ max_contracts)
  destroyChart("chart-yield-distance");
  charts["chart-yield-distance"] = new Chart($("#chart-yield-distance"), {
    type: "bubble",
    data: { datasets: [{
      data: rows.map((r) => ({ x: (r.distance_to_strike || 0) * 100,
        y: (r.annualized_yield || 0) * 100, r: 4 + 2 * (r.max_contracts || 0), ticker: r.ticker })),
      backgroundColor: "rgba(74,158,255,0.55)" }] },
    options: { plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => `${c.raw.ticker}: ${c.raw.y.toFixed(1)}% yearly yield with a ${c.raw.x.toFixed(1)}% cushion` } } },
      scales: { x: { title: { display: true, text: "Safety cushion % (room to fall)" }, grid: { color: COLORS.grid } },
        y: { title: { display: true, text: "Yearly yield %" }, grid: { color: COLORS.grid } } } },
  });

  // Deployable collateral by sector (doughnut)
  const bySector = {};
  rows.forEach((r) => {
    const v = (r.collateral_per_contract || 0) * (r.max_contracts || 0);
    bySector[r.sector || "Unknown"] = (bySector[r.sector || "Unknown"] || 0) + v;
  });
  const palette = [COLORS.accent, COLORS.green, COLORS.yellow, COLORS.red, "#9b59b6", "#16a085", "#e67e22"];
  destroyChart("chart-sector");
  charts["chart-sector"] = new Chart($("#chart-sector"), {
    type: "doughnut",
    data: { labels: Object.keys(bySector),
      datasets: [{ data: Object.values(bySector),
        backgroundColor: Object.keys(bySector).map((_, i) => palette[i % palette.length]) }] },
    options: { plugins: { legend: { position: "bottom" } } },
  });

  // Capital deployed gauge (doughnut)
  const pct = (doc.header && doc.header.pct_deployed) || 0;
  destroyChart("chart-deployed");
  charts["chart-deployed"] = new Chart($("#chart-deployed"), {
    type: "doughnut",
    data: { labels: ["Committed", "Available"],
      datasets: [{ data: [pct, Math.max(0, 1 - pct)],
        backgroundColor: [COLORS.accent, COLORS.grid] }] },
    options: { circumference: 180, rotation: -90, cutout: "70%",
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${(c.raw * 100).toFixed(1)}%` } } } },
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
          backgroundColor: COLORS.accent }] },
      options: { indexAxis: "y", plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.raw.toFixed(1)}% win rate` } } },
        scales: { x: { grid: { color: COLORS.grid }, min: 0, max: 100,
          title: { display: true, text: "Win rate %" } },
          y: { grid: { display: false } } } },
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
  if (!HAS_CHART) return;
  // Demo seed runs would mix fake scores into the real time-series.
  const runs = (index.runs || []).filter((r) => r.date && !r.demo);
  const labels = runs.map((r) => r.date);

  destroyChart("hist-regime");
  charts["hist-regime"] = new Chart($("#hist-regime"), {
    type: "bar",
    data: { labels, datasets: [{
      data: runs.map(() => 1),
      backgroundColor: runs.map((r) => REGIME_COLOR[r.light] || COLORS.muted) }] },
    options: { plugins: { legend: { display: false },
      tooltip: { callbacks: { label: (c) => runs[c.dataIndex].light } } },
      scales: { y: { display: false }, x: { grid: { display: false } } } },
  });

  const line = (id, data, label, color) => {
    destroyChart(id);
    charts[id] = new Chart($(id), {
      type: "line",
      data: { labels, datasets: [{ label, data, borderColor: color,
        backgroundColor: color, tension: 0.2, pointRadius: 3 }] },
      options: { plugins: { legend: { display: false } },
        scales: { x: { grid: { color: COLORS.grid } }, y: { grid: { color: COLORS.grid } } } },
    });
  };
  line("#hist-top-score", runs.map((r) => r.top_score), "Top score", COLORS.accent);
  line("#hist-deployed", runs.map((r) => (r.pct_deployed || 0) * 100), "% committed", COLORS.yellow);

  // Candidates + near misses share one chart; old index rows lack near_miss_count.
  destroyChart("#hist-count");
  charts["#hist-count"] = new Chart($("#hist-count"), {
    type: "line",
    data: { labels, datasets: [
      { label: "Ideas", data: runs.map((r) => r.row_count),
        borderColor: COLORS.green, backgroundColor: COLORS.green, tension: 0.2, pointRadius: 3 },
      { label: "Near misses", data: runs.map((r) => r.near_miss_count ?? null),
        borderColor: COLORS.muted, backgroundColor: COLORS.muted,
        borderDash: [6, 4], tension: 0.2, pointRadius: 3 },
    ] },
    options: { plugins: { legend: { display: true, position: "bottom" } },
      scales: { x: { grid: { color: COLORS.grid } },
        y: { grid: { color: COLORS.grid }, ticks: { precision: 0 } } } },
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
