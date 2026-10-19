"use strict";
/* EconAgent UI. No inline scripts (CSP). Dynamic text goes through textContent, or through DOMPurify which returns a
   sanitized DOM fragment (no innerHTML anywhere): report text can contain content that came from untrusted news. */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
function h(tag, props = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) el.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k.nodeType ? k : document.createTextNode(String(k)));
  return el;
}
const clear = (el) => { el.replaceChildren(); return el; };
const store = {
  get(k, d = null) { try { return localStorage.getItem("econagent." + k) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem("econagent." + k, v); } catch { /* private mode */ } },
};

DOMPurify.addHook("afterSanitizeAttributes", (n) => { if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener noreferrer"); } });
const mdFragment = (text) => DOMPurify.sanitize(marked.parse(text, { gfm: true }),
  { FORBID_ATTR: ["style"], FORBID_TAGS: ["style", "form", "input", "button"], RETURN_DOM_FRAGMENT: true });
const mdInto = (el, text) => { el.replaceChildren(mdFragment(text)); return el; };

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) { init.headers["Content-Type"] = "application/json"; init.body = JSON.stringify(opts.body); }
  const r = await fetch(path, init);
  let data = null;
  try { data = await r.json(); } catch { /* non-JSON error body */ }
  if (!r.ok) { const e = new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`); e.status = r.status; throw e; }
  return data;
}
const fmt = (x, d = 2) => (x == null || Number.isNaN(x) ? "–" : Number(x).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d }));
const sign = (x, d = 2) => (x == null || Number.isNaN(x) ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + fmt(Math.abs(x), d));
const dirClass = (x) => (x > 0 ? "up" : x < 0 ? "down" : "flat");
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind, text: msg }); $("#toasts").append(t); setTimeout(() => t.remove(), kind ? 6000 : 3500);
}
const typing = (el) => el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);

/* ------------------------------------------------------------------ theme */
const THEMES = ["auto", "light", "dark"], THEME_ICON = { auto: "◐", light: "☀", dark: "☾" };
function applyTheme(t) {
  if (t === "auto") document.documentElement.removeAttribute("data-theme"); else document.documentElement.dataset.theme = t;
  $("#themeBtn").textContent = THEME_ICON[t]; $("#themeBtn").title = `Theme: ${t} (t)`;
}
function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(store.get("theme", "auto")) + 1) % 3]; store.set("theme", next); applyTheme(next);
  toast(`Theme: ${next}`); if (macroLoaded) renderYield();
}

/* ------------------------------------------------------------------ sparkline */
function spark(points, color) {
  const NS = "http://www.w3.org/2000/svg", svg = document.createElementNS(NS, "svg");
  svg.setAttribute("class", "spark"); svg.setAttribute("viewBox", "0 0 100 30"); svg.setAttribute("preserveAspectRatio", "none");
  if (points.length > 1) {
    const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo || 1;
    const xy = points.map((v, i) => [(i / (points.length - 1)) * 100, 27 - ((v - lo) / span) * 24]);
    const d = xy.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)},${p[1].toFixed(2)}`).join("");
    const fill = document.createElementNS(NS, "path"); fill.setAttribute("class", "fill"); fill.setAttribute("d", `${d}L100,30L0,30Z`);
    const line = document.createElementNS(NS, "path"); line.setAttribute("class", "line"); line.setAttribute("d", d);
    if (color) { line.style.stroke = color; fill.style.fill = color; }
    svg.append(fill, line);
  }
  return svg;
}

/* ------------------------------------------------------------------ tabs */
const TABS = ["analyst", "macro", "watch", "reports", "journal", "system"];
const loaders = { macro: () => loadMacro(), watch: () => loadWatch(), reports: () => loadReports(), journal: () => loadJournal(), system: () => loadSystem() };
const loadedTabs = new Set();
let currentTab = "analyst";
function showTab(name, fromHash = false) {
  if (!TABS.includes(name)) name = "analyst";
  currentTab = name;
  $$("#tabs button").forEach((b) => { const on = b.dataset.tab === name; b.classList.toggle("active", on); b.setAttribute("aria-selected", on); });
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  if (!fromHash && location.hash.slice(1) !== name) history.replaceState(null, "", "#" + name);
  if (loaders[name] && !loadedTabs.has(name)) { loadedTabs.add(name); loaders[name](); }
  if (name === "watch") scheduleWatchPoll(true);
}
$("#tabs").addEventListener("click", (e) => { const b = e.target.closest("button[data-tab]"); if (b) showTab(b.dataset.tab); });
window.addEventListener("hashchange", () => showTab(location.hash.slice(1), true));
function refreshCurrent() {
  const f = { macro: () => loadMacro(true), watch: () => loadWatch(), reports: () => loadReports(), journal: () => loadJournal(), system: () => loadSystem() }[currentTab];
  if (f) { f(); toast("Refreshed"); } else toast("Nothing to refresh on this tab");
}

/* ------------------------------------------------------------------ status / usage */
let status = null;
async function refreshStatus() {
  try { status = await api("/api/status"); } catch { return; }
  const fh = (status.usage && status.usage.five_hour) || null, u = fh && fh.utilization != null ? fh.utilization : null;
  $("#usageFill").style.width = u == null ? "0%" : Math.min(100, u * 100) + "%";
  $("#usageFill").style.background = u > 0.9 ? "var(--bad)" : u > 0.7 ? "var(--warn)" : "var(--good)";
  let t = u == null ? "no model use yet" : `5h ${Math.round(u * 100)}%`;
  if (fh && fh.resetsAt && u > 0.5) t += ` · resets ${new Date(fh.resetsAt * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
  $("#usageText").textContent = t;
}

/* ------------------------------------------------------------------ live market bar */
const prevTicks = {};
function tickFmt(sym, v) {
  if (sym === "^TNX") return fmt(v, 2) + "%";
  return v >= 1000 ? fmt(v, 0) : fmt(v, 2);
}
function renderTicker(d) {
  const wrap = $("#tickerItems"), first = !wrap.children.length;
  const m = d.market || {};
  $("#mktStatus").className = "mkt" + (m.is_open ? " open" : "");
  const tz = { timeZone: "America/New_York" };
  const tm = (iso) => new Date(iso).toLocaleTimeString("en-US", { ...tz, hour: "numeric", minute: "2-digit" });
  const dayTm = (iso) => new Date(iso).toLocaleString("en-US", { ...tz, weekday: "short", hour: "numeric", minute: "2-digit" }).replace(",", "");
  $("#mktStatus span").textContent = m.is_open ? `Open${m.next_close ? " · closes " + tm(m.next_close) + " ET" : ""}` : `Closed${m.next_open ? " · opens " + dayTm(m.next_open) + " ET" : ""}`;
  if (first) for (const it of d.items) wrap.append(h("button", { class: "tk", "data-sym": it.symbol, title: "Open chart", onclick: () => openStockDrawer(it.symbol, it.label) }));
  for (const it of d.items) {
    const el = wrap.querySelector(`[data-sym="${CSS.escape(it.symbol)}"]`); if (!el) continue;
    if (it.error) { el.replaceChildren(h("span", { class: "l", text: it.label }), h("b", { text: "–" })); continue; }
    el.replaceChildren(h("span", { class: "l", text: it.label }), h("b", { text: tickFmt(it.symbol, it.last) }),
      h("span", { class: "c " + dirClass(it.chg_pct), text: `${sign(it.chg_pct)}%` }));
    const p = prevTicks[it.symbol];
    if (p != null && p !== it.last) { el.classList.remove("flash-up", "flash-down"); void el.offsetWidth; el.classList.add(it.last > p ? "flash-up" : "flash-down"); }
    prevTicks[it.symbol] = it.last;
  }
  $("#liveStamp").textContent = new Date(d.ts * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}
let liveTimer = null, lastMarketOpen = true;
async function pollLive() {
  clearTimeout(liveTimer);
  if (!document.hidden) {
    try { const d = await api("/api/live"); lastMarketOpen = !!(d.market && d.market.is_open); renderTicker(d); }
    catch { $("#liveStamp").textContent = "live data unavailable"; }
  }
  liveTimer = setTimeout(pollLive, document.hidden ? 5000 : lastMarketOpen ? 15000 : 60000);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollLive(); if (currentTab === "watch") scheduleWatchPoll(true); } });

/* ------------------------------------------------------------------ jobs (SSE) */
let activeJob = null;
function watchJob(jobId, handlers) {
  activeJob = jobId;
  const es = new EventSource(`/api/jobs/${jobId}/events`);
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    (handlers[ev.type] || (() => {}))(ev);
    if (ev.type === "end") { es.close(); activeJob = null; document.title = "EconAgent"; refreshStatus(); }
  };
  es.onerror = () => es.close();
  return es;
}
const shortTool = (n) => n.replace(/^mcp__/, "").replace("__", " › ");
function notice(msg, cls = "", actions = []) {
  const n = $("#notice"); clear(n); n.hidden = !msg; n.className = "notice " + cls;
  if (msg) n.append(msg, ...actions.flatMap((a) => [" ", a]));
}
function setBusy(b) {
  $("#runAnalysis").disabled = b; $("#send").disabled = b; $("#refreshUniverse").disabled = b; $("#cancelJob").hidden = !b;
  $("#spinner").classList.toggle("off", !b); if (b) document.title = "EconAgent · working…";
}
async function startAnalysis(force = false) {
  notice("");
  try {
    const { job_id } = await api("/api/analyze", { method: "POST", body: { task: $("#task").value, candidates: +$("#cands").value, max_ideas: +$("#ideas").value, force } });
    runProgress(job_id, "analysis");
  } catch (e) {
    if (e.status === 429) notice(e.message, "", [h("button", { class: "ghost sm", text: "Run anyway", onclick: () => startAnalysis(true) })]);
    else notice(e.message, "err");
  }
}
function runProgress(jobId, kind) {
  setBusy(true); clear($("#toolLog")); $("#progress").hidden = false; $("#stageLabel").textContent = "Starting…";
  const log = (t) => { const ul = $("#toolLog"); ul.append(h("li", { text: t })); ul.scrollTop = 1e9; };
  watchJob(jobId, {
    stage: (ev) => { $("#stageLabel").textContent = ev.label; },
    tool: (ev) => log(shortTool(ev.name)), text: (ev) => log(ev.text.slice(0, 160)),
    stage_done: (ev) => log(`— stage ${ev.stage} done: ${ev.tools} tool calls, ${ev.seconds}s`),
    end: (ev) => {
      setBusy(false);
      if (ev.status === "done" && ev.result.report_id) {
        $("#stageLabel").textContent = "Finished.";
        notice("Analysis complete. ", "", [h("button", { class: "primary sm", text: "Open report", onclick: () => openReport(ev.result.report_id) })]);
        loadedTabs.delete("reports");
      } else if (ev.status === "done") { $("#stageLabel").textContent = "Finished."; notice(kind === "universe" ? "Screener universe refreshed." : "Done."); }
      else if (ev.status === "cancelled") { $("#stageLabel").textContent = "Cancelled."; }
      else { $("#stageLabel").textContent = "Failed."; notice(ev.result.error || "The run failed.", "err"); }
    },
  });
}
$("#runAnalysis").addEventListener("click", () => startAnalysis(false));
$("#task").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!$("#runAnalysis").disabled) startAnalysis(false); } });
$("#cancelJob").addEventListener("click", () => activeJob && api(`/api/jobs/${activeJob}/cancel`, { method: "POST", body: {} }));
async function refreshUniverse() {
  try { const { job_id } = await api("/api/universe/refresh", { method: "POST", body: {} }); showTab("analyst"); runProgress(job_id, "universe"); }
  catch (e) { toast(e.message, "err"); }
}
$("#refreshUniverse").addEventListener("click", refreshUniverse);

/* ------------------------------------------------------------------ chat */
let sessionId = null;
function newChat() { sessionId = null; clear($("#messages")); showTab("analyst"); $("#chatInput").focus(); }
$("#newChat").addEventListener("click", newChat);
async function sendChat(force = false, textOverride = null) {
  const input = $("#chatInput"), text = textOverride ?? input.value.trim();
  if (!text) return;
  const box = $("#messages");
  if (!force) { box.append(h("div", { class: "msg user", text })); input.value = ""; }
  const toolsLine = h("div", { class: "msg tools", text: "thinking…" }); box.append(toolsLine); box.scrollTop = 1e9;
  try {
    const r = await api("/api/chat", { method: "POST", body: { message: text, session_id: sessionId, model: $("#chatModel").value, force } });
    sessionId = r.session_id; setBusy(true);
    let n = 0;
    watchJob(r.job_id, {
      tool: (ev) => { n++; toolsLine.textContent = `${n} tool call${n > 1 ? "s" : ""} · ${shortTool(ev.name)}`; },
      end: (ev) => {
        setBusy(false);
        if (ev.status === "done") { toolsLine.remove(); box.append(mdInto(h("div", { class: "msg bot" }), ev.result.text || "")); }
        else toolsLine.textContent = "Failed: " + (ev.result.error || ev.status);
        box.scrollTop = 1e9;
      },
    });
  } catch (e) {
    toolsLine.textContent = e.message;
    if (e.status === 429) toolsLine.append(" ", h("button", { class: "ghost sm", text: "Send anyway", onclick: () => { toolsLine.remove(); sendChat(true, text); } }));
  }
}
function ask(text, send = true) { showTab("analyst"); $("#chatInput").value = text; if (send) sendChat(); else $("#chatInput").focus(); }
$("#send").addEventListener("click", () => sendChat());
$("#chatInput").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!$("#send").disabled) sendChat(); } });
function renderChips() {
  const wl = watchState.symbols.slice(0, 6).join(", ") || "AAPL, MSFT";
  const prompts = [["Macro in plain English", "Summarize the current US macro regime in plain English, with the three numbers that matter most."],
    ["Risks to my watchlist", `What are the biggest macro risks to these stocks right now: ${wl}? Show the downside for each.`],
    ["Latest FOMC changes", "What changed in the latest FOMC statement versus the previous one, and is it hawkish or dovish?"],
    ["Compare two stocks", "Compare NVDA and MSFT on valuation, growth and rate sensitivity. Which has the better risk/reward today?"]];
  clear($("#chips")).append(...prompts.map(([label, p]) => h("button", { class: "ghost", text: label, onclick: () => ask(p) })));
}

/* ------------------------------------------------------------------ macro */
let macroLoaded = false, macroTiles = null;
const fmtTile = (t, v) => (t.unit === "%" ? `${fmt(v, t.decimals)}%` : t.unit === "k" ? `${fmt(v, 0)}k` : t.unit === " pts" ? `${fmt(v, t.decimals)} pts` : fmt(v, t.decimals));
const PRIOR = { daily: "prior day", weekly: "prior week", monthly: "prior month", quarterly: "prior quarter" };
function tileEl(t) {
  if (t.error) return h("div", { class: "tile" }, h("div", { class: "k", text: t.label }), h("div", { class: "v muted", text: "–" }), h("div", { class: "d", text: "unavailable" }));
  const dlt = t.latest.value - t.prev.value, dec = t.decimals;
  return h("button", { class: "tile", title: `${t.id} · click for chart`, onclick: () => openFredDrawer(t.id, { units: t.units, label: t.label }) },
    h("div", { class: "k", text: t.label }), h("div", { class: "v", text: fmtTile(t, t.latest.value) }),
    h("div", { class: "d" }, h("span", { text: `${dlt > 0 ? "▲" : dlt < 0 ? "▼" : "■"} ${fmt(Math.abs(dlt), dec)}${t.unit === "%" ? " pts" : ""} vs ${PRIOR[t.cadence]}` }), h("span", { class: "age", text: t.latest.date.slice(5) })),
    spark(t.spark));
}
async function loadMacro(force = false) {
  macroLoaded = true; if (force) yieldData = null;
  const groups = $("#macroGroups");
  if (force || !macroTiles) {
    clear(groups).append(h("div", { class: "tiles" }, ...Array.from({ length: 8 }, () => h("div", { class: "tile skeleton", style: "height:112px" }))));
    api("/api/macro").then((m) => clear($("#macroRead")).append(...(m.read || []).map((r) => h("span", { class: "chip", text: r })))).catch(() => {});
    try { macroTiles = await api("/api/macro/tiles"); }
    catch (e) { clear(groups).append(h("div", { class: "notice err", text: "Macro data unavailable: " + e.message }, " ", h("button", { class: "ghost sm", text: "Retry", onclick: () => loadMacro(true) }))); return; }
  }
  clear(groups);
  for (const g of macroTiles.groups) {
    const ts = macroTiles.tiles.filter((t) => t.group === g);
    groups.append(h("div", { class: "group-title" }, h("h3", { text: g })), h("div", { class: "tiles" }, ts.map(tileEl)));
  }
  $("#macroNote").textContent = `Source: FRED · as of ${macroTiles.as_of} · click any card for an interactive chart`;
  renderYield(); renderFedDiff();
}
$("#reloadMacro").addEventListener("click", () => loadMacro(true));

let yieldData = null, yieldChart = null;
async function renderYield() {
  const host = $("#ycChart");
  try { yieldData = yieldData || await api("/api/yieldcurve"); } catch (e) { host.textContent = "Yield curve unavailable: " + e.message; return; }
  const colors = [css("--accent"), css("--faint"), "#d9902b"];
  if (yieldChart) yieldChart.destroy();
  yieldChart = new LineChart(host, { height: 250, mode: "category" });
  yieldChart.setSeries(yieldData.curves.map((c, i) => ({ name: `${c.name}`, color: colors[i], decimals: 2, suffix: "%", points: c.points.filter((p) => p.value != null).map((p) => [p.label, p.value]) })));
  $("#ycNote").textContent = `as of ${yieldData.curves[0].date}`;
  clear($("#ycLegend")).append(...yieldData.curves.map((c, i) => h("span", {}, h("i", { style: `background:${colors[i]}` }), `${c.name} (${c.date})`)));
}
async function renderFedDiff() {
  const box = $("#fedDiff");
  try {
    const d = await api("/api/fed/diff");
    $("#fedNote").textContent = `${d.old.date} → ${d.new.date}`;
    clear(box);
    for (const c of d.changes) {
      box.append(h("div", { class: "chg" }, c.old ? h("div", {}, h("span", { class: "lab", text: c.new ? "Before" : "Removed" }), h("del", { text: c.old })) : null,
        c.new ? h("div", {}, h("span", { class: "lab", text: c.old ? "After" : "Added" }), h("ins", { text: c.new })) : null));
    }
    box.append(h("p", { class: "muted small", text: `${d.unchanged} sentences unchanged. ` }, h("a", { href: d.new.url, target: "_blank", rel: "noopener noreferrer", text: "Read the full statement" })));
  } catch (e) { clear(box).append(h("p", { class: "muted", text: "Unavailable: " + e.message })); }
}

/* FRED series search (used by the macro tab and by chart compare) */
function attachSearch(input, dropdown, onPick) {
  let timer = null, items = [], sel = -1;
  const close = () => { dropdown.hidden = true; sel = -1; };
  const paint = () => $$("button", dropdown).forEach((b, i) => b.classList.toggle("sel", i === sel));
  async function run() {
    const q = input.value.trim(); if (q.length < 2) return close();
    try { items = await api("/api/fred/search?q=" + encodeURIComponent(q)); } catch (e) { items = []; }
    clear(dropdown);
    if (!items.length) dropdown.append(h("div", { class: "cmd-empty", text: "No series found" }));
    items.forEach((s) => dropdown.append(h("button", { type: "button", onclick: () => { close(); input.value = ""; onPick(s); } }, h("b", { text: s.id }), " ", s.title, h("small", { text: `${s.frequency || ""} · ${s.units || ""} · through ${s.last || "?"}` }))));
    dropdown.hidden = false; sel = -1;
  }
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(run, 300); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === "Enter" && sel >= 0) { e.preventDefault(); const s = items[sel]; close(); input.value = ""; onPick(s); }
    else if (e.key === "Escape") { if (!dropdown.hidden) { e.stopPropagation(); close(); } }
  });
  document.addEventListener("click", (e) => { if (!dropdown.contains(e.target) && e.target !== input) close(); });
}
attachSearch($("#fredSearch"), $("#fredResults"), (s) => openFredDrawer(s.id, { units: "lin", label: s.title }));

/* ------------------------------------------------------------------ drawer */
let drawerCleanup = null;
function closeDrawer() {
  if (drawerCleanup) drawerCleanup(); drawerCleanup = null;
  $$(".scrim.drawer-scrim, .drawer").forEach((e) => e.remove());
}
function openDrawer(build) {
  closeDrawer();
  const scrim = h("div", { class: "scrim drawer-scrim", onclick: closeDrawer }), d = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true" });
  document.body.append(scrim, d);
  drawerCleanup = build(d) || null;
}
const seg = (opts, current, onPick) => {
  const el = h("div", { class: "seg" });
  const paint = (v) => $$("button", el).forEach((b) => b.classList.toggle("active", b.dataset.v === v));
  for (const [v, label] of opts) el.append(h("button", { type: "button", "data-v": v, text: label, onclick: () => { paint(v); onPick(v); } }));
  paint(current); return el;
};
const FRED_UNITS = [["lin", "Level"], ["pc1", "YoY %"], ["chg", "Change"], ["pch", "% chg"]];

function openFredDrawer(id, { units = "lin", label = "" } = {}) {
  openDrawer((d) => {
    const st = { id, units, range: "5y", compare: null, chart: null };
    const title = h("h2", { text: label || id }), meta = h("div", { class: "muted small" }), big = h("div", { class: "big" }), delta = h("div", { class: "muted small" });
    const host = h("div", { class: "chart-host" }), legend = h("div", { class: "legend" });
    const cmpInput = h("input", { placeholder: "Compare with another series…", size: "26", "aria-label": "Compare series" }), cmpDrop = h("div", { class: "dropdown", hidden: true });
    const cmpBtn = h("button", { class: "ghost sm", hidden: true, onclick: () => { st.compare = null; cmpBtn.hidden = true; load(); } }, "Remove comparison");
    async function load() {
      host.replaceChildren(h("div", { class: "skeleton", style: "height:340px" }));
      try {
        const main = await api(`/api/series/${st.id}?range=${st.range}&units=${st.units}`);
        let cmp = null;
        if (st.compare) cmp = await api(`/api/series/${st.compare.id}?range=${st.range}&units=lin`);
        title.textContent = main.title; meta.textContent = `${main.id} · ${main.frequency || ""} · ${main.units_label} · updated ${(main.last_updated || "").slice(0, 10)}`;
        const last = main.points[main.points.length - 1], prev = main.points[main.points.length - 2];
        const pct = st.units === "pc1" || st.units === "pch" || (st.units === "lin" && /percent/i.test(main.native_units || ""));
        big.textContent = fmt(last[1], Math.abs(last[1]) >= 1000 ? 0 : 2) + (pct ? "%" : ""); delta.textContent = prev ? `${last[0]} · ${sign(last[1] - prev[1])} vs prior reading` : last[0];
        const series = [{ name: main.units === "lin" ? main.id : `${main.id} (${main.units_label})`, color: css("--accent"), points: main.points, axis: "left", decimals: 2 }];
        if (cmp) series.push({ name: cmp.id, color: "#d9902b", points: cmp.points, axis: "right", decimals: 2 });
        if (st.chart) st.chart.destroy(); st.chart = new LineChart(host, { height: 340 }); st.chart.setSeries(series);
        clear(legend).append(...[h("span", {}, h("i", { style: `background:${css("--accent")}` }), main.title), cmp ? h("span", {}, h("i", { style: "background:#d9902b" }), `${cmp.title} (right axis)`) : null].filter(Boolean));
      } catch (e) { host.replaceChildren(h("div", { class: "notice err", text: e.message })); }
    }
    attachSearch(cmpInput, cmpDrop, (s) => { st.compare = s; cmpBtn.hidden = false; load(); });
    d.append(h("div", { class: "drawer-head" }, h("div", {}, title, meta), h("button", { class: "ghost sm", "aria-label": "Close", onclick: closeDrawer }, "✕ ", h("kbd", { text: "Esc" }))), big, delta,
      h("div", { class: "controls" }, seg([["1y", "1Y"], ["2y", "2Y"], ["5y", "5Y"], ["10y", "10Y"], ["max", "Max"]], st.range, (v) => { st.range = v; load(); }),
        seg(FRED_UNITS, st.units, (v) => { st.units = v; load(); }),
        h("div", { class: "searchbox", style: "min-width:200px" }, cmpInput, cmpDrop), cmpBtn),
      host, legend,
      h("div", { class: "row" }, h("a", { href: `https://fred.stlouisfed.org/series/${id}`, target: "_blank", rel: "noopener noreferrer", text: "Open on FRED ↗" }), h("span", { class: "spacer" }),
        h("button", { class: "ghost sm", onclick: () => { closeDrawer(); ask(`Explain the recent trend in ${st.id} (${title.textContent}) and what it implies for US equities. Use current data.`); } }, "Ask the analyst about this")));
    load();
    return () => { if (st.chart) st.chart.destroy(); };
  });
}

function openStockDrawer(symbol, label = "") {
  openDrawer((d) => {
    const st = { range: "6mo", chart: null };
    const title = h("h2", { text: label ? `${label} (${symbol})` : symbol }), big = h("div", { class: "big", text: "…" }), delta = h("div", { class: "muted small" });
    const host = h("div", { class: "chart-host" });
    async function load() {
      host.replaceChildren(h("div", { class: "skeleton", style: "height:340px" }));
      try {
        const [hs, q] = await Promise.all([api(`/api/history/${encodeURIComponent(symbol)}?range=${st.range}`), api(`/api/quotes?symbols=${encodeURIComponent(symbol)}`)]);
        const qq = q.quotes[0] || {};
        if (qq.last != null) { big.textContent = symbol === "^TNX" ? fmt(qq.last, 2) + "%" : fmt(qq.last, qq.last >= 1000 ? 0 : 2); delta.className = "small " + dirClass(qq.chg_pct); delta.textContent = `${sign(qq.chg)} (${sign(qq.chg_pct)}%) today`; }
        const first = hs.points[0][1], last = hs.points[hs.points.length - 1][1];
        const color = last >= first ? css("--good") : css("--bad");
        if (st.chart) st.chart.destroy(); st.chart = new LineChart(host, { height: 340 });
        st.chart.setSeries([{ name: symbol, color, points: hs.points, decimals: 2 }]);
      } catch (e) { host.replaceChildren(h("div", { class: "notice err", text: e.message })); }
    }
    d.append(h("div", { class: "drawer-head" }, h("div", {}, title), h("button", { class: "ghost sm", "aria-label": "Close", onclick: closeDrawer }, "✕ ", h("kbd", { text: "Esc" }))), big, delta,
      h("div", { class: "controls" }, seg([["1d", "1D"], ["5d", "5D"], ["1mo", "1M"], ["6mo", "6M"], ["1y", "1Y"], ["5y", "5Y"]], st.range, (v) => { st.range = v; load(); })),
      host,
      h("div", { class: "row" }, !symbol.startsWith("^") && !symbol.includes("=") && !symbol.includes("-USD") ? h("button", { class: "ghost sm", onclick: () => { addWatch(symbol); } }, "Add to watchlist") : null,
        h("span", { class: "spacer" }), h("button", { class: "ghost sm", onclick: () => { closeDrawer(); ask(`Give me a concise risk/reward view on ${symbol} right now, using current data.`); } }, "Ask the analyst about this")));
    load();
    return () => { if (st.chart) st.chart.destroy(); };
  });
}

/* ------------------------------------------------------------------ watchlist (live) */
const watchState = { symbols: [], stats: {}, quotes: {}, sort: { key: null, dir: -1 } };
let watchTimer = null;
async function loadWatch() {
  const tb = clear($("#watchTable tbody"));
  tb.append(h("tr", {}, h("td", { colspan: 7 }, h("div", { class: "skeleton", style: "height:90px" }))));
  try {
    const w = await api("/api/watchlist");
    watchState.symbols = w.symbols; watchState.stats = Object.fromEntries(w.rows.map((r) => [r.symbol, r]));
    renderWatch(); renderChips(); pollWatch();
  } catch (e) { clear(tb).append(h("tr", {}, h("td", { colspan: 7, class: "muted", text: e.message }))); }
}
function renderWatch() {
  const tb = clear($("#watchTable tbody"));
  const rows = watchState.symbols.map((s) => ({ symbol: s, ...(watchState.stats[s] || {}), ...(watchState.quotes[s] || {}) }));
  const { key, dir } = watchState.sort;
  if (key) rows.sort((a, b) => ((a[key] ?? -Infinity) > (b[key] ?? -Infinity) ? 1 : -1) * dir);
  $$("#watchTable th[data-sort]").forEach((th) => { th.classList.toggle("sorted", th.dataset.sort === key); th.classList.toggle("asc", th.dataset.sort === key && dir === 1); });
  if (!rows.length) tb.append(h("tr", {}, h("td", { colspan: 7, class: "muted", text: "Your watchlist is empty. Add a symbol above." })));
  for (const r of rows) {
    const live = r.last != null;
    tb.append(h("tr", { class: "clickable", "data-sym": r.symbol, onclick: (e) => { if (!e.target.closest(".x")) openStockDrawer(r.symbol); } },
      h("td", { class: "sym", text: r.symbol }), h("td", { class: "num last", text: live ? fmt(r.last) : "–" }),
      h("td", { class: "num chg " + (live ? dirClass(r.chg_pct) : ""), text: live ? `${sign(r.chg_pct)}%` : "–" }),
      h("td", { style: "min-width:110px" }, r.spark ? spark(r.spark) : null),
      h("td", { class: "num", text: r.vol_ann_pct != null ? fmt(r.vol_ann_pct, 1) + "%" : "–" }),
      h("td", { class: "num down", text: r.drawdown_1m_pct != null ? fmt(r.drawdown_1m_pct, 1) + "%" : "–" }),
      h("td", {}, h("button", { class: "x", title: "Remove", "aria-label": `Remove ${r.symbol}`, onclick: () => removeWatch(r.symbol) }, "✕"))));
  }
}
$("#watchTable thead").addEventListener("click", (e) => {
  const th = e.target.closest("th[data-sort]"); if (!th) return;
  const k = th.dataset.sort; watchState.sort = { key: k, dir: watchState.sort.key === k ? -watchState.sort.dir : k === "symbol" ? 1 : -1 }; renderWatch();
});
async function pollWatch() {
  if (!watchState.symbols.length || document.hidden || currentTab !== "watch") return;
  try {
    const q = await api("/api/quotes?symbols=" + encodeURIComponent(watchState.symbols.join(",")));
    for (const x of q.quotes) {
      if (x.error) continue;
      const old = watchState.quotes[x.symbol]; watchState.quotes[x.symbol] = { last: x.last, chg_pct: x.chg_pct };
      const row = $(`#watchTable tr[data-sym="${CSS.escape(x.symbol)}"]`);
      if (row) {
        $(".last", row).textContent = fmt(x.last); const c = $(".chg", row); c.textContent = `${sign(x.chg_pct)}%`; c.className = "num chg " + dirClass(x.chg_pct);
        if (old && old.last !== x.last) { const cell = $(".last", row); cell.classList.remove("flash-up", "flash-down"); void cell.offsetWidth; cell.classList.add(x.last > old.last ? "flash-up" : "flash-down"); }
      }
    }
  } catch { /* transient; next poll retries */ }
}
function scheduleWatchPoll(now = false) {
  clearTimeout(watchTimer);
  if (currentTab !== "watch") return;
  watchTimer = setTimeout(async () => { await pollWatch(); scheduleWatchPoll(); }, now ? 0 : lastMarketOpen ? 15000 : 60000);
}
async function saveWatch(symbols) {
  const r = await api("/api/watchlist", { method: "PUT", body: { symbols } });
  watchState.symbols = r.symbols; return r.symbols;
}
async function addWatch(raw) {
  const sym = raw.trim().toUpperCase();
  if (!/^[A-Z]{1,5}(-[A-Z])?$/.test(sym)) return toast(`"${raw}" is not a valid stock symbol`, "err");
  if (watchState.symbols.includes(sym)) return toast(`${sym} is already on your watchlist`);
  try { await saveWatch([...watchState.symbols, sym]); toast(`Added ${sym}`); loadedTabs.add("watch"); await loadWatch(); } catch (e) { toast(e.message, "err"); }
}
async function removeWatch(sym) {
  try { await saveWatch(watchState.symbols.filter((s) => s !== sym)); renderWatch(); renderChips(); } catch (e) { toast(e.message, "err"); }
}
$("#watchForm").addEventListener("submit", (e) => { e.preventDefault(); const v = $("#watchInput").value; $("#watchInput").value = ""; if (v.trim()) addWatch(v); });

/* ------------------------------------------------------------------ reports */
let currentReport = null;
async function loadReports() {
  const ul = clear($("#reportList"));
  const list = await api("/api/reports").catch(() => []);
  if (!list.length) ul.append(h("li", { class: "muted", text: "No reports yet." }));
  for (const r of list) ul.append(h("li", { "data-id": r.id, onclick: () => openReport(r.id) },
    r.id.replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2}).*/, "$1-$2-$3 $4:$5"), h("small", { text: r.task || "(no task)" })));
}
async function openReport(id) {
  showTab("reports"); loadedTabs.add("reports"); await loadReports();
  $$("#reportList li").forEach((li) => li.classList.toggle("active", li.dataset.id === id));
  try {
    const r = await api("/api/reports/" + id); currentReport = r;
    mdInto($("#reportView"), r.markdown); $("#copyReport").disabled = $("#downloadReport").disabled = false;
    const box = clear($("#reportEntries"));
    if (r.journal_entries.length) box.append(h("h3", { text: "Proposed journal entries" }), h("p", { class: "muted", text: "The analyst proposes; nothing is logged until you click." }));
    for (const e of r.journal_entries) {
      const st = h("span", { class: "muted" });
      box.append(h("div", { class: "entry" },
        h("div", {}, h("b", { text: `${e.symbol} ${e.direction}` }), ` · entry ${fmt(e.entry_price)} → target ${fmt(e.target_price)} / stop ${fmt(e.stop_price)} · ${e.horizon_days}d · p=${e.probability}`),
        h("span", {}, st, " ", h("button", { class: "primary sm", text: "Log to journal", onclick: async (ev) => {
          try {
            await api("/api/journal/log", { method: "POST", body: { symbol: e.symbol, direction: e.direction, thesis: e.thesis || "", entry_price: e.entry_price, horizon_days: e.horizon_days,
              probability: e.probability, target_price: e.target_price ?? null, stop_price: e.stop_price ?? null, expected_return_pct: e.expected_return_pct ?? null,
              max_drawdown_pct: e.max_drawdown_pct ?? null, kronos_used: !!e.kronos_used, sources: e.sources || [] } });
            st.textContent = "Logged ✓"; ev.target.disabled = true; loadedTabs.delete("journal");
          } catch (err) { st.textContent = err.message; }
        } }))));
    }
  } catch (e) { $("#reportView").textContent = e.message; }
}
$("#copyReport").addEventListener("click", async () => { try { await navigator.clipboard.writeText(currentReport.markdown); toast("Report copied as markdown"); } catch { toast("Copy failed", "err"); } });
$("#downloadReport").addEventListener("click", () => {
  const a = h("a", { href: URL.createObjectURL(new Blob([currentReport.markdown], { type: "text/markdown" })), download: `econagent-${currentReport.id}.md` });
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});

/* ------------------------------------------------------------------ journal */
async function loadJournal() {
  const j = await api("/api/journal").catch((e) => { toast(e.message, "err"); return null; });
  if (!j) return;
  const c = j.calibration, ct = clear($("#calib"));
  const tile = (k, v, d) => h("div", { class: "tile" }, h("div", { class: "k", text: k }), h("div", { class: "v", text: v }), h("div", { class: "d", text: d || "" }));
  ct.append(tile("Closed ideas", c.closed ?? 0, `${c.open ?? j.open.length} open`));
  if (c.closed) ct.append(tile("Hit rate", Math.round(c.hit_rate * 100) + "%", "partial counts ½"), tile("Brier score", c.brier, "lower is better · 0.25 = coin flip"), tile("Avg realized", sign(c.avg_realized_return_pct) + "%", "small samples are noise"));
  else ct.append(h("p", { class: "muted", text: "Nothing to score yet. Log ideas from a report, then close them with the outcome." }));
  const due = clear($("#dueBox"));
  if (j.due.length) due.append(h("div", { class: "notice", text: `${j.due.length} idea(s) are past their horizon and need closing: ${j.due.map((d) => d.symbol + " #" + d.id).join(", ")}` }));
  const ob = clear($("#openTable tbody"));
  if (!j.open.length) ob.append(h("tr", {}, h("td", { colspan: 9, class: "muted", text: "No open ideas." })));
  for (const i of j.open) {
    const review = new Date(Date.parse(i.created_at) + i.horizon_days * 864e5).toISOString().slice(0, 10), cell = h("td");
    ob.append(h("tr", {}, h("td", { text: i.id }), h("td", { class: "sym", text: i.symbol }), h("td", { text: i.direction }), h("td", { class: "num", text: fmt(i.entry_price) }),
      h("td", { class: "num", text: fmt(i.target_price) }), h("td", { class: "num", text: fmt(i.stop_price) }), h("td", { class: "num", text: Math.round(i.probability * 100) + "%" }), h("td", { text: review }), cell));
    cell.append(h("button", { class: "ghost sm", text: "Close…", onclick: () => closeForm(cell, i) }));
  }
  const cb = clear($("#closedTable tbody"));
  if (!j.closed.length) cb.append(h("tr", {}, h("td", { colspan: 6, class: "muted", text: "Nothing closed yet." })));
  for (const i of j.closed) cb.append(h("tr", {}, h("td", { text: i.id }), h("td", { class: "sym", text: i.symbol }), h("td", { text: i.direction }),
    h("td", {}, h("span", { class: "tag " + (i.outcome === "hit" ? "good" : i.outcome === "miss" ? "bad" : "warn"), text: i.outcome })),
    h("td", { class: "num " + dirClass(i.realized_return_pct), text: sign(i.realized_return_pct) + "%" }), h("td", { text: i.note || "" })));
}
function closeForm(cell, idea) {
  clear(cell);
  const px = h("input", { type: "number", step: "0.01", placeholder: "exit price", style: "width:110px" });
  const oc = h("select", {}, h("option", { value: "hit", text: "hit" }), h("option", { value: "partial", text: "partial" }), h("option", { value: "miss", text: "miss" }));
  cell.append(px, " ", oc, " ", h("button", { class: "primary sm", text: "Save", onclick: async () => {
    try { await api("/api/journal/close", { method: "POST", body: { idea_id: idea.id, exit_price: +px.value, outcome: oc.value, note: "" } }); loadJournal(); toast("Idea closed"); }
    catch (e) { toast(e.message, "err"); }
  } }));
  px.focus();
}

/* ------------------------------------------------------------------ shortcuts, help, palette */
const SHORTCUTS = [["⌘/Ctrl K", "Command palette"], ["1 – 6", "Switch tab"], ["/", "Ask the analyst"], ["n", "New chat"], ["r", "Refresh this tab"],
  ["t", "Toggle theme"], ["?", "Show shortcuts"], ["Esc", "Close panel"], ["Enter", "Send chat message"], ["⌘/Ctrl Enter", "Run analysis (in the task box)"]];
function renderShortcuts() { clear($("#shortcutList")).append(...SHORTCUTS.flatMap(([k, d]) => [h("span", {}, h("kbd", { text: k })), h("span", { text: d })])); }
$("#showHelp").addEventListener("click", () => openHelp());

let overlay = null;
function closeOverlay() { if (overlay) { overlay.forEach((e) => e.remove()); overlay = null; return true; } return false; }
function openHelp() {
  closeOverlay();
  const scrim = h("div", { class: "scrim", onclick: closeOverlay });
  const grid = h("div", { class: "help-grid" }, SHORTCUTS.flatMap(([k, d]) => [h("span", {}, h("kbd", { text: k })), h("span", { text: d })]));
  overlay = [scrim, h("div", { class: "modal", role: "dialog", "aria-label": "Keyboard shortcuts" }, h("h2", { text: "Keyboard shortcuts" }), grid, h("div", { class: "hint", text: "Shortcuts are disabled while typing in a field." }))];
  document.body.append(...overlay);
}
const SYMBOLISH = /^[A-Za-z^][A-Za-z0-9.=^-]{0,11}$/;
function commandList(q) {
  const go = (tab, label, ico, hint) => ({ label: `Go to ${label}`, ico, hint, run: () => showTab(tab) });
  const base = [go("analyst", "Analyst", "◆", "1"), go("macro", "Macro", "◆", "2"), go("watch", "Watchlist", "◆", "3"), go("reports", "Reports", "◆", "4"), go("journal", "Journal", "◆", "5"), go("system", "System", "◆", "6"),
    { label: "Run full analysis", ico: "▶", hint: "", run: () => { showTab("analyst"); startAnalysis(false); } },
    { label: "Refresh screener universe", ico: "↻", hint: "no model usage", run: refreshUniverse },
    { label: "New chat", ico: "✎", hint: "n", run: newChat }, { label: "Reload this tab's data", ico: "↻", hint: "r", run: refreshCurrent },
    { label: "Toggle theme", ico: "◐", hint: "t", run: cycleTheme }, { label: "Show keyboard shortcuts", ico: "?", hint: "?", run: openHelp },
    { label: "Focus the chat box", ico: "/", hint: "/", run: () => { showTab("analyst"); $("#chatInput").focus(); } }];
  const dyn = [], t = q.trim();
  if (t && SYMBOLISH.test(t)) {
    const s = t.toUpperCase();
    dyn.push({ label: `Chart ${s}`, ico: "▲", hint: "live quote + history", run: () => openStockDrawer(s) });
    if (/^[A-Z]{1,5}(-[A-Z])?$/.test(s)) dyn.push({ label: `Add ${s} to watchlist`, ico: "+", hint: "", run: () => addWatch(s) });
  }
  if (t.length >= 3) {
    dyn.push({ label: `Search FRED for “${t}”`, ico: "⌕", hint: "", run: () => { showTab("macro"); const i = $("#fredSearch"); i.value = t; i.focus(); i.dispatchEvent(new Event("input")); } });
    if (/\s/.test(t) || t.endsWith("?")) dyn.push({ label: `Ask the analyst: ${t}`, ico: "✦", hint: "uses your Claude plan", run: () => ask(t) });
  }
  const words = t.toLowerCase().split(/\s+/).filter(Boolean);
  const matched = words.length ? base.filter((c) => words.every((w) => c.label.toLowerCase().includes(w))) : base;
  return [...dyn, ...matched];
}
function openPalette() {
  closeOverlay();
  const scrim = h("div", { class: "scrim", onclick: closeOverlay }), input = h("input", { class: "big", placeholder: "Type a command, a ticker (NVDA), a question, or a FRED topic…", "aria-label": "Command palette", autocomplete: "off" });
  const list = h("div", { class: "cmd-list", role: "listbox" });
  let items = [], sel = 0;
  const paint = () => $$(".cmd", list).forEach((b, i) => { b.classList.toggle("sel", i === sel); if (i === sel) b.scrollIntoView({ block: "nearest" }); });
  const run = (c) => { closeOverlay(); c.run(); };
  const render = () => {
    items = commandList(input.value); sel = Math.min(sel, Math.max(0, items.length - 1)); clear(list);
    if (!items.length) list.append(h("div", { class: "cmd-empty", text: "No matches" }));
    items.forEach((c, i) => list.append(h("button", { class: "cmd", role: "option", onclick: () => run(c), onmousemove: () => { sel = i; paint(); } }, h("span", { class: "ico", text: c.ico }), c.label, h("small", { text: c.hint }))));
    paint();
  };
  input.addEventListener("input", () => { sel = 0; render(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(items.length - 1, sel + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === "Enter" && items[sel]) { e.preventDefault(); run(items[sel]); }
  });
  overlay = [scrim, h("div", { class: "modal", role: "dialog", "aria-label": "Command palette" }, input, list, h("div", { class: "hint" }, h("span", { text: "↑↓ navigate" }), h("span", { text: "↵ select" }), h("span", { text: "esc close" })))];
  document.body.append(...overlay); render(); input.focus();
}
$("#paletteBtn").addEventListener("click", openPalette);
$("#themeBtn").addEventListener("click", cycleTheme);

document.addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); overlay ? closeOverlay() : openPalette(); return; }
  if (e.key === "Escape") { if (closeOverlay()) return; if ($(".drawer")) { closeDrawer(); return; } return; }
  if (typing(e.target) || mod || e.altKey) return;
  if (e.key >= "1" && e.key <= "6") { showTab(TABS[+e.key - 1]); return; }
  switch (e.key) {
    case "/": e.preventDefault(); showTab("analyst"); $("#chatInput").focus(); break;
    case "?": e.preventDefault(); openHelp(); break;
    case "r": refreshCurrent(); break;
    case "n": newChat(); break;
    case "t": cycleTheme(); break;
  }
});

/* ------------------------------------------------------------------ system */
async function loadSystem() {
  renderShortcuts(); await refreshStatus(); if (!status) return;
  const kv = (obj) => h("div", { class: "kv" }, Object.entries(obj).flatMap(([k, v]) => [h("span", { text: k }), h("span", { text: v })]));
  const k = status.kronos || {};
  clear($("#kronosBox")).append(h("p", {}, h("span", { class: "tag " + (k.status === "EARNED_TRUST" ? "good" : "warn"), text: k.status || "UNVALIDATED" })),
    h("p", { class: "muted", text: k.verdict_reason || k.note || "" }),
    ...(k.per_run || []).map((r) => h("div", { class: "muted small", text: `${r.symbols} names · ${r.origins} origins · IC ${r.mean_ic} (t ${r.t})` })),
    h("p", { class: "muted small", style: "margin-top:8px", text: "Treated as optional context, never as a signal, until it earns trust." }));
  clear($("#serverBox")).append(h("div", {}, status.servers.flatMap((s) => [h("span", { class: "tag good", text: s }), " "])));
  clear($("#keyBox")).append(kv({ "Missing keys": status.keys_missing.length ? status.keys_missing.join(", ") : "none", "Screener universe": status.universe_refreshed || "never loaded", "Model job running": status.busy ? "yes" : "no" }));
}

/* ------------------------------------------------------------------ boot */
applyTheme(store.get("theme", "auto"));
$$("#tabs button").forEach((b, i) => { b.title = `${b.textContent.replace(/\d$/, "")} (${i + 1})`; });
renderChips(); refreshStatus(); setInterval(refreshStatus, 30000); pollLive();
api("/api/watchlist").then((w) => { watchState.symbols = w.symbols; renderChips(); }).catch(() => {});
showTab(location.hash.slice(1) || "analyst", true);
