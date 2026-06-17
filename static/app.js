/* lifeboard SPA — vanilla JS, hash-routed, zero dependencies */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (t, p = {}, ...kids) => {
  const n = document.createElement(t);
  for (const [k, v] of Object.entries(p)) {
    if (k === "class") n.className = v;
    else if (k === "html") n.innerHTML = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null) continue;
    n.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return n;
};

const api = {
  async get(u) { return (await fetch(u)).json(); },
  async send(m, u, body) {
    const r = await fetch(u, { method: m, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
    if (!r.ok) throw new Error(await r.text());
    return r.status === 204 ? null : r.json();
  },
  post(u, b) { return this.send("POST", u, b); },
  patch(u, b) { return this.send("PATCH", u, b); },
  put(u, b) { return this.send("PUT", u, b); },
  del(u) { return this.send("DELETE", u); },
};

function toast(msg) {
  const t = el("div", { class: "toast" }, msg);
  document.body.append(t);
  setTimeout(() => t.remove(), 2200);
}

const todayStr = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD local

/* tiny, safe-ish markdown renderer (headings, bold, italic, code, lists, quotes, links) */
function md(src) {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const lines = esc(src || "").split("\n");
  let out = "", inUl = false, inOl = false, inPre = false;
  const closeLists = () => { if (inUl) { out += "</ul>"; inUl = false; } if (inOl) { out += "</ol>"; inOl = false; } };
  const inline = (t) => t
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>")
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  for (let line of lines) {
    if (/^```/.test(line)) { if (inPre) { out += "</code></pre>"; inPre = false; } else { closeLists(); out += "<pre><code>"; inPre = true; } continue; }
    if (inPre) { out += line + "\n"; continue; }
    let m;
    if ((m = line.match(/^(#{1,3})\s+(.*)/))) { closeLists(); out += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; }
    else if ((m = line.match(/^>\s?(.*)/))) { closeLists(); out += `<blockquote>${inline(m[1])}</blockquote>`; }
    else if ((m = line.match(/^[-*]\s+(.*)/))) { if (!inUl) { closeLists(); out += "<ul>"; inUl = true; } out += `<li>${inline(m[1])}</li>`; }
    else if ((m = line.match(/^\d+\.\s+(.*)/))) { if (!inOl) { closeLists(); out += "<ol>"; inOl = true; } out += `<li>${inline(m[1])}</li>`; }
    else if (line.trim() === "") { closeLists(); }
    else { closeLists(); out += `<p>${inline(line)}</p>`; }
  }
  closeLists(); if (inPre) out += "</code></pre>";
  return out;
}

/* ---- heatmap: render last `weeks` weeks ending today ---- */
function heatmap(activity, weeks = 26) {
  const wrap = el("div", { class: "heatmap" });
  const today = new Date();
  const end = new Date(today); end.setHours(0, 0, 0, 0);
  // align to end-of-week (Sat)
  const start = new Date(end);
  start.setDate(start.getDate() - (weeks * 7) - end.getDay());
  const maxV = Math.max(1, ...Object.values(activity));
  let d = new Date(start);
  for (let w = 0; w <= weeks; w++) {
    const col = el("div", { class: "heatcol" });
    for (let day = 0; day < 7; day++) {
      const ds = d.toLocaleDateString("en-CA");
      const future = d > today;
      const v = activity[ds] || 0;
      let lvl = 0;
      if (v > 0) lvl = Math.min(4, Math.ceil((v / maxV) * 4));
      const cls = future ? "heatcell future" : "heatcell" + (lvl ? " l" + lvl : "");
      col.append(el("div", { class: cls, title: future ? "" : `${ds}: ${v || 0}` }));
      d.setDate(d.getDate() + 1);
    }
    wrap.append(col);
  }
  return wrap;
}

function sparkline(values) {
  const w = 260, h = 40, p = 3;
  if (!values.length) return el("div", { class: "faint" }, "no data yet");
  const xs = values.map((_, i) => i), ys = values.map(v => v.value);
  const min = Math.min(...ys), max = Math.max(...ys), span = (max - min) || 1;
  const pts = values.map((v, i) => {
    const x = p + (i / Math.max(1, values.length - 1)) * (w - 2 * p);
    const y = h - p - ((v.value - min) / span) * (h - 2 * p);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
  const svg = el("svg", { class: "spark", viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none" });
  svg.innerHTML = `<polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="1.5" />`;
  return svg;
}

/* =====================================================================
   ROUTER
   ===================================================================== */
const state = { tabs: [] };

const DEFAULT_ACCENTS = [
  ["", "phosphor (default)"], ["#7ee787", "lime"], ["#e3b341", "amber"],
  ["#6cb6ff", "blue"], ["#56d4dd", "cyan"], ["#f778ba", "magenta"],
  ["#f47067", "red"], ["#d2a8ff", "violet"], ["#ff9e64", "orange"],
];

function applyAccent(color) {
  if (color) document.documentElement.style.setProperty("--accent", color);
  else document.documentElement.style.removeProperty("--accent");
  try { color ? localStorage.setItem("accent", color) : localStorage.removeItem("accent"); } catch (e) {}
  state.accent = color || "";
}

async function boot() {
  applyTheme(localStorage.getItem("theme") || "dark");
  applyAccent(localStorage.getItem("accent") || "");   // instant from cache
  state.tabs = await api.get("/api/tabs");
  renderChrome();
  window.addEventListener("hashchange", route);
  route();
  // sync accent from server (cross-device) without blocking first paint
  api.get("/api/appearance").then(a => { if ((a.accent || "") !== (state.accent || "")) applyAccent(a.accent || ""); }).catch(() => {});
}

function applyTheme(t) {
  document.documentElement.setAttribute("data-theme", t);
  try { localStorage.setItem("theme", t); } catch (e) {}
}

function renderChrome() {
  const root = $("#root");
  root.innerHTML = "";
  const header = el("header", { class: "top" },
    el("div", { class: "brand" },
      el("span", { class: "prompt" }, "lifeboard@home"),
      el("span", {}, ":~$"),
      el("span", { class: "blink" }, "\u2588"),
      el("span", { class: "spacer" }),
      el("button", { class: "btn-ghost btn-sm", title: "Toggle theme", onclick: () => applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark") }, "◐"),
      el("button", { class: "btn-ghost btn-sm", onclick: () => location.hash = "#/settings" }, "settings"),
    ),
    tabbar(),
  );
  root.append(header, el("main", { class: "app", id: "view" }));
}

function tabbar() {
  const bar = el("div", { class: "tabbar" });
  const cur = location.hash || "#/dashboard";
  const mk = (href, label) => el("a", { class: "tab" + (cur.startsWith(href) ? " active" : ""), href }, label);
  bar.append(mk("#/dashboard", "dashboard"));
  bar.append(mk("#/today", "today"));
  bar.append(mk("#/review", "review"));
  for (const t of state.tabs) bar.append(mk("#/tab/" + t.id, t.name));
  bar.append(mk("#/notes", "notes"));
  bar.append(mk("#/journal", "journal"));
  bar.append(el("a", { class: "tab add", href: "#", onclick: (e) => { e.preventDefault(); newTab(); } }, "+ goal"));
  return bar;
}

async function newTab() {
  const tpls = await api.get("/api/templates");
  const nameInput = el("input", { placeholder: "Goal name (e.g. Gym)", style: "width:100%" });
  let picked = "blank";
  const cards = el("div", { class: "type-grid" });
  const opts = [{ id: "blank", name: "Blank", desc: "Start empty and add your own widgets." }, ...tpls];
  for (const o of opts) {
    const card = el("div", { class: "type-card" + (o.id === "blank" ? " sel" : "") },
      el("div", { class: "n" }, o.name),
      el("div", { class: "d" }, o.desc),
      o.widgets ? el("div", { class: "faint", style: "font-size:11px;margin-top:4px" }, o.widgets.join(" · ")) : null);
    card.onclick = () => { picked = o.id; $$(".type-card", cards).forEach(x => x.classList.remove("sel")); card.classList.add("sel"); if (!nameInput.value) nameInput.value = o.id === "blank" ? "" : o.name; };
    cards.append(card);
  }
  const create = async () => {
    let t;
    if (picked === "blank") t = await api.post("/api/tabs", { name: nameInput.value || "Untitled" });
    else t = await api.post("/api/tabs/from_template", { template_id: picked, name: nameInput.value || undefined });
    state.tabs.push(t); closeModal(); renderChrome(); location.hash = "#/tab/" + t.id;
  };
  openModal("New goal", el("div", {},
    field("Name", nameInput),
    el("label", { class: "faint", style: "font-size:12px" }, "Start from"),
    cards,
    el("div", { class: "row", style: "justify-content:flex-end;margin-top:16px" },
      el("button", { class: "btn-ghost btn-sm", onclick: closeModal }, "cancel"),
      el("button", { class: "btn-accent btn-sm", onclick: create }, "create goal"))));
}

function route() {
  renderChrome();
  const h = location.hash || "#/dashboard";
  const view = $("#view");
  view.innerHTML = "";
  const m = h.match(/^#\/tab\/(\d+)/);
  if (h.startsWith("#/dashboard") || h === "#/" || h === "") return viewDashboard(view);
  if (h.startsWith("#/today")) return viewToday(view);
  if (h.startsWith("#/review")) return viewReview(view);
  if (m) return viewTab(view, +m[1]);
  if (h.startsWith("#/notes")) return viewEntries(view, "note");
  if (h.startsWith("#/journal")) return viewJournal(view);
  if (h.startsWith("#/settings")) return viewSettings(view);
  viewDashboard(view);
}

/* =====================================================================
   DASHBOARD
   ===================================================================== */
async function viewDashboard(v) {
  const d = await api.get("/api/dashboard");
  const pct = d.today_total ? Math.round((d.today_done / d.today_total) * 100) : 0;
  v.append(
    el("div", { class: "panel", style: "margin-bottom:14px" },
      el("div", { class: "stats" },
        stat("today", `${d.today_done}/${d.today_total}`, pct + "% done"),
        stat("current streak", d.current_streak + "d", "consecutive active days"),
        stat("longest streak", d.longest_streak + "d", "all-time best"),
      ),
    ),
    el("div", { class: "panel", style: "margin-bottom:14px" },
      el("h3", {}, "activity \u2014 last 26 weeks"),
      heatmap(d.activity, 26),
      el("div", { class: "heat-legend", style: "margin-top:8px" }, "less",
        ...["", "l1", "l2", "l3", "l4"].map(c => el("div", { class: "heatcell " + c })), "more"),
    ),
  );
  if (d.behind.length) {
    const p = el("div", { class: "panel" }, el("h3", {}, "needs attention"));
    for (const b of d.behind) {
      p.append(el("div", { class: "between", style: "padding:6px 0;border-top:1px solid var(--border)" },
        el("span", {}, b.title),
        el("span", { class: "amber" }, b.pace.required_per_day != null
          ? `need ${fmt(b.pace.required_per_day)} ${b.pace.unit || ""}/day` : "behind pace")));
    }
    v.append(p);
  }
  v.append(el("div", { class: "between", style: "margin-top:16px" },
    el("span", { class: "faint" }, "tip: build goal tabs with “+ goal”, then add widgets"),
    el("a", { href: "#/today", class: "btn-accent btn-sm", style: "padding:6px 12px;border-radius:4px" }, "do today’s check-in →")));
}

function stat(k, vv, sub) {
  return el("div", { class: "stat" },
    el("div", { class: "k" }, k),
    el("div", { class: "v" }, vv),
    sub ? el("div", { class: "faint", style: "font-size:11px" }, sub) : null);
}
const fmt = (n) => (Math.round(n * 100) / 100).toString();

/* =====================================================================
   REVIEW + TRENDS
   ===================================================================== */
function lineChart(series, opts = {}) {
  const w = 520, h = 90, pad = 6;
  if (!series.length) return el("div", { class: "faint" }, "no data in this period");
  const ys = series.map(s => s.value);
  const min = Math.min(...ys, opts.min ?? Infinity), max = Math.max(...ys, opts.max ?? -Infinity);
  const span = (max - min) || 1;
  const x = (i) => pad + (i / Math.max(1, series.length - 1)) * (w - 2 * pad);
  const y = (v) => h - pad - ((v - min) / span) * (h - 2 * pad);
  const line = series.map((s, i) => `${x(i).toFixed(1)},${y(s.value).toFixed(1)}`).join(" ");
  const area = `${pad},${h - pad} ${line} ${x(series.length - 1).toFixed(1)},${h - pad}`;
  const svg = el("svg", { class: "spark", viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", style: "height:90px" });
  svg.innerHTML =
    `<polygon points="${area}" fill="var(--accent)" opacity="0.10" />` +
    `<polyline points="${line}" fill="none" stroke="var(--accent)" stroke-width="1.5" />` +
    series.map((s, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(s.value).toFixed(1)}" r="1.6" fill="var(--accent)"><title>${s.day}: ${fmt(s.value)}</title></circle>`).join("");
  return svg;
}

function barRow(series) {
  const wrap = el("div", { class: "row", style: "gap:2px;align-items:flex-end;height:46px" });
  const max = Math.max(1, ...series.map(s => s.rate));
  for (const s of series) {
    const hh = Math.max(2, (s.rate / max) * 44);
    wrap.append(el("div", { title: `${s.day}: ${s.rate}%`, style: `flex:1;height:${hh}px;border-radius:2px;background:${s.rate >= 80 ? "var(--accent)" : s.rate >= 40 ? "var(--accent-dim)" : "var(--cell-1)"}` }));
  }
  return wrap;
}

async function viewReview(v) {
  let period = state.reviewPeriod || "week";
  const box = el("div", {});
  async function load() {
    state.reviewPeriod = period;
    const d = await api.get("/api/review?period=" + period);
    box.innerHTML = "";
    box.append(
      el("div", { class: "between", style: "margin-bottom:14px" },
        el("h3", {}, "review"),
        el("div", { class: "seg" },
          ...[["week", "7 days"], ["month", "30 days"], ["year", "365 days"]].map(([k, l]) =>
            el("button", { class: period === k ? "active" : "", onclick: () => { period = k; load(); } }, l)))),
      el("div", { class: "panel", style: "margin-bottom:14px" },
        el("div", { class: "stats" },
          stat("habit completion", d.overall_rate + "%", `over ${d.days} days`),
          stat("habits checked", d.habit_completions, "total ticks"),
          stat("window", d.start.slice(5) + " → " + d.end.slice(5), "")),
        el("div", { style: "margin-top:12px" },
          el("div", { class: "faint", style: "font-size:11px;margin-bottom:4px" }, "daily completion"),
          barRow(d.daily))));
    if (!d.items.length) { box.append(el("div", { class: "empty" }, "Nothing to review yet — log some habits and metrics.")); return; }
    const grid = el("div", { class: "grid" });
    for (const it of d.items) grid.append(reviewCard(it));
    box.append(grid);
  }
  v.append(box); load();
}

function reviewCard(it) {
  const body = [];
  if (it.type === "habit") {
    body.push(el("div", { class: "stats", style: "margin-bottom:8px" },
      stat("done", it.done), stat("rate", it.rate + "%")));
    body.push(barRow(it.series.map(s => ({ day: s.day, rate: s.value ? 100 : 0 }))));
  } else {
    body.push(el("div", { class: "stats", style: "margin-bottom:8px" },
      stat("avg", fmt(it.avg) + (it.unit ? " " + it.unit : "")),
      stat("best", fmt(it.best)),
      !["number", "progress"].includes(it.type) ? stat("total", fmt(it.total)) : null));
    body.push(lineChart(it.series));
  }
  return el("div", { class: "panel widget" },
    el("div", { class: "whead" }, el("span", { class: "title" }, it.title),
      el("span", { class: "wtype" }, it.tab)),
    ...body);
}

/* =====================================================================
   TODAY CHECK-IN
   ===================================================================== */
async function viewToday(v) {
  const d = await api.get("/api/today");
  v.append(el("div", { class: "between", style: "margin-bottom:14px" },
    el("h3", {}, "today \u2014 " + d.day),
    el("span", { class: "faint" }, d.items.length + " trackable items")));
  if (!d.items.length) { v.append(el("div", { class: "empty" }, "No trackable widgets yet. Add habits, counters or metrics to a goal tab.")); return; }
  const list = el("div", { class: "stack" });
  for (const it of d.items) list.append(todayRow(it));
  v.append(el("div", { class: "panel" }, list));
}

function todayRow(it) {
  const row = el("div", { class: "between", style: "padding:8px 0;border-top:1px solid var(--border)" });
  const left = el("div", {}, el("div", {}, it.title),
    el("div", { class: "faint", style: "font-size:11px" }, it.tab_name + " · " + it.type));
  const right = el("div", { class: "row" });
  const save = async (val) => { await api.put(`/api/widgets/${it.id}/log`, { value: val }); toast("logged"); };
  if (it.type === "habit") {
    const on = !!it.today_value;
    const b = el("button", { class: "btn-sm" + (on ? " btn-accent" : "") }, on ? "✓ done" : "mark done");
    b.onclick = async () => { const nv = on ? 0 : 1; await save(nv); viewToday($("#view").replaceChildren() || $("#view")); route(); };
    right.append(b);
  } else {
    const inp = el("input", { type: "number", step: "any", value: it.today_value ?? "", style: "width:110px", placeholder: it.config.unit || "value" });
    const b = el("button", { class: "btn-accent btn-sm" }, "save");
    b.onclick = () => save(parseFloat(inp.value || "0"));
    inp.addEventListener("keydown", e => { if (e.key === "Enter") b.click(); });
    right.append(inp, it.config.unit ? el("span", { class: "faint" }, it.config.unit) : null, b);
  }
  row.append(left, right);
  return row;
}

/* =====================================================================
   GOAL TAB + WIDGETS
   ===================================================================== */
let editMode = false;

async function viewTab(v, tabId) {
  const tab = state.tabs.find(t => t.id === tabId);
  if (!tab) { location.hash = "#/dashboard"; return; }
  const widgets = await api.get(`/api/tabs/${tabId}/widgets`);
  const renameInput = el("input", { value: tab.name, style: "font-size:15px;font-weight:600;max-width:240px" });
  const head = el("div", { class: "between", style: "margin-bottom:14px" },
    editMode
      ? el("div", { class: "row" }, renameInput, el("button", { class: "btn-sm", onclick: async () => { await api.patch("/api/tabs/" + tabId, { name: renameInput.value }); tab.name = renameInput.value; renderChrome(); } }, "rename"))
      : el("h3", {}, tab.name),
    el("div", { class: "row" },
      el("button", { class: "btn-sm" + (editMode ? " btn-accent" : ""), onclick: () => { editMode = !editMode; route(); } }, editMode ? "✓ done" : "edit"),
      el("button", { class: "btn-accent btn-sm", onclick: () => addWidgetModal(tabId) }, "+ widget"),
      editMode ? el("button", { class: "btn-ghost btn-sm btn-danger", onclick: () => delTab(tabId) }, "delete tab") : null));
  v.append(head);
  if (editMode) v.append(el("div", { class: "faint", style: "margin-bottom:10px;font-size:12px" }, "drag the ⠿ handle to reorder · tap a title to rename a widget · gear to edit settings"));
  if (!widgets.length) { v.append(el("div", { class: "empty" }, "Empty tab. Add a widget — or delete the tab and pick a template.")); return; }
  const grid = el("div", { class: "grid" });
  for (const w of widgets) grid.append(renderWidget(w, tabId));
  v.append(grid);
  if (editMode) enableDragReorder(grid, tabId);
}

function enableDragReorder(grid, tabId) {
  let dragEl = null;
  $$(".widget", grid).forEach(card => {
    card.setAttribute("draggable", "true");
    card.addEventListener("dragstart", (e) => { dragEl = card; card.style.opacity = "0.4"; e.dataTransfer.effectAllowed = "move"; });
    card.addEventListener("dragend", async () => {
      card.style.opacity = "";
      const order = $$(".widget", grid).map(c => +c.dataset.wid);
      await api.patch(`/api/tabs/${tabId}/reorder`, { order });
    });
    card.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (!dragEl || dragEl === card) return;
      const rect = card.getBoundingClientRect();
      const after = (e.clientY - rect.top) / rect.height > 0.5;
      grid.insertBefore(dragEl, after ? card.nextSibling : card);
    });
  });
}

async function delTab(id) {
  if (!confirm("Delete this tab and all its widgets/logs?")) return;
  await api.del("/api/tabs/" + id);
  state.tabs = state.tabs.filter(t => t.id !== id);
  editMode = false;
  location.hash = "#/dashboard";
}

function widgetShell(w, tabId, body) {
  const head = el("div", { class: "whead" },
    editMode ? el("span", { class: "drag", title: "drag to reorder", style: "cursor:grab;color:var(--fg-faint)" }, "⠿") : null,
    el("span", { class: "title", onclick: editMode ? async () => {
        const nt = prompt("Widget title:", w.title); if (nt && nt !== w.title) { await api.patch("/api/widgets/" + w.id, { title: nt }); route(); }
      } : null, style: editMode ? "cursor:text;border-bottom:1px dashed var(--border-2)" : "" }, w.title),
    el("span", { class: "wtype" }, w.type),
    editMode && ["progress", "counter", "number"].includes(w.type)
      ? el("button", { class: "btn-ghost btn-sm", title: "settings", onclick: () => editWidgetConfig(w) }, "⚙") : null,
    el("button", { class: "btn-ghost btn-sm", title: "delete", onclick: async () => { if (confirm("Delete widget?")) { await api.del("/api/widgets/" + w.id); route(); } } }, "✕"));
  return el("div", { class: "panel widget", "data-wid": w.id }, head, body);
}

function editWidgetConfig(w) {
  const cfg = { ...w.config };
  const fields = [];
  const g = {};
  let milestones = Array.isArray(cfg.milestones) ? cfg.milestones.map(m => ({ ...m })) : [];
  const addF = (key, label, attrs = {}) => { const i = el("input", { value: cfg[key] ?? "", ...attrs }); g[key] = i; fields.push(field(label, i)); };
  if (w.type === "counter") { addF("daily_target", "Daily target", { type: "number", step: "any" }); addF("unit", "Unit"); }
  if (w.type === "number") { addF("unit", "Unit"); }
  let msBox = null;
  if (w.type === "progress") {
    const mode = select("e-mode", [["cumulative", "Cumulative (sum up)"], ["metric", "Metric (reach a value)"]]);
    mode.value = cfg.goal_mode || "cumulative"; g.goal_mode = mode; fields.push(field("Mode", mode));
    addF("unit", "Unit"); addF("start_value", "Start value", { type: "number", step: "any" });
    addF("target", "Target", { type: "number", step: "any" });
    addF("start_date", "Start date", { type: "date" }); addF("end_date", "End date (optional)", { type: "date" });
    // sub-goals / milestones editor
    msBox = el("div", { class: "stack" });
    const drawMs = () => {
      msBox.innerHTML = "";
      milestones.forEach((m, idx) => {
        const lbl = el("input", { value: m.label || "", placeholder: "label (e.g. halfway)", style: "flex:2" });
        const at = el("input", { value: m.at ?? "", type: "number", step: "any", placeholder: "value", style: "flex:1" });
        lbl.oninput = () => milestones[idx].label = lbl.value;
        at.oninput = () => milestones[idx].at = at.value;
        msBox.append(el("div", { class: "row" }, lbl, at,
          el("button", { class: "btn-ghost btn-sm btn-danger", onclick: () => { milestones.splice(idx, 1); drawMs(); } }, "✕")));
      });
      msBox.append(el("button", { class: "btn-sm", onclick: () => { milestones.push({ label: "", at: "" }); drawMs(); } }, "+ milestone"));
    };
    drawMs();
    fields.push(el("div", { class: "field" },
      el("label", {}, "Sub-goals / milestones"),
      el("div", { class: "faint", style: "font-size:11px;margin-bottom:6px" }, "checkpoints on the way to the target; the next one gets an ETA from your pace"),
      msBox));
  }
  const save = async () => {
    const out = {};
    for (const [k, i] of Object.entries(g)) {
      let val = i.value;
      if (["daily_target", "start_value", "target"].includes(k)) val = val === "" ? undefined : +val;
      if (val !== undefined && val !== "") out[k] = val;
    }
    const cleanMs = milestones.filter(m => m.at !== "" && m.at != null && !isNaN(+m.at))
      .map(m => ({ label: (m.label || "").trim(), at: +m.at }));
    if (cleanMs.length) out.milestones = cleanMs;
    await api.patch("/api/widgets/" + w.id, { config: out }); closeModal(); route();
  };
  openModal("Edit " + w.type, el("div", {}, ...fields,
    el("div", { class: "row", style: "justify-content:flex-end;margin-top:14px" },
      el("button", { class: "btn-ghost btn-sm", onclick: closeModal }, "cancel"),
      el("button", { class: "btn-accent btn-sm", onclick: save }, "save"))));
}

function renderWidget(w, tabId) {
  switch (w.type) {
    case "habit": return habitWidget(w, tabId);
    case "counter": return counterWidget(w, tabId);
    case "number": return numberWidget(w, tabId);
    case "progress": return progressWidget(w, tabId);
    case "todo": return todoWidget(w, tabId);
    case "note": return noteWidget(w, tabId);
    case "timer": return timerWidget(w, tabId);
    default: return widgetShell(w, tabId, el("div", { class: "faint" }, "unknown widget"));
  }
}

const activityFromLogs = (logs) => Object.fromEntries(logs.filter(l => l.value).map(l => [l.day, l.value]));

function habitWidget(w, tabId) {
  const on = w.logs.some(l => l.day === todayStr() && l.value);
  const body = el("div", {},
    el("div", { class: "stats", style: "margin-bottom:10px" },
      stat("streak", (w.streak.current) + "d"),
      stat("best", (w.streak.longest) + "d"),
      stat("total", w.streak.total)),
    el("button", { class: "habit-toggle" + (on ? " on" : ""), onclick: async () => {
        await api.put(`/api/widgets/${w.id}/log`, { value: on ? 0 : 1 }); route();
      } }, on ? "✓ done today" : "mark done today"),
    el("div", { style: "margin-top:12px" }, heatmap(activityFromLogs(w.logs), 20)));
  return widgetShell(w, tabId, body);
}

function counterWidget(w, tabId) {
  const t = w.logs.find(l => l.day === todayStr());
  let val = t ? t.value : 0;
  const target = +(w.config.daily_target || 0);
  const valEl = el("div", { class: "big" }, fmt(val));
  const set = async (nv) => { nv = Math.max(0, nv); await api.put(`/api/widgets/${w.id}/log`, { value: nv }); val = nv; valEl.textContent = fmt(nv); if (bar) bar.firstChild.style.width = Math.min(100, target ? nv / target * 100 : 0) + "%"; };
  const bar = target ? el("div", { class: "bar" }, el("span", { style: "width:" + Math.min(100, val / target * 100) + "%" })) : null;
  const body = el("div", {},
    el("div", { class: "between" }, valEl, el("div", { class: "row" },
      el("button", { class: "btn-sm", onclick: () => set(val - 1) }, "\u2212"),
      el("button", { class: "btn-accent btn-sm", onclick: () => set(val + 1) }, "+"))),
    target ? el("div", { class: "faint", style: "margin:6px 0 4px" }, `target ${target} ${w.config.unit || ""}/day`) : null,
    bar,
    el("div", { style: "margin-top:10px" }, sparkline(w.logs.slice(-30))));
  return widgetShell(w, tabId, body);
}

function numberWidget(w, tabId) {
  const t = w.logs.find(l => l.day === todayStr());
  const last = w.logs.length ? w.logs[w.logs.length - 1].value : null;
  const inp = el("input", { type: "number", step: "any", value: t ? t.value : "", placeholder: w.config.unit || "value", style: "width:120px" });
  const body = el("div", {},
    el("div", { class: "between" },
      el("div", {}, el("span", { class: "big" }, last != null ? fmt(last) : "—"), w.config.unit ? el("span", { class: "faint" }, " " + w.config.unit) : null),
      el("div", { class: "row" }, inp,
        el("button", { class: "btn-accent btn-sm", onclick: async () => { await api.put(`/api/widgets/${w.id}/log`, { value: parseFloat(inp.value || "0") }); route(); } }, "log"))),
    el("div", { style: "margin-top:10px" }, sparkline(w.logs.slice(-45))));
  return widgetShell(w, tabId, body);
}

function progressWidget(w, tabId) {
  const p = w.pace || {};
  const statusCls = p.status || "open";
  const isMetric = p.mode === "metric";
  const cur = isMetric ? p.current : p.current;
  const ms = p.milestones || [];
  const track = el("div", { class: "bartrack", style: "margin:10px 0" },
    el("div", { class: "bar" + (statusCls === "behind" ? " amber" : "") },
      el("span", { style: "width:" + Math.min(100, p.percent || 0) + "%" })));
  for (const m of ms) track.append(el("div", { class: "tick" + (m.reached ? " hit" : ""), style: "left:" + m.pos + "%", title: (m.label || "") + " · " + fmt(m.at) + (p.unit ? " " + p.unit : "") }));
  const body = el("div", {},
    el("div", { class: "between" },
      el("div", {}, el("span", { class: "big" }, fmt(cur ?? 0)),
        el("span", { class: "faint" }, ` / ${fmt(p.target ?? 0)} ${p.unit || ""}`)),
      el("span", { class: "pill " + statusCls }, statusCls)),
    track,
    el("div", { class: "faint", style: "font-size:12px" },
      p.required_per_day != null
        ? `need ${fmt(p.required_per_day)} ${p.unit || ""}/day · ${p.days_left} days left`
        : (p.projected_date ? `projected: ${p.projected_date}` : "log progress to see pace")),
    p.observed_per_day != null ? el("div", { class: "faint", style: "font-size:12px" }, `current pace ${fmt(p.observed_per_day)}/day`) : null,
    ms.length ? milestoneList(ms, p.unit) : null,
    el("div", { class: "row", style: "margin-top:10px" },
      (() => { const i = el("input", { type: "number", step: "any", placeholder: isMetric ? "today’s value" : "add amount", style: "flex:1" });
        return el("div", { class: "row", style: "width:100%" }, i,
          el("button", { class: "btn-accent btn-sm", onclick: async () => {
            let val = parseFloat(i.value || "0");
            await api.put(`/api/widgets/${w.id}/log`, { value: val }); route();
          } }, "log")); })()));
  return widgetShell(w, tabId, body);
}

function milestoneList(ms, unit) {
  const box = el("div", { class: "milestones" });
  for (const m of ms) {
    box.append(el("div", { class: "milestone" + (m.reached ? " hit" : "") + (m.next ? " next" : "") },
      el("span", { class: "mk" }, m.reached ? "✓" : (m.next ? "→" : "○")),
      el("span", { class: "ml" }, (m.label || ("at " + fmt(m.at))) + (m.label ? ` · ${fmt(m.at)}${unit ? " " + unit : ""}` : "")),
      m.next && m.eta ? el("span", { class: "me" }, "~" + m.eta) : (m.next ? el("span", { class: "me" }, "next") : null)));
  }
  return box;
}

function todoWidget(w, tabId) {
  const list = el("div", { class: "stack", style: "--space:4px" });
  const draw = () => {
    list.innerHTML = "";
    for (const t of w.todos) {
      const row = el("div", { class: "todo" + (t.done ? " done" : "") },
        el("input", { type: "checkbox", ...(t.done ? { checked: "" } : {}), onchange: async (e) => { await api.patch("/api/todos/" + t.id, { done: e.target.checked }); t.done = e.target.checked; row.classList.toggle("done", t.done); } }),
        el("span", {}, t.text),
        el("span", { class: "x", title: "delete", onclick: async () => { await api.del("/api/todos/" + t.id); w.todos = w.todos.filter(x => x.id !== t.id); draw(); } }, "✕"));
      list.append(row);
    }
  };
  draw();
  const inp = el("input", { placeholder: "add item…", style: "flex:1" });
  const add = async () => { if (!inp.value.trim()) return; const it = await api.post(`/api/widgets/${w.id}/todos`, { text: inp.value }); w.todos.push(it); inp.value = ""; draw(); };
  inp.addEventListener("keydown", e => { if (e.key === "Enter") add(); });
  return widgetShell(w, tabId, el("div", {}, list, el("div", { class: "row", style: "margin-top:8px" }, inp, el("button", { class: "btn-sm", onclick: add }, "+"))));
}

function noteWidget(w, tabId) {
  const view = el("div", { class: "md" });
  const render = () => view.innerHTML = md(w.config.text || "*(empty — click edit)*");
  render();
  const edit = el("button", { class: "btn-ghost btn-sm" }, "edit");
  edit.onclick = () => {
    const ta = el("textarea", {}, w.config.text || "");
    const save = el("button", { class: "btn-accent btn-sm", onclick: async () => { w.config.text = ta.value; await api.patch("/api/widgets/" + w.id, { config: w.config }); render(); body.replaceChildren(view, edit); } }, "save");
    body.replaceChildren(ta, el("div", { class: "row", style: "margin-top:8px" }, save));
  };
  const body = el("div", {}, view, edit);
  return widgetShell(w, tabId, body);
}

function timerWidget(w, tabId) {
  let start = null, elapsed = 0, iv = null;
  const disp = el("div", { class: "big" }, "00:00");
  const fmtT = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const tick = () => { disp.textContent = fmtT(elapsed + Math.floor((Date.now() - start) / 1000)); };
  const startBtn = el("button", { class: "btn-accent btn-sm" }, "start");
  startBtn.onclick = () => {
    if (iv) { clearInterval(iv); iv = null; elapsed += Math.floor((Date.now() - start) / 1000); startBtn.textContent = "start"; }
    else { start = Date.now(); iv = setInterval(tick, 1000); startBtn.textContent = "pause"; }
  };
  const logBtn = el("button", { class: "btn-sm", onclick: async () => {
    if (iv) { clearInterval(iv); iv = null; elapsed += Math.floor((Date.now() - start) / 1000); }
    const mins = Math.round(elapsed / 60);
    const prev = (w.logs.find(l => l.day === todayStr()) || {}).value || 0;
    await api.put(`/api/widgets/${w.id}/log`, { value: prev + mins });
    toast(`logged ${mins} min`); elapsed = 0; disp.textContent = "00:00"; startBtn.textContent = "start";
  } }, "log minutes");
  const todayMin = (w.logs.find(l => l.day === todayStr()) || {}).value || 0;
  return widgetShell(w, tabId, el("div", {}, disp,
    el("div", { class: "row", style: "margin-top:10px" }, startBtn, logBtn),
    el("div", { class: "faint", style: "margin-top:8px" }, `today: ${fmt(todayMin)} min logged`),
    el("div", { style: "margin-top:8px" }, sparkline(w.logs.slice(-30)))));
}

/* widget creation modal with templates ------------------------------- */
const WTYPES = [
  ["habit", "Habit", "Daily yes/no + streak & heatmap"],
  ["counter", "Counter", "Count up per day vs a target"],
  ["number", "Metric", "Log a value (weight, kcal) + trend"],
  ["progress", "Goal / pace", "Track toward a target by a date"],
  ["todo", "Checklist", "A simple to-do list"],
  ["note", "Note", "Pinned markdown text"],
  ["timer", "Timer", "Stopwatch that logs minutes"],
];

function addWidgetModal(tabId) {
  let type = "habit";
  const typeGrid = el("div", { class: "type-grid" });
  const cfgBox = el("div", { class: "stack" });
  const drawCfg = () => {
    cfgBox.innerHTML = "";
    cfgBox.append(field("Title", el("input", { id: "w-title", placeholder: WTYPES.find(t => t[0] === type)[1] })));
    if (type === "counter") {
      cfgBox.append(field("Daily target (optional)", el("input", { id: "w-target", type: "number", step: "any" })),
        field("Unit (optional)", el("input", { id: "w-unit", placeholder: "e.g. glasses" })));
    } else if (type === "number") {
      cfgBox.append(field("Unit (optional)", el("input", { id: "w-unit", placeholder: "e.g. kg" })));
    } else if (type === "progress") {
      cfgBox.append(
        rowFields(
          field("Mode", select("w-mode", [["cumulative", "Cumulative (sum up)"], ["metric", "Metric (reach a value)"]])),
          field("Unit", el("input", { id: "w-unit", placeholder: "km, £, kg…" }))),
        rowFields(
          field("Start value", el("input", { id: "w-start", type: "number", step: "any", value: "0" })),
          field("Target", el("input", { id: "w-targetv", type: "number", step: "any" }))),
        rowFields(
          field("Start date", el("input", { id: "w-sdate", type: "date", value: todayStr() })),
          field("End date (optional)", el("input", { id: "w-edate", type: "date" }))));
    }
  };
  for (const [k, n, d] of WTYPES) {
    const card = el("div", { class: "type-card" + (k === type ? " sel" : ""), onclick: () => { type = k; $$(".type-card", typeGrid).forEach(c => c.classList.remove("sel")); card.classList.add("sel"); drawCfg(); } },
      el("div", { class: "n" }, n), el("div", { class: "d" }, d));
    typeGrid.append(card);
  }
  drawCfg();
  const create = async () => {
    const cfg = {};
    const g = (id) => { const e = $("#" + id); return e ? e.value : ""; };
    if (type === "counter") { if (g("w-target")) cfg.daily_target = +g("w-target"); if (g("w-unit")) cfg.unit = g("w-unit"); }
    if (type === "number") { if (g("w-unit")) cfg.unit = g("w-unit"); }
    if (type === "progress") {
      cfg.goal_mode = g("w-mode"); cfg.unit = g("w-unit");
      cfg.start_value = +(g("w-start") || 0); cfg.target = +(g("w-targetv") || 0);
      cfg.start_date = g("w-sdate"); if (g("w-edate")) cfg.end_date = g("w-edate");
    }
    await api.post("/api/widgets", { tab_id: tabId, type, title: g("w-title"), config: cfg });
    closeModal(); route();
  };
  openModal("Add widget", el("div", {},
    typeGrid, el("hr", { class: "sep" }), cfgBox,
    el("div", { class: "row", style: "margin-top:16px;justify-content:flex-end" },
      el("button", { class: "btn-ghost btn-sm", onclick: closeModal }, "cancel"),
      el("button", { class: "btn-accent btn-sm", onclick: create }, "create widget"))));
}

const field = (label, inp) => el("div", { class: "field" }, el("label", {}, label), inp);
const rowFields = (...f) => el("div", { class: "field inline" }, ...f.map(x => el("div", {}, x)));
const select = (id, opts) => { const s = el("select", { id }); for (const [v, l] of opts) s.append(el("option", { value: v }, l)); return s; };

/* =====================================================================
   NOTES (second brain) + unified search
   ===================================================================== */
async function viewEntries(v, kindDefault) {
  let kind = kindDefault; // note | journal | all
  const results = el("div", { class: "entry-list" });
  const searchInput = el("input", { type: "search", placeholder: "fuzzy search notes & journal…" });
  const seg = el("div", { class: "seg" },
    segBtn("all", "all"), segBtn("note", "notes"), segBtn("journal", "journal"));
  function segBtn(k, label) {
    return el("button", { class: (k === kind ? "active" : ""), onclick: () => { kind = k; $$(".seg button", seg).forEach(b => b.classList.remove("active")); event.target.classList.add("active"); load(); } }, label);
  }
  async function load() {
    const q = searchInput.value.trim();
    let rows;
    if (q) rows = await api.get(`/api/search?q=${encodeURIComponent(q)}${kind !== "all" ? "&kind=" + kind : ""}`);
    else rows = await api.get(`/api/entries${kind !== "all" ? "?kind=" + kind : ""}`);
    results.innerHTML = "";
    if (!rows.length) { results.append(el("div", { class: "empty" }, q ? "No matches." : "Nothing here yet. Create or import a note.")); return; }
    for (const r of rows) results.append(entryRow(r));
  }
  searchInput.addEventListener("input", debounce(load, 180));
  const fileInput = el("input", { type: "file", accept: ".md,.markdown,.txt,text/*", multiple: "", style: "display:none", onchange: async (e) => {
    const fd = new FormData(); for (const f of e.target.files) fd.append("files", f);
    const r = await fetch("/api/import", { method: "POST", body: fd }); const j = await r.json();
    toast(`imported ${j.imported} file(s)`); load();
  } });
  v.append(
    el("div", { class: "between", style: "margin-bottom:12px" }, el("h3", {}, "second brain"),
      el("div", { class: "row" },
        el("button", { class: "btn-sm", onclick: () => fileInput.click() }, "import"),
        el("button", { class: "btn-accent btn-sm", onclick: () => editEntry(null, "note", load) }, "+ new note"),
        fileInput)),
    el("div", { class: "searchbar" }, searchInput, seg),
    results);
  load();
}

function entryRow(r) {
  return el("div", { class: "entry-row", onclick: () => openEntry(r.id) },
    el("div", { class: "between" },
      el("span", { class: "et" }, r.title || "(untitled)"),
      el("span", { class: "badge" }, r.kind + (r.slot ? "·" + r.slot : ""))),
    el("div", { class: "em snippet", html: r.snippet ? r.snippet.replace(/\u3008/g, "<u>").replace(/\u3009/g, "</u>") : (r.preview || "") }),
    el("div", { class: "faint", style: "font-size:11px;margin-top:3px" }, (r.entry_date || (r.updated_at || "").slice(0, 10))));
}

async function openEntry(id) {
  const e = await api.get("/api/entries/" + id);
  const view = el("div", { class: "md", html: md(e.body) });
  const relatedBox = el("div", { style: "margin-top:14px" });
  openModal(e.title || "(untitled)", el("div", {},
    e.entry_date ? el("div", { class: "faint", style: "margin-bottom:8px" }, e.entry_date + (e.slot ? " · " + e.slot : "")) : null,
    view,
    relatedBox,
    el("div", { class: "row", style: "justify-content:flex-end;margin-top:16px" },
      el("button", { class: "btn-ghost btn-sm btn-danger", onclick: async () => { if (confirm("Delete entry?")) { await api.del("/api/entries/" + id); closeModal(); route(); } } }, "delete"),
      el("button", { class: "btn-sm", onclick: () => { closeModal(); editEntry(e, e.kind, () => route()); } }, "edit"),
      el("button", { class: "btn-accent btn-sm", onclick: closeModal }, "close"))));
  // related notes (keyword similarity)
  api.get(`/api/entries/${id}/related`).then(rel => {
    if (!rel.length) return;
    relatedBox.append(el("hr", { class: "sep" }),
      el("div", { class: "faint", style: "font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px" }, "related"));
    for (const r of rel) {
      relatedBox.append(el("div", { class: "entry-row", style: "margin-bottom:4px", onclick: () => { closeModal(); openEntry(r.id); } },
        el("div", { class: "between" }, el("span", { class: "et" }, r.title || "(untitled)"),
          el("span", { class: "badge" }, r.kind))));
    }
  });
}

function editEntry(existing, kind, onsave) {
  const title = el("input", { placeholder: "title", value: existing?.title || "" });
  const body = el("textarea", { placeholder: "markdown supported…", style: "min-height:280px" }, existing?.body || "");
  const save = async () => {
    const payload = { kind, title: title.value, body: body.value, entry_date: existing?.entry_date || null, slot: existing?.slot || null };
    if (existing?.id) await api.patch("/api/entries/" + existing.id, payload);
    else await api.post("/api/entries", payload);
    closeModal(); onsave && onsave();
  };
  openModal(existing?.id ? "Edit note" : "New note", el("div", {},
    field("Title", title), field("Body", body),
    el("div", { class: "row", style: "justify-content:flex-end" },
      el("button", { class: "btn-ghost btn-sm", onclick: closeModal }, "cancel"),
      el("button", { class: "btn-accent btn-sm", onclick: save }, "save"))));
}

/* =====================================================================
   JOURNAL
   ===================================================================== */
async function viewJournal(v) {
  let slot = new Date().getHours() < 12 ? "am" : "pm";
  const box = el("div", {});
  const history = el("div", {});
  async function loadHistory(q = "") {
    const rows = q
      ? await api.get(`/api/search?q=${encodeURIComponent(q)}&kind=journal`)
      : await api.get("/api/entries?kind=journal");
    history.innerHTML = "";
    if (!rows.length) { history.append(el("div", { class: "faint", style: "padding:8px 0" }, q ? "No matching entries." : "No past entries yet.")); return; }
    for (const r of rows.slice(0, 40)) {
      history.append(el("div", { class: "entry-row", style: "margin-bottom:4px", onclick: () => openEntry(r.id) },
        el("div", { class: "between" },
          el("span", { class: "et" }, (r.entry_date || (r.updated_at || "").slice(0, 10)) + (r.slot ? " · " + r.slot : "")),
          el("span", { class: "badge" }, "journal")),
        el("div", { class: "em snippet", html: r.snippet ? r.snippet.replace(/\u3008/g, "<u>").replace(/\u3009/g, "</u>") : (r.preview || "") })));
    }
  }
  async function load() {
    const d = await api.get("/api/journal/today?slot=" + slot);
    box.innerHTML = "";
    const ta = el("textarea", { placeholder: "write…", style: "min-height:240px" }, d.entry?.body || "");
    const save = async () => {
      const payload = { kind: "journal", title: `Journal ${d.day} ${slot}`, body: ta.value, entry_date: d.day, slot };
      if (d.entry?.id) await api.patch("/api/entries/" + d.entry.id, payload);
      else await api.post("/api/entries", payload);
      toast("saved"); load(); loadHistory(searchInput.value.trim());
    };
    box.append(
      el("div", { class: "between", style: "margin-bottom:12px" },
        el("h3", {}, "journal \u2014 " + d.day),
        el("div", { class: "seg" },
          el("button", { class: slot === "am" ? "active" : "", onclick: () => { slot = "am"; load(); } }, "morning"),
          el("button", { class: slot === "pm" ? "active" : "", onclick: () => { slot = "pm"; load(); } }, "evening"))),
      el("div", { class: "panel", style: "margin-bottom:12px;border-left:2px solid var(--accent-dim)" },
        el("div", { class: "faint", style: "font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px" }, "prompt" + (d.prompt && d.prompt.weekdays ? " · scheduled" : "")),
        el("div", {}, d.prompt ? d.prompt.text : "Set prompts in settings.")),
      el("div", { class: "panel" }, ta,
        el("div", { class: "row", style: "justify-content:flex-end;margin-top:10px" },
          el("button", { class: "btn-accent btn-sm", onclick: save }, d.entry ? "update entry" : "save entry"))));
  }
  const searchInput = el("input", { type: "search", placeholder: "search journal history…" });
  searchInput.addEventListener("input", debounce(() => loadHistory(searchInput.value.trim()), 180));
  v.append(box,
    el("div", { class: "panel", style: "margin-top:14px" },
      el("div", { class: "between", style: "margin-bottom:10px" }, el("h3", { style: "margin:0" }, "history"), searchInput),
      history));
  load(); loadHistory();
}

/* =====================================================================
   SETTINGS (prompts + export/backup)
   ===================================================================== */
async function viewSettings(v) {
  const prompts = await api.get("/api/prompts");
  const reminders = await api.get("/api/reminders");
  const WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const list = el("div", { class: "stack" });
  const draw = () => {
    list.innerHTML = "";
    for (const p of prompts) {
      const sched = (p.weekdays || "").trim()
        ? p.weekdays.split(",").map(n => WD[+n]).join(" ")
        : "any day";
      list.append(el("div", { class: "between", style: "padding:6px 0;border-top:1px solid var(--border)" },
        el("div", {}, el("span", { class: "badge", style: "margin-right:8px" }, p.slot),
          p.text, el("span", { class: "faint", style: "font-size:11px;margin-left:8px" }, "· " + sched)),
        el("button", { class: "btn-ghost btn-sm btn-danger", onclick: async () => { await api.del("/api/prompts/" + p.id); prompts.splice(prompts.indexOf(p), 1); draw(); } }, "✕")));
    }
  };
  draw();
  const ptext = el("input", { placeholder: "new journal prompt…", style: "flex:1;min-width:160px" });
  const pslot = select("pslot", [["pm", "evening"], ["am", "morning"], ["any", "any"]]);
  // weekday picker
  const wdState = new Set();
  const wdRow = el("div", { class: "row wrap", style: "gap:4px" });
  WD.forEach((d, i) => {
    const b = el("button", { class: "btn-sm", onclick: () => { if (wdState.has(i)) { wdState.delete(i); b.classList.remove("btn-accent"); } else { wdState.add(i); b.classList.add("btn-accent"); } } }, d);
    wdRow.append(b);
  });

  // config import file input
  const cfgFile = el("input", { type: "file", accept: ".json,application/json", style: "display:none", onchange: async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const text = await f.text();
    let cfg; try { cfg = JSON.parse(text); } catch { toast("invalid JSON"); return; }
    const replace = confirm("Replace your current tabs & prompts with this config?\n\nOK = replace everything (destructive)\nCancel = merge / add to what you have");
    const r = await api.post("/api/config/import", { config: cfg, replace });
    toast(`imported ${r.tabs} tab(s)`); state.tabs = await api.get("/api/tabs"); renderChrome();
  } });

  v.append(
    el("div", { class: "panel", style: "margin-bottom:14px" },
      el("h3", {}, "journal prompts"),
      el("div", { class: "faint", style: "font-size:12px;margin-bottom:8px" }, "Leave weekdays unselected for a prompt that can appear any day. Select days to schedule a prompt for those days only."),
      list,
      el("div", { class: "stack", style: "margin-top:10px" },
        el("div", { class: "row wrap" }, ptext, pslot),
        wdRow,
        el("div", { class: "row", style: "justify-content:flex-end" },
          el("button", { class: "btn-accent btn-sm", onclick: async () => {
            if (!ptext.value.trim()) return;
            const np = await api.post("/api/prompts", { text: ptext.value, slot: pslot.value, weekdays: [...wdState].sort().join(",") });
            prompts.push(np); ptext.value = ""; wdState.clear(); $$("button.btn-accent", wdRow).forEach(b => b.classList.remove("btn-accent")); draw();
          } }, "add prompt")))),

    el("div", { class: "panel", style: "margin-bottom:14px" },
      el("h3", {}, "reminders (ntfy)"),
      el("div", { class: "faint", style: "font-size:12px;margin-bottom:10px" }, "Push a daily nudge listing habits you haven't logged. Install the ntfy app, subscribe to your topic, and it works over your tailnet or ntfy.sh."),
      (() => {
        const enabled = el("input", { type: "checkbox", ...(reminders.enabled ? { checked: "" } : {}) });
        const server = el("input", { value: reminders.server || "https://ntfy.sh", placeholder: "https://ntfy.sh", style: "flex:1;min-width:160px" });
        const topic = el("input", { value: reminders.topic || "", placeholder: "your-secret-topic", style: "flex:1;min-width:140px" });
        const time = el("input", { type: "time", value: reminders.time || "20:00" });
        const saveBtn = el("button", { class: "btn-accent btn-sm", onclick: async () => {
          await api.put("/api/reminders", { enabled: enabled.checked, server: server.value, topic: topic.value, time: time.value });
          toast("reminders saved");
        } }, "save");
        const testBtn = el("button", { class: "btn-sm", onclick: async () => {
          await api.put("/api/reminders", { enabled: enabled.checked, server: server.value, topic: topic.value, time: time.value });
          const r = await api.post("/api/reminders/test"); toast(r.sent ? "test sent ✓" : "not sent: " + r.detail);
        } }, "send test");
        return el("div", { class: "stack" },
          el("label", { class: "row" }, enabled, " enabled"),
          field("ntfy server", server),
          field("topic", topic),
          el("div", { class: "row wrap", style: "align-items:flex-end" }, field("time (daily)", time), el("div", { class: "spacer", style: "flex:1" }), testBtn, saveBtn));
      })()),

    el("div", { class: "panel", style: "margin-bottom:14px" },
      el("h3", {}, "config & backup"),
      el("div", { class: "faint", style: "margin-bottom:10px" }, "Config = your tabs, widgets & prompts (no logged data). Backup = everything."),
      el("div", { class: "row wrap" },
        el("a", { class: "btn-sm btn-accent", href: "/api/config/export", download: "" }, "export config"),
        el("button", { class: "btn-sm", onclick: () => cfgFile.click() }, "import config"), cfgFile,
        el("span", { class: "faint", style: "width:100%;height:1px" }),
        el("a", { class: "btn-sm", href: "/api/export/json", download: "" }, "full JSON backup"),
        el("a", { class: "btn-sm", href: "/api/export/csv", download: "" }, "logs as CSV"),
        el("a", { class: "btn-sm", href: "/api/export/db", download: "" }, "raw .db file"))),

    el("div", { class: "panel" },
      el("h3", {}, "appearance"),
      el("div", { class: "row", style: "margin-bottom:12px" }, "theme:",
        el("button", { class: "btn-sm", onclick: () => applyTheme("dark") }, "dark"),
        el("button", { class: "btn-sm", onclick: () => applyTheme("light") }, "light")),
      (() => {
        const wrap = el("div", {});
        const swatches = el("div", { class: "swatches" });
        const custom = el("input", { type: "color", value: state.accent || "#7ee787", title: "custom color", style: "width:38px;height:30px;padding:2px;cursor:pointer" });
        const save = async (color) => { applyAccent(color); await api.put("/api/appearance", { accent: color }); markSel(); };
        const markSel = () => $$(".swatch", swatches).forEach(s => s.classList.toggle("sel", (s.dataset.color || "") === (state.accent || "")));
        for (const [color, name] of DEFAULT_ACCENTS) {
          const sw = el("div", { class: "swatch", title: name, "data-color": color,
            style: "background:" + (color || "var(--accent)"),
            onclick: () => save(color) });
          if (!color) sw.append(el("span", { style: "font-size:14px;display:block;text-align:center;line-height:22px;color:var(--bg)" }, "↺"));
          swatches.append(sw);
        }
        custom.oninput = () => save(custom.value);
        wrap.append(
          el("div", { class: "faint", style: "font-size:12px;margin-bottom:8px" }, "accent color — recolors heatmaps, bars, and highlights. Syncs across your devices. ↺ resets to default."),
          el("div", { class: "row wrap", style: "align-items:center" }, swatches, el("span", { class: "faint" }, "or"), custom));
        setTimeout(markSel, 0);
        return wrap;
      })()));
}

/* modal + utils ------------------------------------------------------- */
function openModal(title, content) {
  closeModal();
  const ov = el("div", { class: "overlay", id: "overlay", onclick: (e) => { if (e.target.id === "overlay") closeModal(); } },
    el("div", { class: "modal" }, el("h2", {}, title), content));
  document.body.append(ov);
}
function closeModal() { const o = $("#overlay"); if (o) o.remove(); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

boot();
