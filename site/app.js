"use strict";

// Colors mirror styles.css :root
const COLORS = {
  green: "#1a9850", yellow: "#e0a800", red: "#d73027",
  accent: "#4a9eff", muted: "#8b98a5", grid: "#2d3743",
};
const REGIME_COLOR = { GREEN: COLORS.green, YELLOW: COLORS.yellow, RED: COLORS.red };

const charts = {}; // id -> Chart instance, so we can destroy/redraw on run switch
let tableState = { key: "score", dir: -1, rows: [] };

const $ = (sel) => document.querySelector(sel);
const fmtPct = (x) => (x == null ? "—" : (x * 100).toFixed(1) + "%");
const fmtUsd = (x) => (x == null ? "—" : "$" + Math.round(x).toLocaleString());
const fmtNum = (x, d = 2) => (x == null ? "—" : Number(x).toFixed(d));
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
const badge = (cls) => (e) =>
  `<span class="${cls}" title="${esc(e.message || "")}">${esc(e.code || "?")}</span>`;

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
  const tripped = (doc.regime && doc.regime.tripped) || [];
  $("#regime-tripped").textContent = tripped.length
    ? "Signals tripped: " + tripped.join(", ")
    : "No risk signals tripped.";
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
    el.insertAdjacentHTML("beforeend", `<span class="badge-stale" title="This run executed outside regular US market hours — option bid/asks may be stale or zeroed, so gate results are unreliable.">OFF-HOURS DATA</span>`);
  }
}

function renderCards(doc) {
  const h = doc.header || {};
  const rows = doc.rows || [];
  const nearMissCount = (doc.near_misses || []).length;
  const candidates = String(rows.length) +
    (nearMissCount ? ` (+${nearMissCount} near miss${nearMissCount > 1 ? "es" : ""})` : "");
  const cards = [
    ["Total capital", fmtUsd(h.total_capital)],
    ["Deployed", `${fmtUsd(h.deployed)} (${fmtPct(h.pct_deployed)})`],
    ["Remaining cash", fmtUsd(h.remaining_cash)],
    ["Candidates", candidates],
    ["Positions", h.positions_source || "—"],
  ];
  // Only v3+ rows carry `affordable`; skip the card for older snapshots.
  if (rows.some((r) => r.affordable != null)) {
    const n = rows.filter((r) => r.affordable).length;
    cards.splice(4, 0, ["Tradeable", rows.length
      ? `${n} of ${rows.length} fit the per-name cap` : "—"]);
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
      ? `<span class="badge-breach" title="Min account ${fmtUsd(r.min_account_for_1_contract)}">BREACH</span>`
      : "") + (r.data_flags || []).map(badge("badge-flag")).join("");
    return `<tr>
      <td>${r.ticker ?? ""}</td>
      <td>${r.sector ?? ""}</td>
      <td>${r.expiration ?? ""}</td>
      <td class="num">${r.dte ?? ""}</td>
      <td class="num">${fmtNum(r.strike)}</td>
      <td class="num">${fmtNum(r.mid)}</td>
      <td class="num">${fmtNum(r.abs_delta)}</td>
      <td class="num">${fmtPct(r.annualized_yield)}</td>
      <td class="num">${fmtPct(r.distance_to_strike)}</td>
      <td class="num">${fmtNum(r.score, 3)}</td>
      <td class="num">${r.max_contracts ?? ""}</td>
      <td>${flag}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="12" class="muted">No qualifying candidates.</td></tr>`;
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
  const rows = doc.near_misses || [];
  const section = $("#near-miss-section");
  if (!rows.length) { section.classList.add("hidden"); return; }
  section.classList.remove("hidden");
  $("#near-misses tbody").innerHTML = rows.map((r) => `<tr>
      <td>${r.ticker ?? ""}</td>
      <td>${r.sector ?? ""}</td>
      <td>${r.expiration ?? ""}</td>
      <td class="num">${r.dte ?? ""}</td>
      <td class="num">${fmtNum(r.strike)}</td>
      <td class="num">${fmtNum(r.mid)}</td>
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
    data: { labels: entries.map(([k]) => k),
      datasets: [{ data: entries.map(([, v]) => v), backgroundColor: COLORS.yellow }] },
    options: { indexAxis: "y", plugins: { legend: { display: false } },
      scales: { x: { grid: { color: COLORS.grid }, ticks: { precision: 0 } },
        y: { grid: { display: false } } } },
  });
}

function renderRunCharts(doc) {
  if (!HAS_CHART) return;
  const rows = doc.rows || [];

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
      tooltip: { callbacks: { label: (c) => `${c.raw.ticker}: ${c.raw.y.toFixed(1)}% ann @ ${c.raw.x.toFixed(1)}% dist` } } },
      scales: { x: { title: { display: true, text: "Distance to strike %" }, grid: { color: COLORS.grid } },
        y: { title: { display: true, text: "Annualized yield %" }, grid: { color: COLORS.grid } } } },
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
    data: { labels: ["Deployed", "Remaining"],
      datasets: [{ data: [pct, Math.max(0, 1 - pct)],
        backgroundColor: [COLORS.accent, COLORS.grid] }] },
    options: { circumference: 180, rotation: -90, cutout: "70%",
      plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => `${c.label}: ${(c.raw * 100).toFixed(1)}%` } } } },
  });
}

function renderRun(doc) {
  renderRegime(doc);
  renderCards(doc);
  tableState.rows = doc.rows || [];
  renderTable();
  renderNearMisses(doc);
  renderRejectionChart(doc);
  renderRunCharts(doc);

  const t = doc.thresholds || {};
  const dte = t.dte || {}, delta = t.delta || {};
  $("#thresholds-summary").textContent =
    `Thresholds — DTE ${dte.min}-${dte.max} (target ${dte.target}), ` +
    `|Δ| ${delta.min}-${delta.max} (target ${delta.target}), scoring: ${t.scoring_mode || "—"}` +
    (t.unknown_earnings_policy ? `, unknown earnings: ${t.unknown_earnings_policy}` : "") + ".";
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
  line("#hist-deployed", runs.map((r) => (r.pct_deployed || 0) * 100), "% deployed", COLORS.yellow);

  // Candidates + near misses share one chart; old index rows lack near_miss_count.
  destroyChart("#hist-count");
  charts["#hist-count"] = new Chart($("#hist-count"), {
    type: "line",
    data: { labels, datasets: [
      { label: "Candidates", data: runs.map((r) => r.row_count),
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
  let index;
  try {
    index = await fetchJson("data/index.json");
  } catch (e) {
    showError("No data yet — the screener has not produced a run. " +
      "Run `python main.py --json-out site/data/runs/$(date +%F).json` then " +
      "`python scripts/build_index.py`. (" + e.message + ")");
    return;
  }
  renderHistory(index);
  populatePicker(index, loadRun);
  await loadRun(index.latest);
}

main();
