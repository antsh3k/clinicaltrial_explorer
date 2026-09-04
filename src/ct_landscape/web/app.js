/* ct-landscape chat UI — no build step. Trust affordances (spec §7.6): live derivation timeline, structured
   table + citations with phase/status pulled LIVE from the index, machine-verified gate badge, NCT auto-linking
   (same scanner as the gate), trace panel with coverage footer, permalinks (#/answers/{id}), SQL console. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const NCT_RE = /NCT[\s-]?(\d+)/gi; // the gate's scanner: separator tolerated, digit count drives well-formedness
  const state = { conversationId: null, meta: null, answers: {}, busy: false };

  // ---------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const md = (text) => DOMPurify.sanitize(marked.parse(text || ""));
  const linkNcts = (html) => html.replace(NCT_RE, (raw, digits) => {
    const canon = "NCT" + digits, ok = digits.length === 8;
    return `<a class="nct ${ok ? "" : "bad"}" data-nct="${canon}" href="#" title="${ok ? "open trial card" : "malformed NCT"}">${esc(raw)}</a>`;
  });
  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString());
  async function api(path, opts) {
    const r = await fetch(path, opts);
    if (!r.ok) { let d = ""; try { d = (await r.json()).detail; } catch {} throw new Error(d || r.statusText); }
    return r.json();
  }

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
    if (a.table && a.table.columns) html += renderTable(a.table, "atable");
    if (a.caveats && a.caveats.length) html += `<div class="caveats">caveats: ${a.caveats.map(esc).join(" · ")}</div>`;
    html += `<div class="footer">${d.context_turns ? `context includes turns 1–${d.context_turns} · ` : ""}<a href="#/answers/${d.answer_id}" class="permalink">#/answers/${d.answer_id}</a></div></div>`;
    el.innerHTML = html;
    $(".answer", el).addEventListener("click", (e) => { if (!e.target.closest("a.nct")) showEvidence(d.answer_id); });
  }

  function renderTable(t, cls) {
    const th = t.columns.map((c, i) => `<th data-col="${i}">${esc(c)}</th>`).join("");
    const rows = t.rows.map((r) => `<tr>${r.map((c) => `<td>${linkNcts(esc(Array.isArray(c) ? c.join(", ") : c))}</td>`).join("")}</tr>`).join("");
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

  // ---------------------------------------------------------------- evidence panel
  async function showEvidence(answerId) {
    let d = state.answers[answerId];
    if (!d) { try { d = await api(`/api/answers/${answerId}`); d = { ...d, answer_id: d.answer_id }; state.answers[answerId] = d; } catch (e) { $("#evidence").innerHTML = `<div class="evidence-empty">${esc(e.message)}</div>`; return; } }
    document.querySelectorAll(".answer").forEach((x) => x.classList.toggle("selected", x.dataset.answer === answerId));
    const a = d.answer, cites = a.citations || [];
    let html = `<h3>Evidence — answer <span class="permalink">${esc(answerId)}</span></h3>${gateBadge(d.gate)}`;
    html += `<table><thead><tr><th>trial</th><th>phase · status · sponsor</th><th>why</th></tr></thead><tbody>`;
    for (const c of cites) html += `<tr data-cite="${esc(c.nct_id)}"><td><a class="nct" data-nct="${esc(c.nct_id)}" href="#">${esc(c.nct_id)}</a><br><a href="https://clinicaltrials.gov/study/${esc(c.nct_id)}" target="_blank" rel="noopener">ctgov ↗</a></td><td class="live-meta">…</td><td class="why">${esc(c.why)}</td></tr>`;
    html += `</tbody></table>`;
    if (!cites.length) html += `<div class="evidence-empty">no citations submitted${a.answer_md ? "" : ""}</div>`;
    if (a.entities && a.entities.length) html += `<div class="footer">entities: ${a.entities.map((e) => `<code>${esc(e.kind)}:${esc(e.id)}</code>`).join(" ")}</div>`;
    html += renderTrace(d);
    $("#evidence").innerHTML = html;
    // phase/status/sponsor pulled LIVE from the index, never from the model
    for (const c of cites.slice(0, 40)) {
      try { const t = await api(`/api/trials/${c.nct_id}`); const row = $(`tr[data-cite="${c.nct_id}"] .live-meta`, $("#evidence"));
        if (row) row.textContent = `${t.phase_norm || "—"} · ${t.overall_status || "—"} · ${t.lead_company_name || "—"}`; } catch {}
    }
  }

  function renderTrace(d) {
    const tr = d.trace || [], u = d.usage || {}, f = (d.coverage || (state.meta && state.meta.funnel)) || {};
    let html = `<details class="trace"><summary>how was this derived — ${tr.length} step(s)${u.input_tokens ? ` · ${fmt(u.input_tokens)} in / ${fmt(u.output_tokens)} out tokens` : ""}${d.elapsed_ms ? ` · ${(d.elapsed_ms / 1000).toFixed(1)} s` : ""}</summary>`;
    tr.forEach((s, i) => {
      if (s.tool === "run_sql") html += `<div><b>${i + 1}. run_sql</b> → ${s.error ? `<span class="badge bad">${esc(s.error)}</span>` : `${fmt(s.rows)} rows · ${s.elapsed_ms} ms · ${fmt(s.ncts_seen)} NCTs grounded`}<button class="copy" data-copy="${esc(s.input.sql)}">copy</button><button class="open" data-open="${esc(s.input.sql)}">open in SQL tab</button><pre>${esc(s.input.sql)}</pre></div>`;
      else if (s.tool === "resolve_entity") html += `<div><b>${i + 1}. resolve_entity</b> <code>${esc(s.input.query)}</code> (${esc(s.input.kind)}) → ${s.n_candidates} candidate(s)</div>`;
      else if (s.tool === "get_trial") html += `<div><b>${i + 1}. get_trial</b> <code>${esc(s.input.nct_id)}</code> → ${s.found ? "found" : "not in index"}</div>`;
    });
    html += `<div>gate: ${d.gate ? (d.gate.violations && d.gate.violations.length ? "✗ " + esc(d.gate.violations.join("; ")) : `✓ ${d.gate.verified}/${d.gate.checked}`) : "—"}</div>`;
    html += `<div class="footer">coverage: ${fmt(f.studies_ingested)} studies, snapshot ${esc(f.snapshot_date || (state.meta && state.meta.snapshot_date))} · ${f.pct_drug_interventions_to_assets ?? "—"}% interventions→assets · ${f.pct_trial_asset_role_decidable ?? "—"}% arm-role-decidable · ${f.pct_in_scope_assets_moa_labeled ?? "—"}% in-scope assets MoA-labeled (${f.pct_in_scope_trial_asset_rows_moa_labeled ?? "—"}% of trial×asset rows)</div>`;
    html += `</details>`;
    return html;
  }
  document.addEventListener("click", (e) => {
    const c = e.target.closest("button.copy"); if (c) { navigator.clipboard && navigator.clipboard.writeText(c.dataset.copy); c.textContent = "copied"; return; }
    const o = e.target.closest("button.open"); if (o) { $("#sqltext").value = o.dataset.open; $('.tab[data-tab="sql"]').click(); return; }
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
      $("#drawer-body").innerHTML = `<div class="card"><h2>${esc(t.nct_id)} — ${esc(t.brief_title)}</h2>
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
      const r = await api("/api/sql", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sql: $("#sqltext").value }) });
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
        $("#timeline").appendChild(turn); renderAnswer($(".body", turn), rec); showEvidence(m[1]);
      } catch {}
    }
  }
  window.addEventListener("hashchange", route);
  loadMeta().then(route);
})();
