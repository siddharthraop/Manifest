// MANIFEST console: six screens on the MANIFEST API, with context-sensitive help on each.
// No framework: hash routing, fetch, and template strings. Every value from the API is escaped.
(() => {
  "use strict";
  const CFG = window.MANIFEST_CONFIG;
  const API = CFG.api.replace(/\/$/, "");
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const ICON = { pass: "✓ Pass", fail: "✗ Fail", review: "◇ Review", fixed: "✎ Fixed", met: "✓ Met", missed: "✗ Not met",
    succeeded: "✓ Succeeded", failed: "✗ Failed", queued: "… Queued", running: "… Running", approved: "✓ Approved",
    rejected: "✗ Rejected", proposed: "◇ Proposed", open: "◇ Open" };
  const st = (s) => `<span class="st ${esc(s)}"><span>${esc(ICON[s] || s)}</span></span>`;
  const store = { get(k) { try { return sessionStorage.getItem(k); } catch { return null; } },
    set(k, v) { try { v == null ? sessionStorage.removeItem(k) : sessionStorage.setItem(k, v); } catch { /* private mode */ } } };
  let token = store.get("manifest-token");
  let user = store.get("manifest-user");
  let config = { sign_in: false, modes: ["migrate", "replay", "live"], model: "" };
  let csh = {};
  let helpId = "overview";

  // ------------------------------------------------------------ data
  async function api(path, opts = {}) {
    const headers = { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
    const r = await fetch(`${API}${path}`, { ...opts, headers });
    const type = r.headers.get("content-type") || "";
    const data = type.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && data.detail) || `${r.status} ${r.statusText}`);
    return data;
  }
  const file = (run, path) => api(`/api/runs/${encodeURIComponent(run)}/files/${path}`);
  const mapDir = (map) => map.split("/").slice(0, -1).join("/");

  // ------------------------------------------------------------ help panel (CSH)
  function openHelp(id = helpId) {
    const t = csh[id];
    const panel = $("#help");
    $("#help-title").textContent = t ? t.title : "Help";
    $("#help-short").textContent = t ? t.shortdesc : "Help for this screen isn't available yet.";
    $("#help-steps").innerHTML = t ? t.steps.map((s) => `<li>${esc(s)}</li>`).join("") : "";
    $("#help-related").innerHTML = t && t.related.length
      ? `<h3>Related</h3>${t.related.map((r) => `<a href="${esc(CFG.help + r.href)}" target="_blank" rel="noopener">${esc(r.title)}</a>`).join("")}` : "";
    $("#help-open").href = t ? CFG.help + t.href : CFG.help;
    $("#help-browse").href = CFG.site;
    panel.classList.add("open"); panel.setAttribute("aria-hidden", "false");
    $("#help-btn").setAttribute("aria-expanded", "true");
    $("#help-close").focus();
  }
  function closeHelp() {
    $("#help").classList.remove("open"); $("#help").setAttribute("aria-hidden", "true");
    $("#help-btn").setAttribute("aria-expanded", "false");
  }
  const helpBtn = (id, label) => `<button class="icon-btn" type="button" data-help="${esc(id)}" data-help-id="${esc(id)}" aria-label="Help: ${esc(label)}">?</button>`;

  // ------------------------------------------------------------ sign-in (Google Identity Services)
  function renderWho() {
    $("#who").textContent = user ? `Signed in as ${user}` : (config.sign_in ? "" : "Read-only deployment");
    const box = $("#signin");
    if (!config.sign_in) { box.innerHTML = ""; return; }
    if (user) { box.innerHTML = `<button class="btn" type="button" id="signout">Sign out</button>`; $("#signout").onclick = signOut; return; }
    box.innerHTML = `<div id="gsi"></div>`;
    if (!window.google) {
      const s = document.createElement("script");
      s.src = "https://accounts.google.com/gsi/client"; s.async = true; s.onload = initGsi; document.head.appendChild(s);
    } else initGsi();
  }
  function initGsi() {
    google.accounts.id.initialize({ client_id: config.client_id, callback: (resp) => {
      token = resp.credential;
      try { user = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/"))).email; } catch { user = "you"; }
      store.set("manifest-token", token); store.set("manifest-user", user); renderWho(); route();
    } });
    google.accounts.id.renderButton($("#gsi"), { theme: "outline", size: "medium", text: "signin" });
  }
  function signOut() { token = user = null; store.set("manifest-token", null); store.set("manifest-user", null); renderWho(); route(); }
  const canWrite = () => config.sign_in && !!token;

  // ------------------------------------------------------------ screens
  const screens = {};

  screens.overview = async () => {
    helpId = "overview";
    const runs = await api("/api/runs?limit=100");
    const done = runs.filter((r) => r.status === "succeeded");
    const last = done[0];
    if (!last) return `<h1>Overview ${helpBtn("overview", "Overview")}</h1><p>No finished runs yet. Start one from <a href="#/maps">Maps</a>.</p>`;
    const m = Object.fromEntries((last.metrics || []).map((x) => [x.metric, x]));
    const s = last.summary;
    const passed = s.passed;
    const hours = (passed * 20 / 60).toFixed(1);
    const cost = (last.duration_s * (2 * 0.000018 + 4 * 0.000002));
    const kpi = (v, label, note = "") => `<div role="listitem"><b>${esc(v)}</b><span>${esc(label)}</span>${note ? `<small>${esc(note)}</small>` : ""}</div>`;
    const okr = (last.metrics || []).map((x) => {
      const met = x.target === "Tracked" || x.target.startsWith("Set from") ? null : String(x.measured) === String(x.target) || (x.target.startsWith("100%") && x.measured === "100%");
      return `<tr><td>${esc(x.metric)}</td><td>${esc(x.target)}</td><td class="mono">${esc(x.measured)}</td><td>${met === null ? "Tracked" : st(met ? "met" : "missed")}</td></tr>`;
    }).join("");
    return `<h1>Overview ${helpBtn("overview", "Overview")}</h1>
      <p class="meta">Latest finished run <a href="#/runs/${esc(last.id)}">${esc(last.id)}</a> · ${esc(last.map)} · ${esc(last.mode)} · gate ${st(last.gate)}</p>
      <div class="band" role="list" aria-label="Key results">
        ${kpi(m["Unreported losses"]?.measured ?? "–", "unreported losses")}
        ${kpi(m["Reuse kept"]?.measured ?? "–", "reuse kept")}
        ${kpi(`${passed}/${s.topics}`, "topics passed without a writer")}
        ${kpi((m["Fixes approved"]?.measured ?? "–").split(" approved")[0], "fixes approved", (m["Fixes approved"]?.measured ?? "").split(", ")[1] || "")}
        ${kpi(m["Broken links"]?.measured ?? "–", "broken links")}
        ${kpi(`${hours} h`, "writer hours saved", "modelled: 20 min per topic checked by hand")}
        ${kpi(`$${cost.toFixed(4)}`, "cost per map", `${last.duration_s} s × (2 vCPU × $0.000018 + 4 GiB × $0.000002); $0 inside the free tier`)}
      </div>
      <h2>Scorecard</h2>
      <div class="table-wrap"><table><caption>Design doc §7 targets, measured on the latest run.</caption>
      <thead><tr><th scope="col">Metric</th><th scope="col">Target</th><th scope="col">Measured</th><th scope="col">Status</th></tr></thead><tbody>${okr}</tbody></table></div>
      <h2>Recent runs</h2>${runTable(runs.slice(0, 8))}`;
  };

  function runTable(runs) {
    if (!runs.length) return `<p>No runs yet.</p>`;
    return `<div class="table-wrap"><table><thead><tr><th scope="col">Run</th><th scope="col">Map</th><th scope="col">Mode</th><th scope="col">Status</th><th scope="col">Gate</th><th scope="col">Started by</th></tr></thead><tbody>
      ${runs.map((r) => `<tr><td class="mono"><a href="#/runs/${esc(r.id)}">${esc(r.id)}</a></td><td class="mono">${esc(r.map)}</td><td>${esc(r.mode)}</td><td>${st(r.status)}</td><td>${r.gate ? st(r.gate) : "–"}</td><td>${esc(r.triggered_by || "")}</td></tr>`).join("")}
      </tbody></table></div>`;
  }

  screens.maps = async () => {
    helpId = "maps";
    const maps = await api("/api/maps");
    const modes = config.modes.map((m) => `<option>${esc(m)}</option>`).join("");
    const rows = maps.map((m, i) => `<tr><td class="mono">${esc(m.map)}</td>
      <td>${m.last_run ? `<a class="mono" href="#/runs/${esc(m.last_run.id)}">${esc(m.last_run.id)}</a>` : "Never run"}</td>
      <td>${m.last_run ? st(m.last_run.status) : "–"}</td><td>${m.last_run?.gate ? st(m.last_run.gate) : "–"}</td>
      <td><label class="hidden" for="mode-${i}">Mode</label><select id="mode-${i}" ${canWrite() ? "" : "disabled"}>${modes}</select>
        <button class="btn btn-fill" type="button" data-start="${esc(m.map)}" data-mode-select="mode-${i}" ${canWrite() ? "" : "disabled"}>Start run</button></td></tr>`).join("");
    return `<h1>Maps ${helpBtn("maps", "Maps")}</h1>
      ${canWrite() ? "" : `<p class="notice">Sign in to start a run. Reading needs no sign-in.</p>`}
      <div id="msg" aria-live="polite"></div>
      <div class="table-wrap"><table><caption>Every DITA map in the corpus, with its latest run.</caption>
      <thead><tr><th scope="col">Map</th><th scope="col">Last run</th><th scope="col">Status</th><th scope="col">Gate</th><th scope="col">Start a run</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  };

  screens.run = async (id, params) => {
    helpId = "run-detail";
    const run = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (run.status !== "succeeded") {
      return `<h1>Run <span class="mono">${esc(id)}</span> ${helpBtn("run-detail", "Run detail")}</h1>
        <p>${st(run.status)} · ${esc(run.map)} · ${esc(run.mode)}</p>${run.error ? `<p class="notice err">${esc(run.error)}</p>` : ""}
        <p>${run.status === "failed" ? `See <a href="${esc(CFG.help)}topics/ts-run-failed.html" target="_blank" rel="noopener">A run fails before it finishes</a>.` : "This page refreshes when you open it again."}</p>`;
    }
    const report = await file(id, "report/report.json");
    const filter = params.get("status") || "all";
    const counts = report.topics.reduce((a, t) => ((a[t.status] = (a[t.status] || 0) + 1), a), {});
    const shown = report.topics.filter((t) => filter === "all" || t.status === filter);
    const filters = ["all", "fail", "fixed", "review", "pass"].map((f) => `<button class="btn" type="button" aria-pressed="${f === filter}" data-filter="${f}">${esc(f)} (${f === "all" ? report.topics.length : counts[f] || 0})</button>`).join("");
    const rows = shown.map((t) => `<tr class="clickable" data-topic="${esc(t.topic)}" tabindex="0"><td class="mono">${esc(t.topic)}</td><td>${st(t.status)}</td>
      ${["counts", "text", "ids", "links"].map((c) => `<td>${st(t.checks[c])}</td>`).join("")}<td>${esc(t.note)}</td></tr>`).join("");
    const topic = params.get("topic");
    return `<h1>Run <span class="mono">${esc(id)}</span> ${helpBtn("run-detail", "Run detail")}</h1>
      <p class="meta">${esc(run.map)} · ${esc(run.mode)} · ${esc(run.duration_s)} s · started by ${esc(run.triggered_by || "")} ·
        ${Object.entries(run.versions || {}).map(([k, v]) => `${esc(k)} ${esc(v)}`).join(" · ")}</p>
      <p>Gate ${st(report.gate)} · <a href="${esc(API)}/api/runs/${esc(id)}/files/report/report.html" target="_blank" rel="noopener">Report</a></p>
      <div class="filters" role="group" aria-label="Filter topics by status">${filters}</div>
      <div class="table-wrap"><table><caption>Each check compares DITA-OT HTML5 of the source with Sphinx of the migrated MyST, in every build (${esc(report.summary.builds.join(", "))}). Select a topic for its detail.</caption>
      <thead><tr><th scope="col">Topic</th><th scope="col">Status</th><th scope="col">Counts</th><th scope="col">Text</th><th scope="col">IDs</th><th scope="col">Links</th><th scope="col">Note</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div id="topic-detail">${topic ? await topicDetail(run, report, topic) : ""}</div>`;
  };

  async function topicDetail(run, report, topic) {
    const t = report.topics.find((x) => x.topic === topic);
    if (!t) return "";
    const md = topic.replace(/\.dita$/, ".md");
    const [src, myst, ledger, agent] = await Promise.all([
      fetch(`${CFG.site}${mapDir(run.map)}/${topic}`).then((r) => (r.ok ? r.text() : "(source not published with the console)")).catch(() => ""),
      file(run.id, `myst/${md}`).catch(() => "(no MyST page)"),
      file(run.id, "ledger.json").catch(() => []),
      file(run.id, "agent/run.json").catch(() => null),
    ]);
    const mine = (Array.isArray(ledger) ? ledger : []).filter((e) => e.owner === topic);
    const kinds = mine.reduce((a, e) => ((a[e.kind] = (a[e.kind] || 0) + 1), a), {});
    const diffs = t.differences.map((d) => `<li><b>${esc(d.check)}</b> [${esc(d.build)}]: ${esc(d.detail)}${d.dita || d.myst ? ` — DITA <code>${esc(d.dita)}</code> MyST <code>${esc(d.myst)}</code>` : ""}</li>`).join("");
    const session = agent?.sessions?.find((s) => s.topic === topic);
    const trace = session ? session.attempts.map((a) => `<li>Attempt ${a.n}: <span class="mono">${esc(a.rule.id)}</span> ${st(a.passed ? "pass" : "fail")}
        ${a.error ? `<br>${esc(a.error)}` : ""}${a.differences.length ? `<br>${a.differences.length} difference(s) left` : ""}</li>`).join("") : "";
    return `<h2>${esc(topic)} ${st(t.status)}</h2>
      ${diffs ? `<ul>${diffs}</ul>` : `<p>No differences in any build.</p>`}
      <details open><summary>Source and migrated page ${helpBtn("diff", "Comparing source and output")}</summary>
        <div class="split"><div><p class="meta">DITA source</p><pre class="src">${esc(src)}</pre></div><div><p class="meta">MyST (migrated)</p><pre class="src">${esc(myst)}</pre></div></div></details>
      <details><summary>Reuse ledger: ${mine.length} placeholder(s) ${helpBtn("reuse-ledger", "Reuse ledger")}</summary>
        <p>${Object.entries(kinds).map(([k, n]) => `${esc(k)}: ${n}`).join(" · ") || "No reuse in this topic."}</p>
        <div class="table-wrap"><table><thead><tr><th scope="col">Token</th><th scope="col">Kind</th><th scope="col">Element</th><th scope="col">Line</th><th scope="col">Detail</th></tr></thead><tbody>
        ${mine.map((e) => `<tr><td class="mono">${esc(e.token)}</td><td>${esc(e.kind)}</td><td class="mono">&lt;${esc(e.element)}&gt;</td><td>${esc(e.line)}</td><td class="mono">${esc(Object.entries(e.detail || {}).map(([k, v]) => `${k}=${v}`).join(" "))}</td></tr>`).join("")}</tbody></table></div></details>
      <details ${session ? "open" : ""}><summary>Agent trace ${helpBtn("agent-trace", "Agent trace")}</summary>
        ${session ? `<p>Outcome ${st(session.outcome === "fixed" ? "fixed" : "review")} · ${session.refused_calls} refused call(s)</p><ol>${trace}</ol>${session.final_text ? `<p class="meta">Model's last words: ${esc(session.final_text)}</p>` : ""}` : `<p>The agent didn't work on this topic.</p>`}</details>`;
  }

  screens.review = async () => {
    helpId = "review-queue";
    const [reviews, proposed] = await Promise.all([api("/api/reviews"), api("/api/rules?status=proposed")]);
    const rules = proposed.map(ruleCard).join("");
    const items = reviews.map((r) => `<div class="card"><h3 class="mono">${esc(r.topic)}</h3><p>${st(r.status)} · run <a class="mono" href="#/runs/${esc(r.run)}?topic=${encodeURIComponent(r.topic)}">${esc(r.run)}</a> · ${esc(r.attempts.length)} attempt(s)</p>
      <p>${esc(r.summary || "The agent didn't write a summary.")}</p><a href="${esc(API)}/api/runs/${esc(r.run)}/files/${esc(r.file.replace(/^runs\/[^/]+\//, ""))}" target="_blank" rel="noopener">Open the review</a></div>`).join("");
    return `<h1>Review queue ${helpBtn("review-queue", "Review queue")}</h1>
      <div id="msg" aria-live="polite"></div>
      <h2>Proposed rules ${helpBtn("rule-decision", "Approving a rule")}</h2>${rules ? `<div class="cards">${rules}</div>` : `<p>No rules waiting for a decision.</p>`}
      <h2>Topics the agent couldn't fix</h2>${items ? `<div class="cards">${items}</div>` : `<p>No open reviews.</p>`}`;
  };

  function ruleCard(r) {
    const p = r.provenance || {};
    const decide = r.status === "proposed" ? `<p><button class="btn btn-fill" type="button" data-approve="${esc(r.id)}" ${canWrite() ? "" : "disabled"}>Approve</button>
      <button class="btn btn-stamp" type="button" data-reject="${esc(r.id)}" ${canWrite() ? "" : "disabled"}>Reject</button></p>` : "";
    return `<div class="card"><h3 class="mono">${esc(r.id)}</h3><p>${st(r.status)} · matches <code>${esc(r.match)}</code></p><p>${esc(r.description)}</p>
      <p class="meta">${esc(Object.entries(p).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(" · "))}</p>
      <details><summary>XSLT</summary><pre class="src">${esc(r.xslt)}</pre></details>${decide}</div>`;
  }

  screens.rules = async () => {
    helpId = "rules";
    const rules = await api("/api/rules");
    return `<h1>Rules ${helpBtn("rules", "Rules")}</h1>
      <p class="meta">Approved rules run on every migration without the agent. Each keeps its provenance: which run proposed it, who decided, and when.</p>
      ${rules.length ? `<div class="cards">${rules.map(ruleCard).join("")}</div>` : `<p>No rules in the store yet. Rules approved in the repository's <code>rules</code> folder also run; see the repository.</p>`}`;
  };

  screens.settings = async () => {
    helpId = "settings";
    const runs = await api("/api/runs?limit=200");
    const month = new Date().toISOString().slice(0, 7);
    const secs = runs.filter((r) => (r.started_at || "").startsWith(month)).reduce((a, r) => a + (r.duration_s || 0), 0);
    const vcpu = secs * 2;
    return `<h1>Settings and connections ${helpBtn("settings", "Settings")}</h1>
      <div class="table-wrap"><table><tbody>
      <tr><th scope="row">API</th><td class="mono">${esc(API)} · version ${esc(config.version)}</td></tr>
      <tr><th scope="row">Repository</th><td><a href="${esc(CFG.repo)}">${esc(CFG.repo)}</a></td></tr>
      <tr><th scope="row">Model</th><td class="mono">${esc(config.model)}</td></tr>
      <tr><th scope="row">Sign-in</th><td>${config.sign_in ? "Google, allowlisted addresses" : "Not configured: read-only"}</td></tr>
      <tr><th scope="row">Free-tier use this month</th><td>${vcpu.toLocaleString()} of 48,000 vCPU-seconds (the 20% share of Cloud Run jobs' 240,000), ${Math.round(vcpu / 480)}%</td></tr>
      </tbody></table></div>
      <h2>Audit trail</h2>${canWrite() ? await audit() : `<p>Sign in to read the audit trail.</p>`}`;
  };
  async function audit() {
    const rows = await api("/api/audit?limit=50");
    return `<div class="table-wrap"><table><thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Target</th></tr></thead><tbody>
      ${rows.map((e) => `<tr><td class="mono">${esc(e.updated_at)}</td><td>${esc(e.actor)}</td><td class="mono">${esc(e.action)}</td><td class="mono">${esc(e.target)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  // ------------------------------------------------------------ routing and events
  async function route() {
    const [path, query = ""] = (location.hash.slice(1) || "/overview").split("?");
    const parts = path.split("/").filter(Boolean);
    const params = new URLSearchParams(query);
    const main = $("#main");
    const section = parts[0] === "runs" ? "maps" : parts[0] || "overview";
    document.querySelectorAll("nav.main a").forEach((a) => {
      if (a.getAttribute("href") === `#/${section}`) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    try {
      main.innerHTML = `<p class="meta">Loading…</p>`;
      const html = parts[0] === "runs" ? await screens.run(decodeURIComponent(parts[1] || ""), params) : await (screens[parts[0]] || screens.overview)();
      main.innerHTML = html + `<p class="sig">— checked, not assumed</p>`;
      main.dataset.helpId = helpId;
      document.title = `${main.querySelector("h1")?.textContent.replace("?", "").trim() || "MANIFEST"} · MANIFEST console`;
    } catch (e) {
      main.innerHTML = `<h1>Something went wrong</h1><p class="notice err">${esc(e.message)}</p><p>The API may be starting up; it scales to zero when idle. Try again in a few seconds.</p>`;
    }
  }

  document.addEventListener("click", async (ev) => {
    const el = ev.target.closest("[data-help],[data-filter],[data-topic],[data-start],[data-approve],[data-reject]");
    if (!el) return;
    if (el.dataset.help) { ev.preventDefault(); ev.stopPropagation(); return openHelp(el.dataset.help); }
    const [path, query = ""] = location.hash.slice(1).split("?");
    const params = new URLSearchParams(query);
    if (el.dataset.filter) { params.set("status", el.dataset.filter); params.delete("topic"); location.hash = `${path}?${params}`; return; }
    if (el.dataset.topic) { params.set("topic", el.dataset.topic); location.hash = `${path}?${params}`; return; }
    const msg = $("#msg");
    try {
      if (el.dataset.start) {
        const mode = $(`#${el.dataset.modeSelect}`).value;
        const r = await api("/api/runs", { method: "POST", body: JSON.stringify({ map: el.dataset.start, mode }) });
        location.hash = `#/runs/${r.id}`;
      } else if (el.dataset.approve) {
        await api(`/api/rules/${encodeURIComponent(el.dataset.approve)}/approve`, { method: "POST" }); route();
      } else if (el.dataset.reject) {
        const reason = prompt("Why is this rule rejected?");
        if (!reason) return;
        await api(`/api/rules/${encodeURIComponent(el.dataset.reject)}/reject`, { method: "POST", body: JSON.stringify({ reason }) }); route();
      }
    } catch (e) { if (msg) msg.innerHTML = `<p class="notice err">${esc(e.message)}</p>`; }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.dataset?.topic) ev.target.click();
    if (ev.key === "Escape") closeHelp();
    if (ev.key === "?" && !/input|textarea|select/i.test(ev.target.tagName)) { ev.preventDefault(); openHelp($("#main").dataset.helpId || helpId); }
  });
  $("#help-btn").onclick = () => ($("#help").classList.contains("open") ? closeHelp() : openHelp($("#main").dataset.helpId || helpId));
  $("#help-close").onclick = closeHelp;
  $("#theme").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("manifest-theme", document.documentElement.dataset.theme); } catch { /* fine */ }
  };
  try { const t = localStorage.getItem("manifest-theme"); if (t) document.documentElement.dataset.theme = t; } catch { /* fine */ }
  window.addEventListener("hashchange", route);

  Promise.all([
    api("/api/config").then((c) => (config = c)).catch(() => {}),
    fetch(`${CFG.help}csh.json`).then((r) => r.json()).then((d) => (csh = d)).catch(() => {}),
  ]).then(() => { renderWho(); route(); });
})();
