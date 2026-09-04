/* ct-landscape chat UI — no build step. Trust affordances (spec §7.6): live derivation timeline, structured
   table + citations with phase/status pulled LIVE from the index, machine-verified gate badge, NCT auto-linking
   (same scanner as the gate), trace panel with coverage footer, permalinks (#/answers/{id}), SQL console.

   Evidence dashboard (this file's second half): every figure is computed by the harness from the index over the
   answer's evidence set (cited ⊂ mentioned ⊂ retrieved NCTs) or over the entities the answer named — never from
   model text — and every figure carries the SQL that produced it ("open in SQL tab"). Charts cross-filter the
   trial list so "what did the agent see, and what did it cite?" is one click away. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const NCT_RE = /NCT[\s-]?(\d+)/gi; // the gate's scanner: separator tolerated, digit count drives well-formedness
  const state = { conversationId: null, meta: null, answers: {}, busy: false, dash: null };

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (text) => DOMPurify.sanitize(marked.parse(text || ""));
  const linkNcts = (html) => html.replace(NCT_RE, (raw, digits) => {
    const canon = "NCT" + digits, ok = digits.length === 8;
    return `<a class="nct ${ok ? "" : "bad"}" data-nct="${canon}" href="#" title="${ok ? "open trial card" : "malformed NCT"}">${esc(raw)}</a>`;
  });
  const nctsIn = (text) => { const out = new Set(); for (const m of String(text ?? "").matchAll(NCT_RE)) if (m[1].length === 8) out.add("NCT" + m[1]); return out; };
  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString());
  const PHASE_LABEL = { EARLY_PHASE1: "Early Ph1", PHASE1: "Phase 1", PHASE2: "Phase 2", PHASE3: "Phase 3", PHASE4: "Phase 4", NA: "N/A" };
  const PHASE_ORDER = ["Phase 4", "Phase 3", "Phase 2", "Phase 1", "Early Ph1", "N/A", "unknown"];
  const STATUS_ORDER = ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED", "UNKNOWN", "unknown"];
  const TIER_ORDER = ["chembl", "nlm_class", "llm", "none"];
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch {} throw new Error(d || r.statusText); }
    return r.json();
  }
  const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

  // ---------------------------------------------------------------- meta / tabs
  async function loadMeta() {
    try {
      state.meta = await api("/api/meta");
      const f = state.meta.funnel || {};
      $("#meta").textContent = `snapshot ${state.meta.snapshot_date} · ${fmt(state.meta.n_studies)} studies · ${state.meta.model}`;
      $("#schemacard").textContent = state.meta.schema_card || "";
      $("#meta").title = f.pct_drug_interventions_to_assets ? `${f.pct_drug_interventions_to_assets}% interventions→assets · ${f.pct_trial_asset_role_decidable}% roles decidable · ${f.pct_in_scope_assets_moa_labeled}% in-scope assets MoA-labeled` : "";
    } catch (e) { $("#meta").textContent = "index unavailable: " + e.message; }
  }
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === b));
    $("#tab-chat").hidden = b.dataset.tab !== "chat";
    $("#tab-sql").hidden = b.dataset.tab !== "sql";
  }));

  // ---------------------------------------------------------------- chat
  async function ensureConversation() {
    if (!state.conversationId) state.conversationId = (await api("/api/conversations", { method: "POST" })).conversation_id;
    return state.conversationId;
  }

  $("#ask").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (state.busy) return;
    const q = $("#question").value.trim();
    if (!q) return;
    $("#question").value = "";
    await ask(q);
  });

  async function ask(question) {
    state.busy = true; $("#send").disabled = true;
    const turn = document.createElement("div"); turn.className = "turn";
    turn.innerHTML = `<div class="you"><b>you:</b> ${esc(question)}</div><ul class="steps"></ul><div class="body"></div>`;
    $("#timeline").appendChild(turn); turn.scrollIntoView({ block: "end" });
    const steps = $(".steps", turn), body = $(".body", turn);
    const live = {};
    try {
      const cid = await ensureConversation();
      const r = await fetch(`/api/conversations/${cid}/ask`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
      while (true) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        let i; while ((i = buf.indexOf("\n\n")) >= 0) { const block = buf.slice(0, i); buf = buf.slice(i + 2); handle(parseSse(block)); }
      }
    } catch (e) { body.innerHTML = `<div class="refusal">request failed: ${esc(e.message)}</div>`; }
    state.busy = false; $("#send").disabled = false;

    function handle(ev) {
      if (!ev) return;
      const d = ev.data || {};
      if (ev.event === "tool_call") {
        const li = document.createElement("li"); li.className = "live"; li.dataset.step = d.step;
        li.innerHTML = describeCall(d); steps.appendChild(li); live[d.tool_call_id || d.step] = li;
      } else if (ev.event === "tool_result") {
        const li = live[d.tool_call_id] || live[d.step]; if (!li) return; li.classList.remove("live");
        delete live[d.tool_call_id]; delete live[d.step];
        if (d.error) { li.classList.add("err"); li.innerHTML += ` → <code>${esc(d.error)}</code>`; }
        else li.innerHTML += ` → ${d.rows != null ? fmt(d.rows) + " rows" : d.n_candidates != null ? d.n_candidates + " candidates" : "ok"}${d.elapsed_ms != null ? " · " + d.elapsed_ms + " ms" : ""}`;
      } else if (ev.event === "note") {
        let n = $(".note", turn); if (!n) { n = document.createElement("div"); n.className = "note hint"; turn.insertBefore(n, body); } n.textContent += d.text;
      } else if (ev.event === "gate") {
        // rendered with the answer (harness-computed, model-unforgeable)
      } else if (ev.event === "answer") {
        state.answers[d.answer_id] = d; renderAnswer(body, d); showEvidence(d.answer_id);
        location.hash = `#/answers/${d.answer_id}`;
      } else if (ev.event === "error") {
        const v = (d.gate && d.gate.violations) || [];
        body.innerHTML = `<div class="refusal"><b>No answer.</b> ${esc(d.error)}${v.length ? "<br>gate violations: " + esc(v.join("; ")) : ""}</div>`;
      }
    }
  }

  function describeCall(d) {
    const i = d.input || {};
    if (d.tool === "resolve_entity") return `resolve_entity <code>${esc(i.query)}</code> (${esc(i.kind || "auto")})`;
    if (d.tool === "run_sql") { const m = /from\s+([a-z_]+)/i.exec(i.sql || ""); return `run_sql <code>${esc(m ? m[1] : "…")}</code>`; }
    if (d.tool === "get_trial") return `get_trial <code>${esc(i.nct_id)}</code>`;
    return esc(d.tool);
  }

  function parseSse(block) {
    let event = null, data = null;
    for (const line of block.split("\n")) { if (line.startsWith("event: ")) event = line.slice(7); else if (line.startsWith("data: ")) { try { data = JSON.parse(line.slice(6)); } catch {} } }
    return event ? { event, data } : null;
  }

  function gateBadge(g) {
    if (!g) return "";
    const v = g.violations || [];
    if (v.length) return `<span class="badge bad">✗ ${v.length} grounding violation(s): ${esc(v.join("; "))}</span>`;
    return `<span class="badge ok">✓ ${g.verified}/${g.checked} citations &amp; entities verified against retrieved rows</span>`;
  }

  function renderAnswer(el, d) {
    const a = d.answer;
    let html = `<div class="answer" data-answer="${d.answer_id}">${gateBadge(d.gate)}<div class="prose">${linkNcts(md(a.answer_md))}</div>`;
    if (a.table && a.table.columns) html += renderTable(a.table, "atable") + tableFigure(a.table, d.answer_id);
    if (a.caveats && a.caveats.length) html += `<div class="caveats">caveats: ${a.caveats.map(esc).join(" · ")}</div>`;
    html += `<div class="footer">${d.context_turns ? `context includes turns 1–${d.context_turns} · ` : ""}<a href="#/answers/${d.answer_id}" class="permalink">#/answers/${d.answer_id}</a></div></div>`;
    el.innerHTML = html;
    $(".answer", el).addEventListener("click", (e) => { if (!e.target.closest("a.nct, .figure, select")) showEvidence(d.answer_id); });
    wireFigure($(".answer", el), a.table, d.answer_id);
  }

  function renderTable(t, cls) {
    const th = t.columns.map((c, i) => `<th data-col="${i}">${esc(c)}</th>`).join("");
    const rows = t.rows.map((r, i) => `<tr data-row="${i}">${r.map((c) => `<td>${linkNcts(esc(Array.isArray(c) ? c.join(", ") : c))}</td>`).join("")}</tr>`).join("");
    return `<div class="tablewrap"><table class="${cls}"><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  document.addEventListener("click", (e) => {
    const th = e.target.closest("th[data-col]"); if (!th) return;
    const table = th.closest("table"), col = +th.dataset.col, tb = table.tBodies[0];
    const rows = [...tb.rows]; const asc = table.dataset.sort !== `${col}:asc`; table.dataset.sort = `${col}:${asc ? "asc" : "desc"}`;
    rows.sort((x, y) => { const a = x.cells[col].textContent, b = y.cells[col].textContent; const na = parseFloat(a), nb = parseFloat(b);
      const c = !isNaN(na) && !isNaN(nb) ? na - nb : a.localeCompare(b); return asc ? c : -c; });
    rows.forEach((r) => tb.appendChild(r));
  });

  // ---------------------------------------------------------------- the answer table as a figure
  // A ranked table with a numeric column IS a bar chart; drawing it from `table.rows` (never from prose) keeps
  // the figure exactly as structured as the answer. Bars whose row carries NCTs filter the evidence panel.
  const isNum = (v) => v != null && v !== "" && !isNaN(parseFloat(v)) && isFinite(v);
  function numericColumns(t) {
    return t.columns.map((c, i) => i).filter((i) => t.rows.length && t.rows.every((r) => r[i] == null || isNum(r[i])) && t.rows.some((r) => isNum(r[i])));
  }
  function labelColumn(t, valueCol) {
    const cand = t.columns.map((c, i) => i).filter((i) => i !== valueCol && t.rows.some((r) => !isNum(r[i]) && r[i] != null && !nctsIn(String(r[i])).size));
    return cand.length ? cand[0] : 0;
  }
  function tableFigure(t, answerId) {
    const nums = numericColumns(t);
    if (!nums.length || t.rows.length < 2) return "";
    const pick = nums.find((i) => /^(n_|num|count|trials|active|total)/i.test(t.columns[i])) ?? nums[0];
    const sel = nums.length > 1 ? `<select class="figcol">${nums.map((i) => `<option value="${i}" ${i === pick ? "selected" : ""}>${esc(t.columns[i])}</option>`).join("")}</select>` : `<b>${esc(t.columns[pick])}</b>`;
    return `<div class="figure" data-answer="${esc(answerId)}"><div class="fig-head"><span class="hint">figure from the answer table · bar =</span> ${sel} <span class="hint">· click a bar to filter the evidence panel to that row's trials</span></div><div class="fig-body"></div></div>`;
  }
  function wireFigure(answerEl, t, answerId) {
    const fig = $(".figure", answerEl); if (!fig) return;
    const draw = () => {
      const sel = $(".figcol", fig); const col = sel ? +sel.value : numericColumns(t)[0]; const lab = labelColumn(t, col);
      const items = t.rows.map((r, i) => ({ key: String(i), label: String(r[lab] ?? `row ${i + 1}`), value: isNum(r[col]) ? parseFloat(r[col]) : 0, ncts: [...nctsIn(r.join(" "))] }))
        .filter((x) => x.value > 0).sort((a, b) => b.value - a.value).slice(0, 20);
      $(".fig-body", fig).innerHTML = bars(items, { max: Math.max(...items.map((x) => x.value), 1), active: fig.dataset.active });
    };
    draw();
    const sel = $(".figcol", fig); if (sel) sel.addEventListener("change", draw);
    fig.addEventListener("click", (e) => {
      const row = e.target.closest(".bar-row"); if (!row) return;
      const i = +row.dataset.key, ncts = [...nctsIn(t.rows[i].join(" "))];
      const tr = $(`tr[data-row="${i}"]`, answerEl); document.querySelectorAll("tr.hl").forEach((x) => x.classList.remove("hl"));
      if (fig.dataset.active === String(i)) { fig.dataset.active = ""; setFilter("row", null); }
      else {
        fig.dataset.active = String(i); if (tr) { tr.classList.add("hl"); tr.scrollIntoView({ block: "nearest" }); }
        // a row selects its listed NCTs PLUS every evidence-set trial naming an asset whose id equals a cell verbatim
        // (exact id match only, never fuzzy) — so "lenvatinib · 17 trials" shows all 17, not just the 3 example NCTs
        const cells = new Set(t.rows[i].map((c) => String(c ?? "").trim().toLowerCase()).filter(Boolean));
        showEvidence(answerId).then(() => setFilter("row", { label: String(t.rows[i][labelColumn(t, 0)]), ncts: new Set(ncts), assets: cells }));
      }
      draw();
    });
  }

  // ---------------------------------------------------------------- chart primitive (div bars; no chart library)
  // items: [{key,label,value,cited?}] — `cited` draws the darker segment so cited vs merely-retrieved is visible.
  function bars(items, { max, active, activeSet, columns } = {}) {
    max = max || Math.max(1, ...items.map((x) => x.value));
    if (!items.length) return `<div class="hint">nothing to chart</div>`;
    if (columns) return `<div class="cols">${items.map((x) => `<div class="col ${activeSet && activeSet.has(x.key) ? "on" : ""}" data-key="${esc(x.key)}" title="${esc(x.label)}: ${x.value}${x.cited ? ` (${x.cited} cited)` : ""}"><div class="col-track"><div class="col-fill" style="height:${(100 * x.value) / max}%"><div class="col-cited" style="height:${x.value ? (100 * (x.cited || 0)) / x.value : 0}%"></div></div></div><div class="col-label">${esc(x.label)}</div></div>`).join("")}</div>`;
    return items.map((x) => `<div class="bar-row ${(activeSet && activeSet.has(x.key)) || active === x.key ? "on" : ""}" data-key="${esc(x.key)}" title="${esc(x.label)}: ${x.value}${x.cited != null ? ` (${x.cited} cited)` : ""}">
      <div class="bar-label">${esc(x.label)}</div>
      <div class="bar-track"><div class="seg rest" style="width:${(100 * x.value) / max}%"></div>${x.cited ? `<div class="seg cited" style="width:${(100 * x.cited) / max}%"></div>` : ""}</div>
      <div class="bar-val">${fmt(x.value)}${x.cited ? `<span class="hint">/${x.cited}</span>` : ""}</div></div>`).join("");
  }

  // ---------------------------------------------------------------- evidence panel + dashboard
  async function showEvidence(answerId) {
    let d = state.answers[answerId];
    if (!d) { try { d = await api(`/api/answers/${answerId}`); state.answers[answerId] = d; } catch (e) { $("#evidence").innerHTML = `<div class="evidence-empty">${esc(e.message)}</div>`; return; } }
    if (state.dash && state.dash.answerId === answerId) return;
    document.querySelectorAll(".answer").forEach((x) => x.classList.toggle("selected", x.dataset.answer === answerId));
    const a = d.answer, cites = a.citations || [];
    const cited = new Set(cites.map((c) => c.nct_id));
    const mentioned = new Set([...cited, ...nctsIn(a.answer_md), ...nctsIn((a.table && a.table.rows || []).flat().join(" "))]);
    const retrieved = new Set([...(d.retrieved || []), ...mentioned]);
    const why = Object.fromEntries(cites.map((c) => [c.nct_id, c.why]));
    state.dash = { answerId, d, cited, mentioned, retrieved, why, rows: [], filters: {}, scope: "retrieved" };
    const ents = (a.entities || []).filter((e) => ["condition", "drug", "company"].includes(e.kind));
    let html = `<h3>Evidence — answer <span class="permalink">${esc(answerId)}</span></h3>${gateBadge(d.gate)}`;
    html += `<div class="scope"><span class="hint">evidence set:</span>
      <button class="scopebtn" data-scope="cited">cited <b>${cited.size}</b></button>
      <button class="scopebtn" data-scope="mentioned">in answer <b>${mentioned.size}</b></button>
      <button class="scopebtn on" data-scope="retrieved">retrieved <b>${retrieved.size}</b></button>
      <span class="hint" title="cited = citations[] · in answer = every NCT in the prose or table · retrieved = every NCT that appeared in any tool result this conversation (the gate's set)">?</span></div>`;
    html += `<div id="filters" class="filters"></div><div id="charts" class="charts"><div class="hint">profiling ${retrieved.size} trials from the index…</div></div>`;
    html += `<div id="trials"></div>`;
    if (ents.length) html += `<div id="landscapes"><h4>Landscape of the entities named in this answer <span class="hint">(deterministic, from the views — the agent's numbers can be checked against these)</span></h4>${ents.slice(0, 4).map((e) => `<details class="land" data-kind="${esc(e.kind)}" data-id="${esc(e.id)}" ${ents.length === 1 ? "open" : ""}><summary><code>${esc(e.kind)}</code> ${esc(e.id)}</summary><div class="land-body hint">loading…</div></details>`).join("")}</div>`;
    if (a.entities && a.entities.length) html += `<div class="footer">entities: ${a.entities.map((e) => `<code>${esc(e.kind)}:${esc(e.id)}</code>`).join(" ")}</div>`;
    html += renderTrace(d);
    $("#evidence").innerHTML = html;
    try {
      const p = await post("/api/trials/profile", { nct_ids: [...retrieved] });
      if (state.dash.answerId !== answerId) return;
      state.dash.rows = p.rows; state.dash.profileSql = p.sql; state.dash.missing = p.missing || [];
    } catch (e) { $("#charts").innerHTML = `<div class="refusal">profile failed: ${esc(e.message)}</div>`; return; }
    drawDashboard();
    for (const det of document.querySelectorAll("#landscapes details.land")) {
      const load = async () => { if (det.dataset.loaded) return; det.dataset.loaded = "1"; try { renderLandscape($(".land-body", det), await api(`/api/entities/${det.dataset.kind}/${encodeURIComponent(det.dataset.id)}/landscape`)); } catch (e) { $(".land-body", det).textContent = e.message; } };
      if (det.open) load(); else det.addEventListener("toggle", () => det.open && load());
    }
  }

  function scopedRows() {
    const s = state.dash, set = s.scope === "cited" ? s.cited : s.scope === "mentioned" ? s.mentioned : s.retrieved;
    return s.rows.filter((r) => set.has(r.nct_id));
  }
  function rowDims(r) {
    return {
      phase: PHASE_LABEL[r.phase_norm] || (r.phase_norm ? r.phase_norm : "unknown"),
      status: r.overall_status || "unknown",
      sponsor: r.lead_company_name || "unknown",
      year: r.start_year != null ? String(r.start_year) : "unknown",
      tiers: [...new Set((r.assets || []).map((x) => x.tier || "none"))],
      industry: r.is_industry == null ? "unknown" : r.is_industry ? "industry lead" : "non-industry lead",
    };
  }
  function passes(r, except) {
    const f = state.dash.filters, dm = rowDims(r);
    for (const [dim, vals] of Object.entries(f)) {
      if (dim === except || !vals) continue;
      if (dim === "row") { if (!vals.ncts.has(r.nct_id) && !(r.assets || []).some((x) => vals.assets && vals.assets.has(String(x.asset_id).toLowerCase()))) return false; continue; }
      const have = dim === "tier" ? dm.tiers : [dm[dim]];
      if (![...vals].some((v) => have.includes(v))) return false;
    }
    return true;
  }
  function setFilter(dim, value) {
    const f = state.dash.filters;
    if (dim === "row") { if (value) f.row = value; else delete f.row; }
    else { const cur = f[dim] || new Set(); if (cur.has(value)) cur.delete(value); else cur.add(value); if (cur.size) f[dim] = cur; else delete f[dim]; }
    drawDashboard();
  }
  function tally(rows, dim, order) {
    const m = new Map();
    for (const r of rows) {
      const dm = rowDims(r), keys = dim === "tier" ? dm.tiers : [dm[dim]];
      for (const k of keys) { const e = m.get(k) || { key: k, label: k, value: 0, cited: 0, assets: new Set() }; e.value++; if (state.dash.cited.has(r.nct_id)) e.cited++; m.set(k, e); }
    }
    const items = [...m.values()];
    if (order) items.sort((a, b) => (order.indexOf(a.key) + 1 || 99) - (order.indexOf(b.key) + 1 || 99) || b.value - a.value);
    else items.sort((a, b) => b.value - a.value || a.label.localeCompare(b.label));
    return items;
  }
  function drawDashboard() {
    const s = state.dash, all = scopedRows();
    const chartsEl = $("#charts"), fEl = $("#filters"); if (!chartsEl) return;
    // active filter chips
    const chips = Object.entries(s.filters).map(([dim, v]) => dim === "row" ? `<span class="chip" data-dim="row" title="the row's listed NCTs plus every evidence-set trial naming an asset whose id equals a cell of the row verbatim — a superset of the row's own metric, so a count difference is a real difference in definition">row ${esc(v.label)}: ${v.ncts.size} listed NCT(s) + ${all.filter((r) => !v.ncts.has(r.nct_id) && (r.assets || []).some((x) => v.assets.has(String(x.asset_id).toLowerCase()))).length} more naming the same asset id ×</span>` : [...v].map((x) => `<span class="chip" data-dim="${dim}" data-val="${esc(x)}">${dim}: ${esc(x)} ×</span>`).join(""));
    fEl.innerHTML = chips.length ? chips.join("") + `<button class="clear">clear filters</button>` : "";
    const visible = all.filter((r) => passes(r));
    // each chart is computed with every OTHER filter applied (standard cross-filter), so its own bars stay clickable
    const chart = (dim, title, order, opts = {}) => {
      const rows = all.filter((r) => passes(r, dim)); let items = tally(rows, dim, order);
      if (opts.top) items = items.slice(0, opts.top);
      const max = Math.max(1, ...items.map((x) => x.value));
      return `<div class="chart" data-dim="${dim}"><div class="chart-title">${title} <span class="hint">${rows.length} trials</span></div>${bars(items, { max, activeSet: s.filters[dim], columns: opts.columns })}${opts.note ? `<div class="hint">${opts.note}</div>` : ""}</div>`;
    };
    let html = `<div class="dash-summary"><b>${visible.length}</b> of ${all.length} trials in view · <b>${visible.filter((r) => s.cited.has(r.nct_id)).length}</b> cited · <b>${visible.filter((r) => r.is_active_readout).length}</b> active · <b>${visible.filter((r) => r.is_industry).length}</b> industry-led${s.missing && s.missing.length ? ` · <span class="badge warn">${s.missing.length} retrieved ids not in v_trials</span>` : ""}
      <button class="open" data-open="${esc(s.profileSql || "")}" title="the query behind these figures">open profile SQL</button></div>`;
    html += `<div class="chart-grid">`;
    html += chart("phase", "Phase", PHASE_ORDER, { note: "trial phase, combined rounds up; Phase 4 ≠ approval" });
    html += chart("status", "Status", STATUS_ORDER);
    html += chart("sponsor", "Lead sponsor", null, { top: 8 });
    html += chart("tier", "MoA label tier of assets in these trials", TIER_ORDER, { note: "per trial: tiers of its named assets (chembl > nlm_class > llm); 'none' = an asset with no mechanism label" });
    html += `</div>`;
    html += chart("year", "Start year", null, { columns: true });
    chartsEl.innerHTML = html;
    // sort years ascending for the column strip
    const yc = $('.chart[data-dim="year"] .cols', chartsEl);
    if (yc) [...yc.children].sort((a, b) => a.dataset.key.localeCompare(b.dataset.key)).forEach((c) => yc.appendChild(c));
    document.querySelectorAll(".scopebtn").forEach((b) => b.classList.toggle("on", b.dataset.scope === s.scope));
    drawTrials(visible);
  }
  function drawTrials(rows) {
    const s = state.dash, el = $("#trials"); if (!el) return;
    const sorted = [...rows].sort((a, b) => (s.cited.has(b.nct_id) - s.cited.has(a.nct_id)) || (s.mentioned.has(b.nct_id) - s.mentioned.has(a.nct_id)) || (b.phase_rank || 0) - (a.phase_rank || 0) || a.nct_id.localeCompare(b.nct_id));
    const limit = s.showAll ? sorted.length : 40;
    let html = `<table class="trials"><thead><tr><th>trial</th><th>phase · status · lead sponsor · start</th><th>why cited / role in evidence</th></tr></thead><tbody>`;
    for (const r of sorted.slice(0, limit)) {
      const kind = s.cited.has(r.nct_id) ? "cited" : s.mentioned.has(r.nct_id) ? "in answer" : "retrieved, not cited";
      html += `<tr class="${kind.replace(/[ ,]+/g, "-")}"><td><a class="nct" data-nct="${esc(r.nct_id)}" href="#">${esc(r.nct_id)}</a><br><a href="https://clinicaltrials.gov/study/${esc(r.nct_id)}" target="_blank" rel="noopener">ctgov ↗</a></td>
        <td><div>${esc(PHASE_LABEL[r.phase_norm] || r.phase_norm || "no phase")} · ${esc(r.overall_status || "—")} · ${esc(r.lead_company_name || "—")}${r.is_industry ? "" : " <span class='hint'>(non-industry)</span>"} · ${r.start_year ?? "—"}</div><div class="hint">${esc(r.brief_title || "")}</div></td>
        <td class="why">${s.why[r.nct_id] ? esc(s.why[r.nct_id]) : `<span class="hint">${kind}</span>`}</td></tr>`;
    }
    html += `</tbody></table>`;
    if (sorted.length > limit) html += `<button class="showall">show all ${sorted.length}</button>`;
    if (!sorted.length) html += `<div class="evidence-empty">no trials match the current filters</div>`;
    el.innerHTML = html;
  }
  function renderLandscape(el, L) {
    const head = Object.entries(L.headline || {}).map(([k, v]) => `<div class="kpi"><div class="kpi-v">${typeof v === "number" ? fmt(v) : esc(v)}</div><div class="kpi-k">${esc(k.replace(/_/g, " "))}</div></div>`).join("");
    const charts = (L.charts || []).map((c) => {
      const items = c.items.map((x) => ({ key: x.label, label: x.label, value: Number(x.value) || 0 }));
      const cols = /year/i.test(c.title);
      return `<div class="chart"><div class="chart-title">${esc(c.title)} <button class="open" data-open="${esc(c.sql)}">SQL</button></div>${bars(items, { columns: cols })}${c.note ? `<div class="hint">${esc(c.note)}</div>` : ""}</div>`;
    }).join("");
    el.classList.remove("hint");
    el.innerHTML = `<div class="land-name">${esc(L.name)} <button class="open" data-open="${esc(L.headline_sql || "")}">SQL</button></div><div class="kpis">${head}</div><div class="chart-grid">${charts}</div>`;
  }
  $("#evidence").addEventListener("click", (e) => {
    const sb = e.target.closest(".scopebtn"); if (sb) { state.dash.scope = sb.dataset.scope; drawDashboard(); return; }
    const chip = e.target.closest(".chip"); if (chip) { setFilter(chip.dataset.dim, chip.dataset.dim === "row" ? null : chip.dataset.val); return; }
    if (e.target.closest(".clear")) { state.dash.filters = {}; document.querySelectorAll(".figure").forEach((f) => (f.dataset.active = "")); drawDashboard(); return; }
    if (e.target.closest(".showall")) { state.dash.showAll = true; drawDashboard(); return; }
    const bar = e.target.closest("#charts .bar-row, #charts .col"); if (bar) { const dim = bar.closest(".chart").dataset.dim; setFilter(dim, bar.dataset.key); }
  });

  function renderTrace(d) {
    const tr = d.trace || [], u = d.usage || {}, f = (d.coverage || (state.meta && state.meta.funnel)) || {};
    let html = `<details class="trace"><summary>how was this derived — ${tr.length} step(s)${u.input_tokens ? ` · ${fmt(u.input_tokens)} in / ${fmt(u.output_tokens)} out tokens` : ""}${d.elapsed_ms ? ` · ${(d.elapsed_ms / 1000).toFixed(1)} s` : ""}</summary>`;
    tr.forEach((s, i) => {
      if (s.tool === "run_sql") html += `<div><b>${i + 1}. run_sql</b> → ${s.error ? `<span class="badge bad">${esc(s.error)}</span>` : `${fmt(s.rows)} rows · ${s.elapsed_ms} ms · ${fmt(s.ncts_seen)} NCTs grounded`}<button class="copy" data-copy="${esc(s.input.sql)}">copy</button><button class="open" data-open="${esc(s.input.sql)}">open in SQL tab</button><pre>${esc(s.input.sql)}</pre></div>`;
      else if (s.tool === "resolve_entity") html += `<div><b>${i + 1}. resolve_entity</b> <code>${esc(s.input.query)}</code> (${esc(s.input.kind)}) → ${s.n_candidates} candidate(s)</div>`;
      else if (s.tool === "get_trial") html += `<div><b>${i + 1}. get_trial</b> <code>${esc(s.input.nct_id)}</code> → ${s.found ? "found" : "not in index"}</div>`;
    });
    html += `<div>gate: ${d.gate ? (d.gate.violations && d.gate.violations.length ? "✗ " + esc(d.gate.violations.join("; ")) : `✓ ${d.gate.verified}/${d.gate.checked}`) : "—"}</div>`;
    html += coverageFigure(f);
    html += `</details>`;
    return html;
  }
  // the coverage footer as a figure: the index's own completeness, so a reader can see what "N of M" is measured against
  function coverageFigure(f) {
    const pct = [
      ["drug interventions → assets", f.pct_drug_interventions_to_assets],
      ["trial×asset rows role-decidable", f.pct_trial_asset_role_decidable],
      ["in-scope assets MoA-labeled", f.pct_in_scope_assets_moa_labeled],
      ["in-scope trial×asset rows MoA-labeled", f.pct_in_scope_trial_asset_rows_moa_labeled],
    ].filter((x) => x[1] != null).map(([label, v]) => ({ key: label, label, value: Number(v) }));
    return `<div class="footer">coverage: ${fmt(f.studies_ingested)} studies, snapshot ${esc(f.snapshot_date || (state.meta && state.meta.snapshot_date))}</div><div class="chart coverage">${bars(pct, { max: 100 })}</div>`;
  }
  document.addEventListener("click", (e) => {
    const c = e.target.closest("button.copy"); if (c) { navigator.clipboard && navigator.clipboard.writeText(c.dataset.copy); c.textContent = "copied"; return; }
    const o = e.target.closest("button.open"); if (o) { $("#sqltext").value = o.dataset.open; $('.tab[data-tab="sql"]').click(); $("#runsql").click(); return; }
    const n = e.target.closest("a.nct"); if (n) { e.preventDefault(); openTrial(n.dataset.nct); }
  });

  // ---------------------------------------------------------------- trial card drawer
  async function openTrial(nct) {
    $("#drawer").hidden = false; $("#drawer-body").innerHTML = `loading ${esc(nct)}…`;
    try {
      const t = await api(`/api/trials/${nct}`);
      const arms = (t.arms || []).map((a) => `<div class="arm"><span class="type">${esc(a.type || "—")}</span> <b>${esc(a.label || "")}</b>${(a.assets || []).map((x) => ` <span class="role">${esc(x.asset_id)} · ${esc(x.role)}</span>`).join("")}<div class="hint">${esc(a.description || "")}</div></div>`).join("");
      const conds = (t.conditions_primary || []).map((c) => `${esc(c.display_name)} <code>${esc(c.condition_key)}</code>`).join(", ");
      const pops = (t.population_mentions || []).filter((p) => p && p.term_id).map((p) => `<li><code>${esc(p.kind)}:${esc(p.term_id)}</code> (${esc(p.surface)}) — ${esc(p.evidence)}</li>`).join("");
      const why = state.dash && state.dash.why[nct];
      $("#drawer-body").innerHTML = `<div class="card"><h2>${esc(t.nct_id)} — ${esc(t.brief_title)}</h2>
        ${why ? `<div class="whybox"><b>why the agent cited it:</b> ${esc(why)}</div>` : ""}
        <div class="kv">${esc(t.study_type)} · ${esc(t.phase_norm || "no phase")} · ${esc(t.overall_status)} · lead: ${esc(t.lead_company_name || "—")} (${t.is_industry ? "industry" : "non-industry"}) · program_exists: ${t.program_exists} · <a href="${esc(t.ctgov_url)}" target="_blank" rel="noopener">clinicaltrials.gov ↗</a></div>
        <div><b>conditions:</b> ${conds || "—"}</div>
        <div><b>arms</b>${arms || " — none recorded"}</div>
        ${pops ? `<div><b>population mentions (lexicon; inclusion/exclusion not parsed):</b><ul>${pops}</ul></div>` : ""}
        <div><b>eligibility</b> (${esc(t.sex || "")}, ${esc(t.minimum_age || "?")}–${esc(t.maximum_age || "?")}, ${(t.std_ages || []).join("/")})</div><pre class="elig">${esc(t.eligibility_criteria || "")}</pre>
        <div class="kv">start ${esc(t.start_date || "—")} · completion ${esc(t.completion_date || "—")} · last update ${esc(t.last_update_date || "—")} (${esc(t.date_precision || "")} precision) · enrollment ${fmt(t.enrollment_count)} ${esc(t.enrollment_type || "")}</div></div>`;
    } catch (e) { $("#drawer-body").innerHTML = `<div class="refusal">${esc(e.message)}</div>`; }
  }
  $("#drawer-close").addEventListener("click", () => { $("#drawer").hidden = true; });
  $("#drawer").addEventListener("click", (e) => { if (e.target === $("#drawer")) $("#drawer").hidden = true; });

  // ---------------------------------------------------------------- SQL console
  $("#runsql").addEventListener("click", async () => {
    $("#sqlstatus").textContent = "running…"; $("#sqlresult").innerHTML = "";
    try {
      const r = await post("/api/sql", { sql: $("#sqltext").value });
      $("#sqlstatus").textContent = `${fmt(r.total_row_count)} rows${r.truncated ? " (showing 200)" : ""} · ${r.elapsed_ms} ms`;
      $("#sqlresult").innerHTML = renderTable({ columns: r.columns, rows: r.rows }, "sqltable");
    } catch (e) { $("#sqlstatus").textContent = ""; $("#sqlresult").innerHTML = `<div class="err">${esc(e.message)}</div>`; }
  });

  // ---------------------------------------------------------------- permalinks
  async function route() {
    const m = /^#\/answers\/([a-z0-9]+)$/.exec(location.hash);
    if (m && !state.answers[m[1]]) {
      try {
        const rec = await api(`/api/answers/${m[1]}`);
        state.answers[m[1]] = rec;
        const turn = document.createElement("div"); turn.className = "turn";
        turn.innerHTML = `<div class="you"><b>you:</b> ${esc(rec.question)} <span class="hint">(replayed from permalink)</span></div><ul class="steps">${(rec.trace || []).map((s) => `<li>${describeCall({ tool: s.tool, input: s.input })}${s.rows != null ? ` → ${fmt(s.rows)} rows` : ""}</li>`).join("")}</ul><div class="body"></div>`;
        $("#timeline").appendChild(turn); renderAnswer($(".body", turn), rec); showEvidence(m[1]); turn.scrollIntoView({ block: "start" });
      } catch {}
    }
  }
  window.addEventListener("hashchange", route);
  loadMeta().then(route);
})();
