"use strict";

// My picks: select ideas, record them in site/data/selections.json on GitHub,
// and show how they turned out. Loaded after app.js — shares its top-level
// helpers (fmtUsd, fmtPct, fmtNum, esc, fetchJson, COLORS, HAS_CHART,
// destroyChart, charts, $).

// Where selections are written. branch may be pointed at a scratch branch
// while developing the write path.
const GH = {
  owner: "poeloe4g",
  repo: "GYBwheel",
  branch: "main",
  path: "site/data/selections.json",
};
const TOKEN_KEY = "gyb_token";

const GYBTrack = (() => {
  // doc: the selections document; sha: its blob sha at last read (write
  // concurrency token); writable: a working GitHub key is present.
  const state = { doc: null, sha: null, writable: false, stale: false };

  const RESULT_BADGE = {
    EXPIRED_WIN: `<span class="badge-win" title="EXPIRED_WIN: the stock stayed above your buy-at price — you kept the full premium.">KEPT THE CASH</span>`,
    ASSIGNED: `<span class="badge-reject" title="ASSIGNED: the stock ended below your buy-at price — you own the shares; the loss is marked at the expiry-day price.">OWN THE SHARES</span>`,
    EARLY_CLOSED: `<span class="badge-flag" title="EARLY_CLOSED: you bought the put back before it ended.">CLOSED EARLY</span>`,
  };

  // ------------------------------------------------------------ token + API
  const getToken = () => localStorage.getItem(TOKEN_KEY) || "";

  const apiUrl = () =>
    `https://api.github.com/repos/${GH.owner}/${GH.repo}/contents/${GH.path}`;

  const apiHeaders = () => ({
    Authorization: `Bearer ${getToken()}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
  });

  const b64decode = (b64) => new TextDecoder().decode(
    Uint8Array.from(atob(b64.replace(/\s/g, "")), (c) => c.charCodeAt(0)));
  const b64encode = (str) => {
    let bin = "";
    new TextEncoder().encode(str).forEach((b) => { bin += String.fromCharCode(b); });
    return btoa(bin);
  };

  async function ghGetFile() {
    const res = await fetch(`${apiUrl()}?ref=${GH.branch}`, { headers: apiHeaders(), cache: "no-store" });
    if (!res.ok) throw new Error(`GitHub said ${res.status} while reading your picks`);
    return res.json(); // { content: base64, sha, ... }
  }

  // Re-reads the file, applies mutate to the fresh copy, writes it back. One
  // retry on a concurrent write (409/422 stale sha) — mutations are appends /
  // in-place patches, so re-applying to fresher content is safe.
  async function saveSelections(mutate, message) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const file = await ghGetFile();
      const doc = JSON.parse(b64decode(file.content));
      doc.selections = doc.selections || [];
      mutate(doc);
      doc.updated_at = new Date().toISOString();
      const res = await fetch(apiUrl(), {
        method: "PUT",
        headers: apiHeaders(),
        body: JSON.stringify({
          message,
          content: b64encode(JSON.stringify(doc, null, 2) + "\n"),
          sha: file.sha,
          branch: GH.branch,
        }),
      });
      if (res.ok) {
        const out = await res.json();
        state.doc = doc;
        state.sha = out.content && out.content.sha;
        state.stale = false;
        renderMyPicks();
        return out.commit && out.commit.sha;
      }
      if (res.status !== 409 && res.status !== 422) {
        const err = await res.json().catch(() => ({}));
        throw new Error(`GitHub said ${res.status}${err.message ? `: ${err.message}` : ""}`);
      }
      // stale sha — loop refetches and re-applies
    }
    throw new Error("Your picks file changed twice while saving — reload the page and try again.");
  }

  async function loadSelections() {
    if (getToken()) {
      try {
        const file = await ghGetFile();
        state.doc = JSON.parse(b64decode(file.content));
        state.sha = file.sha;
        state.writable = true;
        state.stale = false;
        return;
      } catch (e) {
        state.writable = false; // bad/revoked key — fall back to published copy
        setTokenStatus(`✗ Saved key no longer works (${e.message}). Paste a new one.`, false);
      }
    }
    try {
      state.doc = await fetchJson("data/selections.json");
      state.stale = true;
    } catch (e) {
      state.doc = { schema_version: 1, updated_at: null, selections: [], summary: null };
      state.stale = true;
    }
  }

  // --------------------------------------------------------------- token UI
  function setTokenStatus(msg, ok) {
    const el = $("#token-status");
    el.textContent = msg;
    el.style.color = ok == null ? "" : ok ? COLORS.green : COLORS.red;
  }

  function refreshSetupBox() {
    const has = Boolean(getToken());
    $("#token-forget").classList.toggle("hidden", !has);
    $("#picks-setup-summary").textContent = has && state.writable
      ? "Connected — picks save from this page ✓"
      : "Connect to save your picks";
  }

  function wireSetup() {
    $("#token-save").addEventListener("click", async () => {
      const t = $("#token-input").value.trim();
      if (!t) { setTokenStatus("Paste a key first.", false); return; }
      localStorage.setItem(TOKEN_KEY, t);
      setTokenStatus("Testing the key…", null);
      try {
        await ghGetFile();
        state.writable = true;
        setTokenStatus("✓ Connected. Picks now save straight from this page.", true);
        $("#token-input").value = "";
        await loadSelections();
        renderMyPicks();
      } catch (e) {
        localStorage.removeItem(TOKEN_KEY);
        state.writable = false;
        setTokenStatus(`✗ That key didn't work (${e.message}). Check it can read and write Contents for this project only.`, false);
      }
      refreshSetupBox();
    });
    $("#token-forget").addEventListener("click", () => {
      localStorage.removeItem(TOKEN_KEY);
      state.writable = false;
      setTokenStatus("Key forgotten. Your picks are untouched — reconnect any time.", null);
      refreshSetupBox();
      renderMyPicks();
    });
  }

  // ------------------------------------------------------------ select modal
  let pendingRow = null;
  // Set while the live-data section holds a valid broker quote:
  // { inputs: {bid, ask, spot, strike, expiration}, result: computeVerification() }
  let pendingVerify = null;

  function selectedPremiumPerShare(row) {
    return row.premium_used != null ? row.premium_used : row.mid;
  }

  function updateSelectMath() {
    if (!pendingRow) return;
    const input = $("#sel-contracts");
    // The screener's account-limits cap; not re-sized for an overridden strike.
    const max = Math.max(1, pendingRow.max_contracts || 1);
    let n = Math.floor(Number(input.value) || 1);
    n = Math.min(Math.max(n, 1), max);
    input.value = n;
    const strike = pendingVerify ? pendingVerify.inputs.strike : null;
    const cash = pendingVerify
      ? strike * 100 * n
      : (pendingRow.collateral_per_contract || pendingRow.strike * 100) * n;
    const premium = pendingVerify
      ? pendingVerify.result.metrics.premium_used
      : selectedPremiumPerShare(pendingRow);
    $("#sel-cash").textContent = fmtUsd(cash);
    $("#sel-premium").textContent = fmtUsd((premium || 0) * 100 * n);
  }

  // --------------------------------------------- live-data verification panel
  const verifyThresholds = () => ((window.__currentRun || {}).thresholds) || {};
  const canVerify = () =>
    Boolean(verifyThresholds().quality) && typeof GYBVerify !== "undefined";

  function resetVerifyPanel(row) {
    const panel = $("#sel-verify");
    pendingVerify = null;
    panel.open = false;
    panel.classList.toggle("hidden", !canVerify());
    if (!canVerify()) return;
    $("#vf-strike").value = row.strike ?? "";
    $("#vf-expiration").value = row.expiration ?? "";
    ["#vf-bid", "#vf-ask", "#vf-spot"].forEach((sel) => { $(sel).value = ""; });
    $("#vf-bid").placeholder = row.bid != null ? `screener: ${fmtNum(row.bid)}` : "";
    $("#vf-ask").placeholder = row.ask != null ? `screener: ${fmtNum(row.ask)}` : "";
    $("#vf-spot").placeholder = row.spot != null ? `screener: ${fmtNum(row.spot)}` : "";
    $("#vf-result").classList.add("hidden");
  }

  const GATE_ICON = { pass: "✓", fail: "✗", flag: "⚠" };
  // Neutral checklist names — app.js's FRIENDLY_CODE labels are phrased for
  // failures ("Premium too small") and read wrong next to a green check.
  const GATE_LABEL = {
    yield_30dte: "Premium size", spread: "Bid-ask spread",
    distance: "Price cushion", implied_move: "Expected volatility",
    delta_band: "Odds of keeping the premium",
    open_interest: "Liquidity", oi_unknown: "Liquidity",
    earnings: "Earnings date", earnings_unknown: "Earnings date",
    expired: "Expiration", no_premium: "Premium",
    crossed_quote: "Quote sanity", one_sided_quote: "Quote sanity",
    iv_stale: "Volatility data", iv_missing: "Volatility data",
  };
  const gateLabel = (code) => GATE_LABEL[code] || friendly(code);

  function renderVerifyResult(result, row) {
    const box = $("#vf-result");
    if (!result) {
      pendingVerify = null;
      box.classList.add("hidden");
      updateSelectMath();
      return;
    }
    box.classList.remove("hidden");
    const verdictEl = $("#vf-verdict");
    verdictEl.className = `verdict-pill verdict-${result.verdict}`;
    verdictEl.textContent = result.verdict === "green" ? "Still a good pick"
      : result.verdict === "amber" ? "OK, with caveats" : "Fails a safety check";
    const m = result.metrics;
    const vs = (live, screener, fmt) => `${fmt(live)} (screener: ${fmt(screener)})`;
    $("#vf-metrics").textContent = [
      `Premium ${vs(m.premium_used != null ? m.premium_used * 100 : null,
        selectedPremiumPerShare(row) != null ? selectedPremiumPerShare(row) * 100 : null, fmtUsd)}`,
      `odds of keeping it ${vs(m.pop, row._pop != null ? row._pop : row.pop, fmtPct0)}`,
      `yearly yield ${vs(m.annualized_yield, row.annualized_yield, fmtPct)}`,
      `score ${vs(m.score, row.score, (x) => fmtNum(x, 3))}`,
      m.iv != null ? `IV ${fmtPct0(m.iv)} (${m.iv_source === "solved" ? "from your quote" : "screener's"})` : null,
    ].filter(Boolean).join(" · ");
    $("#vf-gates").innerHTML = result.gates.map((g) =>
      `<li class="gate-${esc(g.status)}" title="${esc(g.code + ": " + g.message)}">` +
      `${GATE_ICON[g.status] || ""} ${esc(gateLabel(g.code))} — ${esc(g.message)}</li>`).join("");
  }

  function onVerifyInput() {
    if (!pendingRow || !canVerify()) return;
    const inputs = {
      bid: parseFloat($("#vf-bid").value),
      ask: parseFloat($("#vf-ask").value),
      spot: parseFloat($("#vf-spot").value),
      strike: parseFloat($("#vf-strike").value),
      expiration: $("#vf-expiration").value,
    };
    const complete = [inputs.bid, inputs.ask, inputs.spot, inputs.strike]
      .every(Number.isFinite) && inputs.expiration;
    const result = complete
      ? GYBVerify.computeVerification(inputs, pendingRow, verifyThresholds())
      : null;
    pendingVerify = result ? { inputs, result } : null;
    renderVerifyResult(result, pendingRow);
    updateSelectMath();
  }

  function clearVerify() {
    if (pendingRow) resetVerifyPanel(pendingRow);
    updateSelectMath();
  }

  function openSelectModal(row) {
    const run = window.__currentRun || {};
    const meta = run.meta || {};
    pendingRow = row;
    resetVerifyPanel(row);
    $("#sel-title").textContent = `Select ${row.ticker} — sell a put at ${fmtUsd(row.strike)}`;
    $("#sel-desc").textContent =
      `Ends ${row.expiration} (${row.dte} days). You collect the premium now and keep it ` +
      `as long as ${row.ticker} stays above ${fmtUsd(row.strike)}.`;
    const warnEl = $("#sel-warning");
    const warnings = [];
    if (meta.demo) warnings.push("This is demo seed data — picks from it can't be tracked.");
    else if (meta.run_date && window.__latestRunDate && meta.run_date !== window.__latestRunDate) {
      warnings.push(`You're viewing the ${meta.run_date} run, not the latest — these prices are stale.`);
    }
    if (meta.quotes_trusted === false) {
      warnings.push("This run used off-hours prices — the premium shown may be unrealistic.");
    }
    warnEl.textContent = warnings.join(" ");
    warnEl.classList.toggle("hidden", !warnings.length);
    $("#sel-error").classList.add("hidden");
    const max = Math.max(1, row.max_contracts || 1);
    $("#sel-contracts").max = max;
    $("#sel-contracts").value = 1;
    $("#sel-max").textContent = max > 1 ? `(up to ${max} fit your limits)` : "(only 1 fits your limits)";
    const btn = $("#sel-confirm");
    btn.disabled = Boolean(meta.demo);
    btn.textContent = state.writable ? "Save pick" : "Set up a key first (below the My picks heading)";
    updateSelectMath();
    $("#select-dialog").showModal();
  }

  async function confirmSelect() {
    if (!pendingRow) return;
    if (!state.writable) {
      $("#select-dialog").close();
      $("#my-picks").scrollIntoView({ behavior: "smooth" });
      $("#picks-setup").open = true;
      return;
    }
    const row = pendingRow;
    const runDate = ((window.__currentRun || {}).meta || {}).run_date || "unknown";
    const n = Math.floor(Number($("#sel-contracts").value) || 1);
    const selectedAt = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
    // With a live verification, the entry records the contract and prices
    // actually traded; the screener's own numbers stay under verify.*.
    const v = pendingVerify;
    const strike = v ? v.inputs.strike : row.strike;
    const expiration = v ? v.inputs.expiration : row.expiration;
    const key = `${runDate}|${row.ticker}|${expiration}|${strike}`;
    const entry = {
      uid: `${key}|${selectedAt}`,
      key,
      symbol: v && v.result.contract_overridden ? null : (row.symbol ?? null),
      run_date: runDate,
      ticker: row.ticker,
      sector: row.sector ?? "Unknown",
      option_type: row.option_type ?? "put",
      strike,
      expiration,
      dte_at_entry: v ? v.result.metrics.dte : (row.dte ?? null),
      contracts: n,
      entry_premium: v ? v.result.metrics.premium_used : selectedPremiumPerShare(row),
      entry_premium_basis: v ? v.result.metrics.premium_basis : (row.premium_basis ?? "mid"),
      entry_bid: v ? v.inputs.bid : (row.bid ?? null),
      entry_mid: v ? v.result.metrics.mid : (row.mid ?? null),
      spot_at_entry: v ? v.inputs.spot : (row.spot ?? null),
      collateral: strike * 100 * n,
      max_contracts_at_entry: row.max_contracts ?? null,
      selected_at: selectedAt,
      status: "OPEN",
      close: null,
    };
    if (v) {
      const m = v.result.metrics;
      entry.live_verified = true;
      entry.verify = {
        verified_at: selectedAt,
        bid: v.inputs.bid, ask: v.inputs.ask, spot: v.inputs.spot,
        iv: m.iv, iv_source: m.iv_source,
        abs_delta: m.abs_delta, implied_move: m.implied_move,
        yield_30dte: m.yield_30dte, annualized_yield: m.annualized_yield,
        distance_to_strike: m.distance_to_strike,
        score: m.score, score_mode: m.score_mode,
        screener_score: row.score ?? null,
        verdict: v.result.verdict,
        gates: v.result.gates,
        contract_overridden: v.result.contract_overridden,
        screener_contract: { strike: row.strike, expiration: row.expiration },
      };
    }
    const btn = $("#sel-confirm");
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      const commit = await saveSelections(
        (doc) => doc.selections.push(entry),
        `selection: ${row.ticker} ${fmtNum(strike, 0)}P x${n} (run ${runDate})` +
          (v ? ` (verified ${v.result.verdict})` : ""));
      $("#select-dialog").close();
      setPicksStatus(`Saved ✓ — ${row.ticker} ×${n} is now tracked under My picks` +
        (commit ? ` (commit ${commit.slice(0, 7)})` : "") + ".");
    } catch (e) {
      const errEl = $("#sel-error");
      errEl.textContent = `Could not save: ${e.message} — your pick was NOT recorded. ` +
        "Check your connection and try again.";
      errEl.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Save pick";
    }
  }

  // ------------------------------------------------------- early-close modal
  let pendingClose = null;

  function updateCloseMath() {
    if (!pendingClose) return;
    const buyback = Number($("#cls-buyback").value);
    const el = $("#cls-result");
    if (!$("#cls-buyback").value || Number.isNaN(buyback) || buyback < 0) {
      el.textContent = "—";
      return;
    }
    const pnl = (pendingClose.entry_premium - buyback) * 100 * pendingClose.contracts;
    el.textContent = `${pnl >= 0 ? "+" : "−"}${fmtUsd(Math.abs(pnl))}`;
    el.style.color = pnl >= 0 ? COLORS.green : COLORS.red;
  }

  function openCloseModal(sel) {
    pendingClose = sel;
    $("#cls-title").textContent =
      `Close ${sel.ticker} ${fmtUsd(sel.strike)} early (${sel.contracts} contract${sel.contracts > 1 ? "s" : ""})`;
    $("#cls-buyback").value = "";
    $("#cls-error").classList.add("hidden");
    updateCloseMath();
    $("#close-dialog").showModal();
  }

  async function confirmClose() {
    if (!pendingClose) return;
    const sel = pendingClose;
    const buyback = Number($("#cls-buyback").value);
    if (Number.isNaN(buyback) || buyback < 0 || $("#cls-buyback").value === "") {
      const errEl = $("#cls-error");
      errEl.textContent = "Enter the per-share price you paid to buy the put back (0 or more).";
      errEl.classList.remove("hidden");
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    const pnl = (sel.entry_premium - buyback) * 100 * sel.contracts;
    const roc = sel.collateral ? pnl / sel.collateral : 0;
    const heldDays = Math.max(1, Math.round(
      (Date.parse(today) - Date.parse((sel.selected_at || "").slice(0, 10) || today)) / 86400e3));
    const close = {
      method: "early_close",
      closed_at: today,
      buyback_price: buyback,
      pnl_usd: Math.round(pnl * 100) / 100,
      realized_roc: Math.round(roc * 1e6) / 1e6,
      annualized_realized: Math.round((roc * 365 / heldDays) * 1e6) / 1e6,
      win: pnl >= 0,
    };
    const btn = $("#cls-confirm");
    btn.disabled = true;
    btn.textContent = "Saving…";
    try {
      const commit = await saveSelections((doc) => {
        const target = (doc.selections || []).find((s) => s.uid === sel.uid);
        if (!target) throw new Error("this pick is no longer in the file");
        if (target.status !== "OPEN") throw new Error("this pick was already closed");
        target.status = "EARLY_CLOSED";
        target.close = close;
      }, `selection: early-close ${sel.ticker} ${fmtNum(sel.strike, 0)}P @${buyback}`);
      $("#close-dialog").close();
      setPicksStatus(`Recorded ✓ — ${sel.ticker} closed early` +
        (commit ? ` (commit ${commit.slice(0, 7)})` : "") + ".");
    } catch (e) {
      const errEl = $("#cls-error");
      errEl.textContent = `Could not save: ${e.message} — nothing was recorded.`;
      errEl.classList.remove("hidden");
    } finally {
      btn.disabled = false;
      btn.textContent = "Record close";
    }
  }

  // ----------------------------------------------------------------- render
  function setPicksStatus(msg) {
    $("#picks-status").textContent = msg;
  }

  // Courtesy "where is the stock now" hint from the most recent run that
  // screened this ticker (candidates or near misses).
  function spotNow(ticker) {
    const run = window.__currentRun || {};
    const rows = (run.rows || []).concat(run.near_misses || []);
    const hit = rows.find((r) => r.ticker === ticker && r.spot != null);
    return hit ? hit.spot : null;
  }

  function daysLeft(expiration) {
    return Math.ceil((Date.parse(expiration) - Date.now()) / 86400e3);
  }

  function renderMyPicks() {
    const doc = state.doc || {};
    const selections = (doc.selections || []).filter((s) => s && s.ticker);
    const section = $("#my-picks");
    const hasToken = Boolean(getToken());
    if (!selections.length && !hasToken) { section.classList.add("hidden"); return; }
    section.classList.remove("hidden");
    $("#picks-stale-note").classList.toggle("hidden", !(state.stale && selections.length));
    refreshSetupBox();

    const open = selections.filter((s) => s.status === "OPEN");
    const closed = selections.filter((s) => s.close && s.status !== "OPEN");
    const premiumUsd = (s) => (s.entry_premium || 0) * 100 * (s.contracts || 1);

    // Summary cards
    const wins = closed.filter((s) => s.close.win).length;
    const totalPnl = closed.reduce((t, s) => t + (s.close.pnl_usd || 0), 0);
    const cards = [
      ["Open picks", open.length
        ? `${open.length} — ${fmtUsd(open.reduce((t, s) => t + (s.collateral || 0), 0))} set aside`
        : "none"],
      ["Cash collected up front", fmtUsd(selections.reduce((t, s) => t + premiumUsd(s), 0))],
      ["Result so far", closed.length
        ? `${totalPnl >= 0 ? "+" : "−"}${fmtUsd(Math.abs(totalPnl))}` : "no finished picks yet"],
    ];
    if (closed.length) cards.push(["Picks that kept the cash", `${wins} of ${closed.length}`]);
    $("#picks-cards").innerHTML = cards
      .map(([l, v]) => `<div class="card"><div class="label">${l}</div><div class="value">${v}</div></div>`)
      .join("");

    // Open picks table
    $("#picks-open-block").classList.toggle("hidden", !open.length);
    $("#picks-open tbody").innerHTML = open.map((s) => {
      const spot = spotNow(s.ticker);
      const now = spot == null
        ? `<span class="muted">—</span>`
        : spot > s.strike
          ? `<span class="badge-win" title="At the last run, ${esc(s.ticker)} was at ${fmtUsd(spot)} — above your ${fmtUsd(s.strike)} buy-at price.">above ✓</span>`
          : `<span class="badge-reject" title="At the last run, ${esc(s.ticker)} was at ${fmtUsd(spot)} — below your ${fmtUsd(s.strike)} buy-at price. If it stays there, you'll own the shares.">below ⚠</span>`;
      const left = daysLeft(s.expiration);
      const act = state.writable
        ? `<button type="button" class="btn-select btn-close-early" data-uid="${esc(s.uid)}">Close early…</button>`
        : "";
      const verified = s.live_verified && s.verify
        ? ` <span class="badge-${s.verify.verdict === "green" ? "win" : "flag"}" title="Verified against live broker data at entry — verdict: ${esc(s.verify.verdict)}.">verified</span>`
        : "";
      return `<tr>
        <td>${esc(s.ticker)}${verified}</td>
        <td class="num">${fmtNum(s.strike)}</td>
        <td>${esc(s.expiration || "")}</td>
        <td class="num">${Number.isFinite(left) ? Math.max(left, 0) : "—"}</td>
        <td class="num">${s.contracts ?? 1}</td>
        <td class="num">${fmtUsd(premiumUsd(s))}</td>
        <td class="num">${fmtUsd(s.collateral)}</td>
        <td>${now}</td>
        <td>${act}</td>
      </tr>`;
    }).join("");

    // Finished picks table (most recently closed first)
    $("#picks-closed-block").classList.toggle("hidden", !closed.length);
    $("#picks-closed tbody").innerHTML = [...closed]
      .sort((a, b) => String(b.close.closed_at || "").localeCompare(String(a.close.closed_at || "")))
      .map((s) => `<tr>
        <td>${esc(s.ticker)}</td>
        <td class="num">${fmtNum(s.strike)}</td>
        <td class="num">${s.contracts ?? 1}</td>
        <td>${esc(s.close.closed_at || "")}</td>
        <td>${RESULT_BADGE[s.status] || esc(s.status)}</td>
        <td class="num">${(s.close.pnl_usd ?? 0) >= 0 ? "+" : "−"}${fmtUsd(Math.abs(s.close.pnl_usd ?? 0))}</td>
        <td class="num">${fmtPct(s.close.realized_roc)}</td>
      </tr>`).join("");

    renderPnlChart(closed);
  }

  function renderPnlChart(closed) {
    if (!HAS_CHART) return;
    const card = $("#chart-my-pnl-card");
    destroyChart("chart-my-pnl");
    if (closed.length < 1) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    const points = [...closed]
      .sort((a, b) => String(a.close.closed_at || "").localeCompare(String(b.close.closed_at || "")));
    let cum = 0;
    const data = points.map((s) => { cum += s.close.pnl_usd || 0; return Math.round(cum * 100) / 100; });
    charts["chart-my-pnl"] = new Chart($("#chart-my-pnl"), {
      type: "line",
      data: { labels: points.map((s) => s.close.closed_at),
        datasets: [{ label: "Running P&L $", data, borderColor: COLORS.accent,
          backgroundColor: COLORS.accent, tension: 0.2, pointRadius: 3 }] },
      options: { plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => `Running total: ${c.raw >= 0 ? "+" : "−"}$${Math.abs(c.raw).toLocaleString()}` } } },
        scales: { x: { grid: { color: COLORS.grid } }, y: { grid: { color: COLORS.grid } } } },
    });
  }

  // -------------------------------------------------------------- bootstrap
  function wireModals() {
    $("#sel-minus").addEventListener("click", () => {
      $("#sel-contracts").value = Number($("#sel-contracts").value) - 1;
      updateSelectMath();
    });
    $("#sel-plus").addEventListener("click", () => {
      $("#sel-contracts").value = Number($("#sel-contracts").value) + 1;
      updateSelectMath();
    });
    $("#sel-contracts").addEventListener("input", updateSelectMath);
    $("#sel-cancel").addEventListener("click", () => $("#select-dialog").close());
    $("#sel-confirm").addEventListener("click", confirmSelect);

    ["#vf-strike", "#vf-expiration", "#vf-bid", "#vf-ask", "#vf-spot"]
      .forEach((sel) => $(sel).addEventListener("input", onVerifyInput));
    $("#vf-clear").addEventListener("click", clearVerify);

    $("#cls-buyback").addEventListener("input", updateCloseMath);
    $("#cls-cancel").addEventListener("click", () => $("#close-dialog").close());
    $("#cls-confirm").addEventListener("click", confirmClose);

    $("#picks-open tbody").addEventListener("click", (ev) => {
      const btn = ev.target.closest(".btn-close-early");
      if (!btn) return;
      const sel = ((state.doc || {}).selections || []).find((s) => s.uid === btn.dataset.uid);
      if (sel) openCloseModal(sel);
    });
  }

  async function init() {
    wireSetup();
    wireModals();
    await loadSelections();
    renderMyPicks();
  }

  return { init, openSelectModal };
})();
