// mmbox console: the operator's view. No framework, no build step; every piece of message data is inserted as text.
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const svg = (d, cls = "ico") => {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("class", cls); s.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path"); p.setAttribute("d", d); s.append(p);
  return s;
};
const ICON = {
  chevron: "M8.59 16.59 13.17 12 8.59 7.41 10 6l6 6-6 6z",
  all: "M20 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2Zm0 4-8 5-8-5V6l8 5 8-5Z",
  machine: "M20 18a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2H0v2h24v-2h-4ZM4 6h16v10H4V6Z",
  folder: "M10 4H4a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-8l-2-2Z",
  box: "M19 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2Zm0 12h-4a3 3 0 0 1-6 0H5V5h14v10Z",
  mbox: "M12 2 2 7v10l10 5 10-5V7L12 2Zm0 2.2L19.5 8 12 11.8 4.5 8 12 4.2Z",
  server: "M4 3h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Zm0 10h16a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Zm3-7.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm0 10a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z",
  servers: "M3 13h18v-2H3v2Zm0 6h18v-2H3v2ZM3 5v2h18V5H3Z",
  model: "M9 2v2H7a3 3 0 0 0-3 3v2H2v2h2v2H2v2h2v2a3 3 0 0 0 3 3h2v2h2v-2h2v2h2v-2h2a3 3 0 0 0 3-3v-2h2v-2h-2v-2h2V9h-2V7a3 3 0 0 0-3-3h-2V2h-2v2h-2V2H9Zm-2 4h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Zm2 3v6h6V9H9Z",
  more: "M12 8a2 2 0 1 0 0-4 2 2 0 0 0 0 4Zm0 2a2 2 0 1 0 0 4 2 2 0 0 0 0-4Zm0 6a2 2 0 1 0 0 4 2 2 0 0 0 0-4Z",
  stop: "M6 6h12v12H6z",
  pin: "M16 3v2h-1v6l2 3v2h-4.5v5.5L12 21l-.5-.5V16H7v-2l2-3V5H8V3h8Z",
  play: "M8 5v14l11-7z",
  console: "M20 3H4a2 2 0 0 0-2 2v11a2 2 0 0 0 2 2h6l-2 3v1h8v-1l-2-3h6a2 2 0 0 0 2-2V5a2 2 0 0 0-2-2Zm0 13H4V5h16v11Z",
  download: "M19 9h-4V3H9v6H5l7 7 7-7ZM5 18v2h14v-2H5Z",
  clip: "M16.5 6v11.5a4 4 0 0 1-8 0V5a2.5 2.5 0 0 1 5 0v10.5a1 1 0 0 1-2 0V6H10v9.5a2.5 2.5 0 0 0 5 0V5a4 4 0 0 0-8 0v12.5a5.5 5.5 0 0 0 11 0V6h-1.5Z",
  back: "M20 11H7.83l5.59-5.59L12 4l-8 8 8 8 1.41-1.41L7.83 13H20v-2z",
  up: "M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6z",
  down: "M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6z",
  lock: "M18 8h-1V6A5 5 0 0 0 7 6v2H6a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V10a2 2 0 0 0-2-2ZM9 6a3 3 0 0 1 6 0v2H9V6Zm3 11a2 2 0 1 1 0-4 2 2 0 0 1 0 4Z",
  check: "M9 16.17 4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z",
};

// ---------------------------------------------------------------- the token and the API
const DEEP = new URLSearchParams(location.hash.slice(1));  // #token=…&tab=settings&msg=<id>: a deep link
const token = (() => {
  const m = location.hash.match(/token=([\w-]+)/);
  if (m) { sessionStorage.setItem("mmbox-console-token", m[1]); history.replaceState(null, "", location.pathname); }
  return sessionStorage.getItem("mmbox-console-token") || "";
})();
async function api(path) {
  const r = await fetch(path, { headers: { "X-Console-Token": token } });
  if (r.status === 401) throw new Error("token refused: open again the URL the console printed");
  return r.json();
}
async function action(body) {
  const r = await fetch("/api/action", { method: "POST", headers: { "Content-Type": "application/json", "X-Console-Token": token }, body: JSON.stringify(body) });
  return r.json();
}

// ---------------------------------------------------------------- state
const S = {
  snap: null, version: 0, boxes: new Map(), servers: new Map(), msgs: [], byId: new Map(),
  node: { type: "all", key: null }, selected: null, limit: 300, expanded: new Set(["machines", "servers"]),
  freshLate: new Set(), startupShown: false,
};
const FKEY = "mmbox-console-filters";
const F = Object.assign({ view: "all", dir: "both", tier: "1", period: "604800", sort: "desc", group: "none", source: "all", search: "", treeActive: false, boxActive: true,
  lateMin: 5, desktop: false, notifyForward: true, statsRange: {} },
  JSON.parse(localStorage.getItem(FKEY) || "{}"));
const saveF = () => localStorage.setItem(FKEY, JSON.stringify(F));

// ---------------------------------------------------------------- formatting
const fmtFull = new Intl.DateTimeFormat(undefined, { dateStyle: "short", timeStyle: "medium" });
const fmtDay = new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const fmtTime = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const fmtShort = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const tsOf = (m) => Date.parse(m.ts || "") || 0;
function when(t) {
  if (!t) return "?";
  const d = new Date(t), now = Date.now();
  if (now - t < 86400e3 && d.getDate() === new Date().getDate()) return fmtTime.format(d);
  return fmtShort.format(d);
}
function ago(sec) {
  if (sec === undefined || sec === null) return "—";
  if (sec < 60) return `${Math.round(sec)} s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)} min ago`;
  if (sec < 86400) return `${Math.round(sec / 3600)} h ago`;
  return `${Math.round(sec / 86400)} d ago`;
}
// "taken" is what the service knows: a box took the message, not that an agent read or handled it
const STATE_TXT = { unread: "unread", taken: "being taken", read: "taken", queued: "queued", sent: "sent", posted: "posted", emptied: "emptied",
  delivered: "consumed", stranded: "left in a closed box", dropped: "dropped" };
const STATE_TITLE = {
  read: "a box took it from the service; it says nothing of what its agent did with it",
  delivered: "its service says it no longer holds it (taken by its box, or deleted with it): learnt afterwards, no log nor collector of this computer saw it taken",
  stranded: "it waits in a box that closed before reading it: the relay forwards real messages to the machine box, the tool's own (ping, notice, link…) stay until the box is removed",
  dropped: "its sender gave up: no service ever accepted it (the recipient was no longer known, or never listed the sender)",
  sent: "sent; nothing says yet whether its box took it",
};
const HANDLED_TXT = { execute: "executed", ask: "asked the user", archived: "archived", rejected: "rejected", expired: "expired", interrupted: "interrupted",
  forwarded: "forwarded", relayed: "relayed", purged: "purged", dropped: "dropped (closed box)", notice: "notice", introduced: "introduction", pinged: "wake-up", pong: "pong",
  broadcast: "broadcast read", lockevent: "lock event", transport: "service announced", retired: "closing", link: "link" };
const LEVEL_TXT = { full: "full content", subject: "subject only", meta: "metadata", none: "not annotated" };

// ---------------------------------------------------------------- the model: boxes, machines, projects
const boxOf = (id) => S.boxes.get(id) || { id, label: null, servers: [] };
const boxName = (id) => { if (!id) return "?"; const b = boxOf(id); return b.title || b.label || b.alias || id; };
const boxLabel = (id) => { const b = boxOf(id); return b.label || b.alias || id; };
// the state of a direct message in words: who took it (the session behind a machine box when known), answered
function stateText(m) {
  if (m.broadcast) return STATE_TXT[m.state] || m.state || "";
  if ((m.answers || []).length) return "answered";
  if (m.state !== "read") return STATE_TXT[m.state] || m.state || "";
  if (m.taken_by) return "taken by " + (m.taken_by.id ? boxName(m.taken_by.id) : m.taken_by.label);
  const r = m.read_by || m.to;
  return boxOf(r).role === "machine" || m.via_machine ? "taken by the machine box" : "taken by its box";
}
function machineKey(b) {
  if (b.role === "machine") return b.id;
  if (b.machine) return b.machine;
  const m = b.hostname && [...S.boxes.values()].find((x) => x.role === "machine" && x.hostname === b.hostname);
  return m ? m.id : "host:" + (b.hostname || "?");
}
function machineName(key) {
  if (key.startsWith("host:")) return key.slice(5) === "?" ? "unknown machine" : key.slice(5);
  const m = S.boxes.get(key);
  return (m && (m.hostname || m.label)) || key;
}
const isActive = (b) => !!b.active && !b.closed_at;
// the provider and model a box declared (each model has its own box); "" when it did not say
const providerOf = (b) => (b.provider || "").toLowerCase();
const modelKey = (b) => providerOf(b) + "\u0000" + (b.model || "");
const modelName = (b) => [b.provider, b.model].filter(Boolean).join(" · ");
function involved(m) {
  const s = new Set([m.from, m.to, ...(m.to_boxes || []), ...(m.seen_by || [])].filter(Boolean));
  return s;
}
function nodeBoxes(node) {
  if (node.type === "box") return new Set([node.key]);
  if (node.type === "machine") return new Set([...S.boxes.values()].filter((b) => machineKey(b) === node.key).map((b) => b.id));
  if (node.type === "provider") return new Set([...S.boxes.values()].filter((b) => providerOf(b) === node.key).map((b) => b.id));
  if (node.type === "model") return new Set([...S.boxes.values()].filter((b) => modelKey(b) === node.key).map((b) => b.id));
  if (node.type === "project") {
    const [mk, wp] = node.key.split("\u0000");
    return new Set([...S.boxes.values()].filter((b) => machineKey(b) === mk && b.role !== "machine" && (b.work || "") === wp).map((b) => b.id));
  }
  return null;
}
function nodeLabel(node) {
  if (node.type === "all") return "All messages";
  if (node.type === "machine") return `Machine ${machineName(node.key)}`;
  if (node.type === "project") { const [mk, wp] = node.key.split("\u0000"); return `Project ${wp || "(no project)"} on ${machineName(mk)}`; }
  if (node.type === "box") return `Box ${boxName(node.key)}`;
  if (node.type === "provider") return `Provider ${node.key || "(not declared)"}`;
  if (node.type === "model") { const [p, m] = node.key.split("\u0000"); return `Model ${[p, m].filter(Boolean).join(" · ") || "(not declared)"}`; }
  if (node.type === "server") return `Server ${(S.servers.get(node.key) || {}).name || node.key}`;
  return "";
}

// ---------------------------------------------------------------- filtering
function filtered() {
  const set = nodeBoxes(S.node);
  const tier = +F.tier, period = +F.period, now = Date.now();
  const q = F.search.trim().toLowerCase();
  const out = S.msgs.filter((m) => {
    if (m.tier > tier) return false;
    if (period && now - tsOf(m) > period * 1000) return false;
    if (S.node.type === "server" && !(m.servers || []).includes(S.node.key)) return false;
    if (set) {
      const ins = [m.to, ...(m.to_boxes || []), ...(m.seen_by || [])].some((b) => set.has(b));
      const outs = set.has(m.from);
      if (F.dir === "in" ? !ins : F.dir === "out" ? !outs : !(ins || outs)) return false;
    }
    switch (F.view) {
      case "unread": if (!["unread", "taken"].includes(m.state)) return false; break;
      case "late": if (!isLate(m) || m.tier > LATE_TIER) return false; break;
      case "read": if (m.state !== "read" && !(m.broadcast && (m.seen_by || []).length)) return false; break;
      case "pending": if (!["queued", "sent"].includes(m.state)) return false; break;
      case "undelivered": if (!["stranded", "dropped"].includes(m.state)) return false; break;
      case "broadcast": if (!m.broadcast) return false; break;
      case "direct": if (m.broadcast) return false; break;
    }
    if (F.source !== "all") {
      const src = m.sources || [];
      if (F.source === "local" && !src.some((s) => s.startsWith("local:") || s.startsWith("waiting:"))) return false;
      if (F.source === "collector" && !src.some((s) => s.startsWith("collector:"))) return false;
    }
    if (q) {
      const hay = [m.subject, m.preview, m.id, m.kind, m.work, m.project, m.topic, boxName(m.from), boxName(m.to), m.from, m.to].join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  out.sort((a, b) => (F.sort === "asc" ? tsOf(a) - tsOf(b) : tsOf(b) - tsOf(a)));
  return out;
}
function groupKey(m) {
  switch (F.group) {
    case "machine": return machineName(machineKey(boxOf(m.from)));
    case "project": return m.work || boxOf(m.from).work || "(no project)";
    case "model": return modelName(boxOf(m.from)) || "(model not declared)";
    case "box": return boxName(m.from) + " → " + (m.broadcast ? (m.scope === "network" ? "network" : "this computer") : boxName(m.to));
    case "server": return (m.servers || []).map((u) => (S.servers.get(u) || {}).name || u).join(", ") || "(local only)";
    case "day": return fmtDay.format(new Date(tsOf(m)));
    case "thread": { let r = m, n = 0; while (r.re && S.byId.get(r.re) && n++ < 50) r = S.byId.get(r.re); return "Thread: " + (r.subject || r.id); }
    default: return null;
  }
}

// ---------------------------------------------------------------- rendering: the tree
function treeItem({ id, type, key, icon, name, sub, badge, dot, children, level, extra }) {
  const hasKids = children && children.length;
  const r = rightsOf({ type, key });
  const li = h("li", { role: "treeitem", id: "t-" + id, "aria-level": level, "aria-selected": String(S.node.type === type && S.node.key === key),
    "aria-expanded": hasKids ? String(S.expanded.has(id)) : null, tabindex: "-1", dataset: { type, key: key ?? "", nid: id },
    "aria-keyshortcuts": r ? "Shift+F10" : null, "aria-description": r ? `handling: ${RIGHTS_TXT[r.rights] || "as the workstations say"} (menu: Shift+F10)` : null });
  const showChip = r && r.rights && (type === "machine" || r.source === "own" || r.network);
  const node = h("div", { class: "node ripple" },
    h("span", { class: "twist" }, hasKids ? svg(ICON.chevron) : null),
    dot ? h("span", { class: "dot " + dot, title: { listening: "listening", active: "active", closed: "closed", paused: "receiving stopped" }[dot] || "" }) : null,
    svg(ICON[icon] || ICON.box),
    h("span", { class: "name", title: name + (sub ? " · " + sub : "") }, name, sub ? h("span", { class: "sub" }, sub) : null),
    showChip ? h("span", { class: "chip rights-chip " + r.rights, title: r.title + ": " + (RIGHTS_TXT[r.rights] || "?") + (r.network ? " (overrides the workstations and boxes)" : r.source === "own" && type === "box" ? " (own setting)" : "") }, RIGHTS_SHORT[r.rights] || "?") : null,
    badge ? h("span", { class: "badge", title: `${badge} unread` }, badge) : null,
    type === "box" && (S.snap?.pinned || []).includes(key) ? h("button", { class: "pin-btn ripple", type: "button", tabindex: "-1",
      "aria-label": `Unpin the conversation with ${name}`, title: "Pinned conversation: nothing expires. Click to unpin (each message starts the full delay again)" }, svg(ICON.pin)) : null,
    r ? h("button", { class: "node-menu ripple", type: "button", tabindex: "-1", "aria-haspopup": "menu", "aria-expanded": "false",
      "aria-label": `Handling of the messages from ${name}`, title: "Handling: execute, ask, ignore" }, svg(ICON.more)) : null);
  li.append(node);
  if (extra) li.append(extra);
  if (hasKids) li.append(h("ul", { role: "group" }, children));
  return li;
}
function renderTree() {
  if (RM.el.matches(":popover-open") || RG.el?.matches(":popover-open")) { S.treeDirty = true; return; }  // its trigger must stay where it is
  const tree = $("#tree");
  const unread = new Map();
  for (const m of S.msgs) if (m.tier <= +F.tier && (m.state === "unread" || m.state === "taken") && m.to) unread.set(m.to, (unread.get(m.to) || 0) + 1);
  const q = $("#tree-filter").value.trim().toLowerCase();
  const machines = new Map();
  for (const b of S.boxes.values()) {
    if (F.treeActive && !isActive(b)) continue;
    const mk = machineKey(b);
    if (!machines.has(mk)) machines.set(mk, { box: null, projects: new Map() });
    const M = machines.get(mk);
    if (b.role === "machine") M.box = b;
    else { const wp = b.work || ""; if (!M.projects.has(wp)) M.projects.set(wp, []); M.projects.get(wp).push(b); }
  }
  const dotOf = (b) => (b.closed_at || b.retired_at ? "closed" : b.listening || b.listening_here ? "listening" : b.active ? "active" : "");
  const sum = (ids) => ids.reduce((n, id) => n + (unread.get(id) || 0), 0);
  const match = (b) => !q || [b.title, b.label, b.id, b.alias, b.work, b.hostname, b.profile, b.activity, b.provider, b.model].join(" ").toLowerCase().includes(q);
  const boxItem = (b, level, pre = "b") => treeItem({ id: pre + b.id, type: "box", key: b.id, icon: b.role === "machine" ? "mbox" : "box", level,
    name: b.title || b.label || b.alias || b.id,
    sub: [b.title ? (b.label || b.id) : b.local ? (b.profile === "default" ? "here" : b.profile) : null, b.role !== "machine" ? b.model : null].filter(Boolean).join(" · ") || null,
    badge: unread.get(b.id), dot: dotOf(b) });
  const mItems = [...machines.entries()].sort((a, b) => machineName(a[0]).localeCompare(machineName(b[0]))).map(([mk, M]) => {
    const kids = [];
    if (M.box && match(M.box)) kids.push(boxItem(M.box, 3));
    for (const [wp, bs] of [...M.projects.entries()].sort((a, b) => a[0].localeCompare(b[0]))) {
      const shown = bs.filter(match).sort((a, b) => (a.label || "").localeCompare(b.label || ""));
      if (!shown.length) continue;
      const pid = "p" + mk + ":" + wp;
      kids.push(treeItem({ id: pid, type: "project", key: mk + "\u0000" + wp, icon: "folder", level: 3, name: wp || "(no project)",
        sub: `${shown.length}`, badge: sum(bs.map((b) => b.id)) || null, children: shown.map((b) => boxItem(b, 4)) }));
    }
    if (!kids.length && q) return null;
    const all = [M.box, ...[...M.projects.values()].flat()].filter(Boolean);
    const live = all.some((b) => b.listening || b.listening_here);
    return treeItem({ id: "m" + mk, type: "machine", key: mk, icon: "machine", level: 2, name: machineName(mk),
      sub: M.box && M.box.label && M.box.label !== machineName(mk) ? M.box.label : null, badge: sum(all.map((b) => b.id)) || null,
      dot: live ? "listening" : all.some(isActive) ? "active" : "", children: kids });
  }).filter(Boolean);
  // Models: provider, then model, then the session boxes running it (machine boxes and the console run no model)
  const providers = new Map();
  for (const b of S.boxes.values()) {
    if (b.role === "machine" || b.role === "operator" || (F.treeActive && !isActive(b)) || !match(b)) continue;
    const p = providerOf(b);
    if (!providers.has(p)) providers.set(p, new Map());
    const ms = providers.get(p);
    if (!ms.has(modelKey(b))) ms.set(modelKey(b), []);
    ms.get(modelKey(b)).push(b);
  }
  const pItems = [...providers.entries()].sort((a, b) => (!a[0]) - (!b[0]) || a[0].localeCompare(b[0])).map(([p, ms]) => {
    const all = [...ms.values()].flat();
    const kids = [...ms.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([mk, bs]) => treeItem({ id: "o" + mk.replace("\u0000", ":"), type: "model", key: mk,
      icon: "model", level: 3, name: mk.split("\u0000")[1] || "(model not declared)", sub: `${bs.length}`, badge: sum(bs.map((b) => b.id)) || null,
      dot: bs.some((b) => b.listening || b.listening_here) ? "listening" : bs.some(isActive) ? "active" : "",
      children: bs.sort((a, b) => (a.title || a.label || "").localeCompare(b.title || b.label || "")).map((b) => boxItem(b, 4, "ob")) }));
    return treeItem({ id: "v" + p, type: "provider", key: p, icon: "model", level: 2, name: p || "(not declared)", sub: `${all.length}`,
      badge: sum(all.map((b) => b.id)) || null, children: kids });
  });
  const sItems = [...S.servers.values()].map((s) => treeItem({ id: "s" + s.url, type: "server", key: s.url, icon: "server", level: 2,
    name: s.name, sub: s.paused ? "receiving stopped" : s.ok ? `${s.active ?? "?"} active` : "unreachable", dot: s.paused ? "paused" : s.ok ? "active" : "closed",
    extra: statsLine(s) }));
  const totalUnread = [...unread.values()].reduce((a, b) => a + b, 0);
  const keep = document.activeElement && tree.contains(document.activeElement) ? document.activeElement.dataset.nid : null;
  tree.replaceChildren(
    treeItem({ id: "all", type: "all", key: null, icon: "all", level: 1, name: "All messages", badge: totalUnread || null }),
    treeItem({ id: "machines", type: "group", key: "machines", icon: "machine", level: 1, name: "Machines", sub: `${machines.size}`, children: mItems }),
    treeItem({ id: "models", type: "group", key: "models", icon: "model", level: 1, name: "Models", sub: `${providers.size}`, children: pItems }),
    treeItem({ id: "servers", type: "group", key: "servers", icon: "servers", level: 1, name: "Servers", sub: `${S.servers.size}`, children: sItems }));
  const items = $$('[role="treeitem"]', tree);
  const cur = (keep && items.find((i) => i.dataset.nid === keep)) || items.find((i) => i.getAttribute("aria-selected") === "true") || items[0];
  if (cur) { cur.tabIndex = 0; if (keep) cur.focus({ preventScroll: true }); }
}
function visibleItems() {
  return $$('#tree [role="treeitem"]').filter((li) => !li.parentElement.closest('[aria-expanded="false"]'));
}
function selectNode(li) {
  const type = li.dataset.type;
  if (type === "group") { toggle(li); return; }
  S.node = { type, key: li.dataset.key || null };
  S.limit = 300;
  closeReader(false);
  renderTree(); renderList();
  const again = $(`#tree [data-nid="${CSS.escape(li.dataset.nid)}"]`);
  if (again) { $$('#tree [role="treeitem"]').forEach((i) => (i.tabIndex = -1)); again.tabIndex = 0; again.focus({ preventScroll: true }); }
}
function toggle(li, open) {
  if (!li.hasAttribute("aria-expanded")) return false;
  const want = open ?? li.getAttribute("aria-expanded") !== "true";
  li.setAttribute("aria-expanded", String(want));
  want ? S.expanded.add(li.dataset.nid) : S.expanded.delete(li.dataset.nid);
  return true;
}
function focusItem(li) {
  if (!li) return;
  $$('#tree [role="treeitem"]').forEach((i) => (i.tabIndex = -1));
  li.tabIndex = 0; li.focus();
}
$("#tree").addEventListener("click", (e) => {
  const li = e.target.closest('[role="treeitem"]');
  if (!li) return;
  if (e.target.closest(".pin-btn")) { setPin(li.dataset.key, false); return; }
  if (e.target.closest(".stats-menu")) { openRangeMenu(e.target.closest(".stats-menu").dataset.url, e.detail !== 0); return; }
  if (e.target.closest(".node-menu")) { openRightsMenu(li, e.detail !== 0); return; }
  if (e.target.closest(".twist") && li.hasAttribute("aria-expanded")) { toggle(li); focusItem(li); return; }
  selectNode(li);
});
$("#tree").addEventListener("contextmenu", (e) => {
  const li = e.target.closest('[role="treeitem"]');
  if (!li || !li.querySelector(":scope > .node .node-menu")) return;
  e.preventDefault();
  openRightsMenu(li, true);
});
$("#tree").addEventListener("keydown", (e) => {
  const li = e.target.closest('[role="treeitem"]');
  if (!li) return;
  const vis = visibleItems(), i = vis.indexOf(li);
  switch (e.key) {
    case "ArrowDown": focusItem(vis[i + 1]); break;
    case "ArrowUp": focusItem(vis[i - 1]); break;
    case "Home": focusItem(vis[0]); break;
    case "End": focusItem(vis.at(-1)); break;
    case "ArrowRight":
      if (li.getAttribute("aria-expanded") === "false") toggle(li, true);
      else if (li.getAttribute("aria-expanded") === "true") focusItem($('[role="treeitem"]', li.lastElementChild));
      break;
    case "ArrowLeft":
      if (li.getAttribute("aria-expanded") === "true") toggle(li, false);
      else focusItem(li.parentElement.closest('[role="treeitem"]'));
      break;
    case "Enter": case " ": selectNode(li); break;
    case "ContextMenu": openRightsMenu(li, false); break;
    case "F10": if (!e.shiftKey) return; openRightsMenu(li, false); break;
    default: return;
  }
  e.preventDefault();
});

// ---------------------------------------------------------------- how this computer handles what a machine or a box sends
const RIGHTS_TXT = { execute: "Execute", ask: "Ask", archive: "Ignore" };
const RIGHTS_SHORT = { execute: "executes", ask: "asks", archive: "ignores" };
function rightsOf(node) {
  const snap = S.snap;
  if (!snap || !node.key) return null;
  if (node.type === "machine") {
    if (node.key === snap.local?.machine) {
      const lr = snap.local_rights;
      return lr ? { target: "local", rights: lr.rights, source: "local-default", env: lr.env, title: "The sessions of this computer, between themselves" } : null;
    }
    const b = S.boxes.get(node.key);
    return b?.rights ? { target: b.id, rights: b.rights.rights, source: b.rights.source, title: `Messages from ${machineName(node.key)} and its sessions` + networkNote(b) } : null;
  }
  if (node.type === "server") {
    const net = snap.service_rights?.[node.key];
    const name = (S.servers.get(node.key) || {}).name || node.key;
    return { target: "@" + node.key, rights: net || null, source: net ? "network" : "none", network: true,
      follow: "Follow the workstations and boxes", following: !net, title: `Everything that arrives through ${name} from other computers` };
  }
  if (node.type === "box") {
    const b = S.boxes.get(node.key);
    if (!b?.rights || b.role === "operator" || b.role === "machine") return null;  // a machine box: its machine's menu
    const r = b.rights;
    const follow = r.local ? "Follow the sessions of this computer" : r.introduced_by ? `Follow its workstation (${machineName(machineKey(b))})` : null;
    return { target: b.id, rights: r.rights, source: r.source, follow, following: r.source === "follows" || r.source === "local",
      env: r.local && snap.local_rights?.env, title: `Messages from ${boxName(b.id)}` + (r.local ? "" : networkNote(b)) };
  }
  return null;
}
const RM = { el: $("#rights-menu"), r: null, nid: null, fromPointer: false };
async function setReception(url, act) {
  const srv = S.servers.get(url) || {};
  const r = await action({ action: act, url });
  if (!r.ok) { showOutput("Receiving", r); return; }
  toast(act === "pause" ? `Receiving stopped from ${srv.name || url}: its messages wait on the service` : `Receiving resumed from ${srv.name || url}`);
  poll(true);
}
// ---------------------------------------------------------------- the traffic of each network
const RANGE_TXT = { 300: "5 min", 3600: "1 h", 86400: "1 d", 604800: "7 d" };
const rangeOf = (url) => +(F.statsRange?.[url] || 3600);
const fmtBytes = (n) => n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n >= 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n >= 1e3 ? `${Math.round(n / 1e3)} kB` : `${n} B`;
function statsLine(s) {
  if (!s.stats) return h("div", { class: "stats-line dim" }, s.ok && !(s.features || []).includes("stats") ? "statistics: older gateway" : "");
  const w = rangeOf(s.url), c = s.stats.windows[String(w)] || { messages: 0, posts: 0, bytes: 0 };
  const cut = s.stats.cleared_ms && s.stats.cleared_ms > Date.now() - w * 1000;
  return h("div", { class: "stats-line", title: `${c.messages} message(s) and ${c.posts} broadcast(s), ${fmtBytes(c.bytes)} encrypted, over ${RANGE_TXT[w]}` + (cut ? `, since the reset (${fmtFull.format(new Date(s.stats.cleared_ms))})` : "") },
    h("span", {}, `${c.messages} msg · ${c.posts} bcast · ${cut ? "since " + fmtTime.format(new Date(s.stats.cleared_ms)) : RANGE_TXT[w]}`),
    h("button", { class: "node-menu stats-menu ripple", type: "button", tabindex: "-1", "aria-haspopup": "menu", "aria-expanded": "false",
      "aria-label": `Statistics period of ${s.name}`, title: "Period: 5 min, 1 h, 1 d, 7 d; reset", dataset: { url: s.url } }, svg(ICON.more)));
}
const RG = { el: $("#range-menu"), url: null, btn: null, nid: null, fromPointer: false };
function openRangeMenu(url, fromPointer) {
  const li = $(`#tree [data-nid="${CSS.escape("s" + url)}"]`);
  const btn = li?.querySelector(":scope > .stats-line .stats-menu");
  if (!btn) return;
  if (RG.el.matches(":popover-open")) RG.el.hidePopover();
  Object.assign(RG, { url, btn, nid: li.dataset.nid, fromPointer });
  $("#range-menu-title").textContent = `Messages through ${(S.servers.get(url) || {}).name || url} over`;
  $$('[data-range]', RG.el).forEach((b) => b.dataset.range !== "clear" && b.setAttribute("aria-checked", String(+b.dataset.range === rangeOf(url))));
  try { RG.el.showPopover({ source: btn }); } catch { RG.el.showPopover(); }
}
RG.el.addEventListener("toggle", (e) => {
  const open = e.newState === "open";
  RG.btn?.setAttribute("aria-expanded", String(open));
  if (open) {
    const b = RG.btn.getBoundingClientRect(), m = RG.el.getBoundingClientRect();
    RG.el.style.left = Math.max(8, Math.min(b.left, window.innerWidth - m.width - 8)) + "px";
    RG.el.style.top = (b.bottom + 4 + m.height > window.innerHeight - 8 ? Math.max(8, b.top - m.height - 4) : b.bottom + 4) + "px";
    ($('[aria-checked="true"]', RG.el) || $("[data-range]", RG.el)).focus({ focusVisible: !RG.fromPointer });
  } else {
    const li = $(`#tree [data-nid="${CSS.escape(RG.nid || "")}"]`);
    if (li && (RG.el.contains(document.activeElement) || document.activeElement === document.body)) focusItem(li);
    if (S.treeDirty) { S.treeDirty = false; renderTree(); }
  }
});
RG.el.addEventListener("keydown", (e) => {
  const items = $$("[data-range]", RG.el), i = items.indexOf(document.activeElement);
  const j = e.key === "ArrowDown" ? (i + 1) % items.length : e.key === "ArrowUp" ? (i - 1 + items.length) % items.length : e.key === "Home" ? 0 : e.key === "End" ? items.length - 1 : -1;
  if (e.key === "Tab") { RG.el.hidePopover(); return; }
  if (j < 0) return;
  e.preventDefault(); items[j].focus();
});
RG.el.addEventListener("pointermove", (e) => { const it = e.target.closest("[data-range]"); if (it && document.activeElement !== it) it.focus({ focusVisible: false }); });
RG.el.addEventListener("click", async (e) => {
  const it = e.target.closest("[data-range]");
  if (!it || !RG.url) return;
  const url = RG.url, v = it.dataset.range;
  if (v === "clear") {
    RG.el.hidePopover();
    const r = await action({ action: "stats_clear", url });
    r.ok ? toast(`Counter of ${(S.servers.get(url) || {}).name || url} reset to zero`) : toast("Failed: " + (r.err || "?"), true);
    poll(true);
    return;
  }
  $$('[data-range]', RG.el).forEach((b) => b.dataset.range !== "clear" && b.setAttribute("aria-checked", String(b === it)));
  F.statsRange = { ...(F.statsRange || {}), [url]: +v }; saveF();
  setTimeout(() => { RG.el.hidePopover(); renderTree(); }, 160);
});
function networkNote(b) {
  const nets = (b?.servers || []).filter((u) => S.snap?.service_rights?.[u]);
  return nets.length ? ` — the network ${nets.map((u) => (S.servers.get(u) || {}).name || u).join(", ")} overrides it` : "";
}
function rmItems() { return $$('[role="menuitemradio"], [role="menuitem"]', RM.el).filter((b) => !b.hidden && !b.disabled); }
function openRightsMenu(li, fromPointer) {
  const btn = li.querySelector(":scope > .node .node-menu");
  const r = rightsOf({ type: li.dataset.type, key: li.dataset.key });
  if (!r || !btn) return;
  if (RM.el.matches(":popover-open")) RM.el.hidePopover();
  Object.assign(RM, { r, nid: li.dataset.nid, fromPointer, btn });
  $("#rights-menu-title").textContent = r.title;
  for (const it of $$('[role="menuitemradio"]', RM.el)) {
    const v = it.dataset.rights;
    if (v === "follow") {
      it.hidden = !r.follow;
      it.firstChild.textContent = r.follow || "Follow the workstation";
      it.setAttribute("aria-checked", String(!!r.following));
      it.disabled = !!r.following || !!r.env;
    } else {
      it.setAttribute("aria-checked", String(r.rights === v));
      it.disabled = !!r.env;
      it.title = r.env ? "MMBOX_LOCAL_RIGHTS is set in the console's environment: it decides for every session" : "";
    }
  }
  $("#rights-follow").previousElementSibling.hidden = !r.follow;
  const pz = $("#rights-pause");
  pz.hidden = $("#rights-pause-sep").hidden = $("#rights-range").hidden = !r.network;
  if (r.network) {
    const srv = S.servers.get(r.target.slice(1)) || {};
    pz.replaceChildren(svg(srv.paused ? ICON.play : ICON.stop), srv.paused ? "Resume receiving" : "Stop receiving",
      h("span", { class: "hint" }, srv.paused ? "reads what waited" : "everything waits on the service"));
    pz.dataset.act = srv.paused ? "resume" : "pause";
    pz.disabled = !srv.known;
    pz.title = srv.known ? "" : "add this service to the list first (Servers)";
  }
  try { RM.el.showPopover({ source: btn }); } catch { RM.el.showPopover(); }
}
function placeMenu() {
  const b = RM.btn?.getBoundingClientRect();
  if (!b) return;
  const m = RM.el.getBoundingClientRect();
  let left = Math.min(b.right - m.width, window.innerWidth - m.width - 8);
  left = Math.max(8, b.left + m.width + 8 < window.innerWidth ? b.left : left);
  let top = b.bottom + 4;
  if (top + m.height > window.innerHeight - 8) top = Math.max(8, b.top - m.height - 4);
  RM.el.style.left = left + "px"; RM.el.style.top = top + "px";
}
RM.el.addEventListener("toggle", (e) => {
  const open = e.newState === "open";
  RM.btn?.setAttribute("aria-expanded", String(open));
  if (open) {
    placeMenu();
    const items = rmItems();
    const first = items.find((i) => i.getAttribute("aria-checked") === "true" && i.dataset.rights !== "follow") || items[0];
    first?.focus({ focusVisible: !RM.fromPointer });
  } else {
    const li = $(`#tree [data-nid="${CSS.escape(RM.nid || "")}"]`);
    if (li && (RM.el.contains(document.activeElement) || document.activeElement === document.body)) focusItem(li);
    if (S.treeDirty) { S.treeDirty = false; renderTree(); }
  }
});
RM.el.addEventListener("keydown", (e) => {
  const items = rmItems(), i = items.indexOf(document.activeElement);
  let j = -1;
  if (e.key === "ArrowDown") j = (i + 1) % items.length;
  else if (e.key === "ArrowUp") j = (i - 1 + items.length) % items.length;
  else if (e.key === "Home") j = 0;
  else if (e.key === "End") j = items.length - 1;
  else if (e.key === "Tab") { RM.el.hidePopover(); return; }
  else return;
  e.preventDefault();
  items[j]?.focus();
});
RM.el.addEventListener("pointermove", (e) => { const it = e.target.closest('[role="menuitemradio"]'); if (it && !it.disabled && document.activeElement !== it) it.focus({ focusVisible: false }); });
RM.el.addEventListener("click", async (e) => {
  if (e.target.closest("#rights-range") && RM.r) { const url = RM.r.target.slice(1); RM.el.hidePopover(); setTimeout(() => openRangeMenu(url, false), 50); return; }
  const pz = e.target.closest("#rights-pause");
  if (pz && !pz.disabled && RM.r) { RM.el.hidePopover(); setReception(RM.r.target.slice(1), pz.dataset.act); return; }
  const it = e.target.closest('[role="menuitemradio"]');
  if (!it || it.disabled || !RM.r) return;
  const r = RM.r, v = it.dataset.rights;
  $$('[role="menuitemradio"]', RM.el).forEach((x) => x.dataset.rights !== "follow" && x.setAttribute("aria-checked", String(x === it)));
  setTimeout(() => RM.el.hidePopover(), 160);  // the new check mark seen first
  if (v !== "follow" && v === r.rights && r.source !== "follows" && r.source !== "local") return;
  const res = await action({ action: "rights", target: r.target, rights: v });
  if (!res.ok) { showOutput("Handling", res); return; }
  toast(`${r.title}: ${v === "follow" ? (r.network ? "follows the workstations and boxes" : "follows its workstation") : RIGHTS_TXT[v]}`);
  poll(true);
});

// ---------------------------------------------------------------- files attached to a message
// Images are fetched with the token, shown from a blob URL (cached per view, message and index, revoked when the view
// closes); other files are chips that download on a click. The server says what may be shown inline.
const RASTER = new Set(["image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp", "image/avif", "image/bmp"]);
const AT = { scopes: new Map(), detail: null };
const TYPE_EXT = { "text/plain": "TXT", "application/json": "JSON", "application/pdf": "PDF", "text/markdown": "MD", "text/csv": "CSV", "application/zip": "ZIP" };
const attExt = (a) => { const m = /\.([0-9a-z]{1,8})$/i.exec(a.name || ""); return m ? m[1].toUpperCase() : TYPE_EXT[(a.type || "").toLowerCase()] || "FILE"; };
function attFamily(a) {
  const e = attExt(a).toLowerCase(), t = a.type || "";
  if (e === "pdf") return "pdf";
  if (/^(zip|gz|tgz|xz|bz2|7z|rar|tar|zst)$/.test(e)) return "archive";
  if (t.startsWith("image/")) return "image";
  if (t.startsWith("text/") || /json|xml|yaml|toml|javascript/.test(t) || /^(md|txt|log|csv|py|js|ts|sh|diff|patch|ini|conf)$/.test(e)) return "text";
  return "other";
}
async function attFetch(m, i, dl) {
  const r = await fetch(`/api/attachment?id=${encodeURIComponent(m.id)}&i=${i}${dl ? "&dl=1" : ""}`, { headers: { "X-Console-Token": token } });
  if (!r.ok) { let why = `HTTP ${r.status}`; try { why = (await r.json()).error || why; } catch { /* not JSON */ } throw new Error(why); }
  return r;
}
function attImage(scope, m, i) {
  let sc = AT.scopes.get(scope);
  if (!sc) AT.scopes.set(scope, sc = new Map());
  const key = m.id + "\n" + i;
  let e = sc.get(key);
  if (e) return e;
  e = { url: null, failed: false };
  e.p = attFetch(m, i, false).then(async (r) => {
    if (!(r.headers.get("Content-Type") || "").startsWith("image/")) throw new Error("not shown as an image");
    const url = URL.createObjectURL(await r.blob());
    if (AT.scopes.get(scope)?.get(key) !== e) { URL.revokeObjectURL(url); throw new Error("view closed"); }
    return (e.url = url);
  });
  e.p.catch(() => { e.failed = true; });
  sc.set(key, e);
  return e;
}
function attRelease(scope) {
  const sc = AT.scopes.get(scope);
  if (!sc) return;
  for (const e of sc.values()) if (e.url) URL.revokeObjectURL(e.url);
  AT.scopes.delete(scope);
}
async function attDownload(m, i, btn) {
  const a = m.attachments[i];
  btn.classList.add("loading"); btn.setAttribute("aria-busy", "true");
  try {
    const blob = await (await attFetch(m, i, true)).blob();
    const url = URL.createObjectURL(blob);
    const link = h("a", { href: url, download: a.name || "file", hidden: true });
    document.body.append(link); link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } catch (e) { toast(`${a.name}: ${e.message}`, true); }
  finally { btn.classList.remove("loading"); btn.removeAttribute("aria-busy"); }
}
function attChip(m, i) {
  const a = m.attachments[i], ext = attExt(a), size = a.size != null ? fmtBytes(a.size) : "";
  const off = !a.path || !!a.error;
  const why = a.error ? `Not kept here: ${a.error}` : "Only on the recipient's computer: this console knows its name, not its content";
  return h("button", { class: "att-chip" + (off ? " off" : " ripple"), type: "button", dataset: { att: m.id + ":" + i },
    "aria-label": off ? `${a.name}: ${why}` : `Download ${a.name}`, "aria-disabled": off ? "true" : null, title: off ? why : `${a.name} · ${size}`,
    onclick: (e) => { e.stopPropagation(); if (off) toast(why); else attDownload(m, i, e.currentTarget); } },
    h("span", { class: "att-icon " + attFamily(a), "aria-hidden": "true" }, h("span", {}, ext.slice(0, 4))),
    h("span", { class: "att-text" }, h("span", { class: "att-name" }, a.name || "file"), h("span", { class: "att-meta" }, [ext, size].filter(Boolean).join(" · "))),
    h("span", { class: "att-dl", "aria-hidden": "true" }, svg(off ? ICON.lock : ICON.download)));
}
function attThumb(scope, m, i) {
  const a = m.attachments[i];
  const img = h("img", { alt: a.name || "image", decoding: "async" });
  const btn = h("button", { class: "att-img ripple loading", type: "button", dataset: { att: m.id + ":" + i }, "aria-label": `Open the image ${a.name}`, title: a.name,
    onclick: (e) => { e.stopPropagation(); if (img.src) openViewer(m, i, img.src, e.currentTarget); } }, img);
  const e = attImage(scope, m, i);
  const fail = () => {  // not an image after all: a chip, with the other files
    const row = btn.parentElement, chip = attChip(m, i);
    if (!row?.classList.contains("att-imgs")) { btn.replaceWith(chip); return; }
    let files = row.nextElementSibling;
    if (!files?.classList.contains("att-files")) { files = h("div", { class: "att-files" }); row.after(files); }
    files.prepend(chip); btn.remove();
    if (!row.children.length) row.remove();
  };
  const show = (url) => {
    img.addEventListener("load", () => { btn.classList.remove("loading"); if (scope === "chat" && CH.stick) $("#chat-log").scrollTop = $("#chat-log").scrollHeight; }, { once: true });
    img.addEventListener("error", fail, { once: true });
    img.src = url;
  };
  if (e.url) show(e.url); else if (e.failed) return attChip(m, i); else e.p.then(show, () => btn.isConnected && fail());
  return btn;
}
function attachmentsBlock(m, scope) {
  const atts = Array.isArray(m.attachments) ? m.attachments : [];
  if (!atts.length) return null;
  const imgs = [], files = [];
  const failed = (i) => AT.scopes.get(scope)?.get(m.id + "\n" + i)?.failed;
  atts.forEach((a, i) => (a.path && !a.error && RASTER.has((a.type || "").toLowerCase()) && !failed(i) ? imgs : files).push(i));
  return h("div", { class: "atts", role: "group", "aria-label": `${atts.length} attachment(s)` },
    imgs.length ? h("div", { class: "att-imgs" }, imgs.map((i) => attThumb(scope, m, i))) : null,
    files.length ? h("div", { class: "att-files" }, files.map((i) => attChip(m, i))) : null);
}
function openViewer(m, i, url, opener) {
  const a = m.attachments[i], dlg = $("#att-viewer");
  $("#att-viewer-title").textContent = a.name || "image";
  $("#att-viewer-meta").textContent = [a.type, a.size != null ? fmtBytes(a.size) : null, boxName(m.from) + " → " + boxName(m.to)].filter(Boolean).join(" · ");
  const img = $("#att-viewer-img");
  img.src = url; img.alt = a.name || "image";
  const dl = $("#att-viewer-dl");
  dl.onclick = () => attDownload(m, i, dl);
  const key = opener?.dataset.att;
  dlg.addEventListener("close", () => {
    img.removeAttribute("src");
    const back = opener?.isConnected ? opener : key ? document.querySelector(`[data-att="${CSS.escape(key)}"]`) : null;
    back?.focus();
  }, { once: true });
  dlg.showModal();
}

// ---------------------------------------------------------------- a box's conversation, as a chat
const CH = { box: null, data: null, version: -1, senders: null, loading: false };
const fmtDays = (s) => { const d = s / 86400; return d >= 1 ? `${+d.toFixed(d < 10 ? 1 : 0)} d` : `${Math.round(s / 3600)} h`; };
async function renderChat(force = false) {
  const box = S.node.key;
  if (CH.box !== box) { attRelease("chat"); Object.assign(CH, { box, data: null, version: -1, senders: null, stick: true }); $("#chat-text").value = ""; $("#chat-error").textContent = ""; }
  drawChatHead();
  if (CH.loading || (!force && CH.version === S.version && CH.data)) return;
  CH.loading = true;
  try {
    const [d, snd] = await Promise.all([api("/api/chat?box=" + encodeURIComponent(box)), CH.senders ? Promise.resolve(CH.senders) : api("/api/senders?to=" + encodeURIComponent(box))]);
    if (CH.box !== box) return;
    CH.data = d; CH.senders = snd; CH.version = S.version;
    drawChatHead(); drawChatLog(); drawChatFrom();
  } catch (e) { $("#chat-error").textContent = e.message; } finally { CH.loading = false; }
}
function drawChatHead() {
  const b = boxOf(CH.box), d = CH.data;
  $("#chat-title").textContent = boxName(CH.box);
  $("#chat-sub").textContent = [b.title && b.label ? b.label : null, machineName(machineKey(b)), b.work, b.activity].filter(Boolean).join(" · ");
  const pinned = d ? d.pinned : (S.snap?.pinned || []).includes(CH.box);
  const own = d?.ttl_s, def = d?.default_ttl_s;
  if (document.activeElement !== $("#chat-ttl")) $("#chat-ttl").value = d ? +((own || def) / 86400).toFixed(3) : "";
  for (const id of ["#chat-ttl", "#chat-ttl-save", "#chat-ttl-reset"]) $(id).disabled = !d || pinned;
  $("#chat-ttl-reset").hidden = !own;
  const pin = $("#chat-pin");
  pin.setAttribute("aria-pressed", String(!!pinned));
  pin.classList.toggle("filled", !!pinned);
  pin.replaceChildren(svg(ICON.pin), pinned ? "Pinned" : "Pin");
  pin.title = pinned ? "Unpin: each message starts the full delay again, as if it had just arrived" : "Freeze the conversation: nothing expires any more";
  $("#chat-keep-help").textContent = !d ? "" : pinned ? "Pinned: no message of this conversation expires."
    : own ? `Setting of this conversation only: ${fmtDays(own)} after each message is received (default ${fmtDays(def)}).`
    : `Default: ${fmtDays(def)} after each message is received (the collector's retention). Another delay applies to this conversation only.`;
}
function drawChatLog() {
  const log = $("#chat-log"), atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  const msgs = (CH.data?.messages || []).filter((m) => (m.tier || 1) <= Math.max(2, +F.tier));
  let lastDay = null;
  const frag = document.createDocumentFragment();
  for (const m of msgs) {
    const t = tsOf(m), day = fmtDay.format(new Date(t));
    if (day !== lastDay) { frag.append(h("div", { class: "chat-day", role: "separator" }, day)); lastDay = day; }
    const fromBox = m.from === CH.box;
    const body = typeof m.body === "string" ? m.body : m.preview || "";
    const subj = m.subject && !body.startsWith(m.subject) ? m.subject : null;
    frag.append(h("article", { class: "bubble " + (fromBox ? "them" : "us") + (isUnread(m) ? " unread" : "") + (m.broadcast ? " broadcast" : ""), tabindex: "-1", dataset: { id: m.id },
      "aria-label": `${fromBox ? boxName(CH.box) : boxName(m.from)}, ${fmtFull.format(new Date(t))}` },
      h("header", {}, h("strong", {}, fromBox ? (m.broadcast ? "Broadcast" : "→ " + (m.to ? boxName(m.to) : "?")) : boxName(m.from)),
        m.kind ? h("span", { class: "chip " + m.kind }, m.kind) : null, h("time", { datetime: m.ts || "", title: fmtFull.format(new Date(t)) }, when(t))),
      subj ? h("p", { class: "bubble-subject" }, subj) : null,
      body ? h("p", { class: "bubble-body" }, body) : h("p", { class: "bubble-body dim" }, "(content not transmitted)"),
      attachmentsBlock(m, "chat"),
      h("footer", {}, stateText(m), m.handled ? ` · ${HANDLED_TXT[m.handled] || m.handled}` : "",
        m.kept_until ? h("span", { class: "dim", title: "this copy expires on " + fmtFull.format(new Date(m.kept_until * 1000)) }, ` · expires ${when(m.kept_until * 1000)}`) : h("span", { class: "dim" }, " · ", svg(ICON.pin, "ico tiny"), " kept"))));
  }
  if (!msgs.length) frag.append(h("p", { class: "empty" }, "No message kept for this box."));
  log.replaceChildren(frag);
  if (RD.open && RD.from === "chat") readerNav();
  if (atBottom || !log.dataset.seen || log.dataset.seen !== CH.box) { log.scrollTop = log.scrollHeight; log.dataset.seen = CH.box; CH.stick = true; }
}
$("#chat-log").addEventListener("scroll", (e) => { const l = e.currentTarget; CH.stick = l.scrollHeight - l.scrollTop - l.clientHeight < 40; }, { passive: true });
function drawChatFrom() {
  const sel = $("#chat-from"), snd = CH.senders?.senders || [];
  const keep = sel.value;
  sel.replaceChildren(...(snd.length ? snd.map((s) => h("option", { value: s.profile }, `${s.role === "machine" ? "machine box" : s.profile} — ${s.label}`))
    : [h("option", { value: "" }, "no box here is paired with it")]));
  if (keep && snd.some((s) => s.profile === keep)) sel.value = keep;
  sel.disabled = $("#chat-text").disabled = !snd.length;
  chatValid();
}
function chatValid() { const ok = !!$("#chat-from").value && $("#chat-text").value.trim().length > 0; $("#chat-send").disabled = !ok; return ok; }
$("#chat-text").addEventListener("input", chatValid);
$("#chat-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#chat-form").requestSubmit(); } });
$("#chat-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!chatValid()) return;
  const text = $("#chat-text").value.trim();
  const first = text.split("\n")[0];
  const btn = $("#chat-send");
  busy(btn, true); $("#chat-error").textContent = "";
  const r = await action({ action: "send", to: CH.box, from: $("#chat-from").value, kind: $("#chat-kind").value,
    subject: first.length > 80 ? first.slice(0, 79) + "…" : first, body: text }).catch((x) => ({ ok: false, err: x.message }));
  busy(btn, false);
  if (!r.ok) { $("#chat-error").textContent = ((r.err || "") + "\n" + (r.out || "")).trim().split("\n").filter(Boolean).at(-1) || "failed"; chatValid(); return; }
  $("#chat-text").value = ""; chatValid();
  await poll(true);
  renderChat(true);
});
$("#chat-log").addEventListener("click", (e) => { if (e.target.closest(".atts")) return; const b = e.target.closest(".bubble"); if (b) openMessage(b.dataset.id); });
$("#chat-log").addEventListener("keydown", (e) => {
  const b = e.target.closest(".bubble");
  if (b && e.target === b && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openMessage(b.dataset.id); }
});
async function setPin(box, pinned) {
  const r = await action({ action: "conv_pin", box, pinned });
  if (!r.ok) { toast("Failed: " + (r.err || "?"), true); return; }
  toast(pinned ? `Conversation with ${boxName(box)} pinned: nothing expires` : `Conversation with ${boxName(box)} unpinned: each message starts the full delay again`);
  S.snap.pinned = pinned ? [...new Set([...(S.snap.pinned || []), box])] : (S.snap.pinned || []).filter((x) => x !== box);
  renderTree(); renderBoxes();
  if (CH.box === box) renderChat(true);
}
$("#chat-pin").addEventListener("click", () => setPin(CH.box, $("#chat-pin").getAttribute("aria-pressed") !== "true"));
$("#chat-ttl-save").addEventListener("click", async (e) => {
  const v = +$("#chat-ttl").value;
  if (!(v > 0)) { $("#chat-error").textContent = "a number of days"; return; }
  const btn = e.currentTarget;
  busy(btn, true);
  const r = await action({ action: "conv_ttl", box: CH.box, days: v });
  busy(btn, false);
  if (!r.ok) { $("#chat-error").textContent = r.err || "failed"; return; }
  toast(`Expiry of this conversation: ${fmtDays(v * 86400)}`); renderChat(true);
});
$("#chat-ttl-reset").addEventListener("click", async () => {
  const r = await action({ action: "conv_ttl", box: CH.box, days: null });
  if (!r.ok) { $("#chat-error").textContent = r.err || "failed"; return; }
  toast("Default expiry for this conversation"); renderChat(true);
});

// ---------------------------------------------------------------- rendering: the list
function stateChip(m) {
  if (m.broadcast) return h("span", { class: "chip broadcast" }, m.scope === "network" ? "network" : m.scope === "machine" ? "@here" : "broadcast");
  const ok = (m.answers || []).length || m.state === "read" || m.state === "delivered";
  return h("span", { class: "chip " + (ok ? "ok" : m.state), title: (m.answers || []).length ? null : STATE_TITLE[m.state] || null }, stateText(m));
}
function route(m) {
  const to = m.broadcast ? (m.scope === "network" ? "the whole network" : (m.to_boxes || []).length > 1 ? `${m.to_boxes.length} channels` : "this computer") : boxName(m.to);
  return `${boxName(m.from)} → ${to}`;
}
const LATE_TIER = 2;  // late messages are reported up to this verbosity (the header button counts the same ones)
const isUnread = (m) => m.state === "unread" || m.state === "taken";
const isLate = (m) => !m.broadcast && isUnread(m) && Date.now() - tsOf(m) > (+F.lateMin || 5) * 60e3;
const isForward = (m) => m.kind === "forward" || !!m.forwarded;
function msgRow(m) {
  const unread = isUnread(m);
  const late = isLate(m);
  return h("div", { class: "msg ripple" + (unread ? " unread" : "") + (late ? " late" + (S.freshLate.has(m.id) ? " fresh-late" : "") : "") + (isForward(m) ? " forward" : ""),
    title: late ? `unread for ${ago((Date.now() - tsOf(m)) / 1000).replace(" ago", "")}` : null, role: "option", id: "m-" + m.id, tabindex: "-1",
    "aria-selected": String(S.selected === m.id), dataset: { id: m.id } },
    h("span", { class: "state" }, h("span", { class: "dot " + (unread ? "listening" : ["queued", "dropped", "stranded"].includes(m.state) ? "closed" : "") })),
    h("span", { class: "line1" }, m.kind ? h("span", { class: "chip " + m.kind }, m.kind) : h("span", { class: "chip" }, "?"),
      stateChip(m), h("span", { class: "route" }, route(m)),
      m.attachments?.length ? h("span", { class: "att-count", title: `${m.attachments.length} attachment(s)` }, svg(ICON.clip, "ico tiny"), h("span", { class: "sr" }, "attachments: "), String(m.attachments.length)) : null),
    h("span", { class: "when", title: m.ts ? fmtFull.format(new Date(tsOf(m))) : "" }, when(tsOf(m))),
    h("span", { class: "subject" }, m.subject || (m.lock ? `lock ${m.lock.name} · ${m.lock.event}` : m.level === "none" || !m.level ? "(content not annotated)" : "(no subject)")),
    m.preview ? h("span", { class: "preview" }, m.preview.replace(/\s+/g, " ")) : null);
}
function renderList() {
  const chat = S.node.type === "box";
  $("#chat").hidden = !chat;
  for (const el of [$(".list-pane .toolbar"), $(".list-pane .list-head"), $("#list")]) el.hidden = chat;
  if (chat) { renderChat(); return; }
  attRelease("chat");
  const list = $("#list");
  const rows = filtered();
  $("#list-count").textContent = `${rows.length} message(s)` + (rows.length > S.limit ? ` · ${S.limit} shown` : "");
  $("#scope").textContent = nodeLabel(S.node);
  const frag = document.createDocumentFragment();
  let last = null;
  const shown = rows.slice(0, S.limit);
  if (F.group !== "none") {
    const counts = new Map();
    for (const m of shown) { const k = groupKey(m); counts.set(k, (counts.get(k) || 0) + 1); }
    if (F.group !== "day") shown.sort((a, b) => groupKey(a).localeCompare(groupKey(b)) || (F.sort === "asc" ? tsOf(a) - tsOf(b) : tsOf(b) - tsOf(a)));
    for (const m of shown) {
      const k = groupKey(m);
      if (k !== last) { frag.append(h("div", { class: "group-head", role: "presentation" }, h("span", {}, k), h("span", {}, String(counts.get(k))))); last = k; }
      frag.append(msgRow(m));
    }
  } else for (const m of shown) frag.append(msgRow(m));
  if (rows.length > S.limit) frag.append(h("div", { class: "more" }, h("button", { class: "btn ripple", type: "button", onclick: () => { S.limit += 300; renderList(); } }, "Show more")));
  if (!rows.length) frag.append(h("p", { class: "empty" }, "No message for these filters."));
  const top = list.scrollTop, hadFocus = list.contains(document.activeElement);
  list.replaceChildren(frag);
  list.scrollTop = top;
  const sel = S.selected && $("#m-" + CSS.escape(S.selected));
  (sel || $(".msg", list))?.setAttribute("tabindex", "0");
  if (hadFocus) (sel || list).focus({ preventScroll: true });  // a refresh does not take the keyboard away
  if (RD.open && RD.from === "list") readerNav();
}
$("#list").addEventListener("click", (e) => {
  const row = e.target.closest(".msg");
  if (row) openMessage(row.dataset.id);
});
$("#list").addEventListener("keydown", (e) => {
  const rows = $$(".msg", $("#list"));
  if (!rows.length) return;
  const cur = e.target.closest(".msg") || rows.find((r) => r.getAttribute("aria-selected") === "true") || rows[0];
  let i = rows.indexOf(cur);
  if (e.key === "ArrowDown") i = Math.min(rows.length - 1, i + 1);
  else if (e.key === "ArrowUp") i = Math.max(0, i - 1);
  else if (e.key === "Home") i = 0;
  else if (e.key === "End") i = rows.length - 1;
  else if (e.key === "Enter" || e.key === " ") { openMessage(cur.dataset.id); e.preventDefault(); return; }
  else return;
  e.preventDefault();
  rows.forEach((r) => (r.tabIndex = -1));
  S.selected = rows[i].dataset.id; markRow(S.selected); rows[i].focus();
});

// ---------------------------------------------------------------- the message
function boxRef(id) {
  if (!id) return "—";
  const b = boxOf(id);
  return h("span", {}, h("button", { class: "boxref", type: "button", onclick: () => openBoxCard(id), title: "Who this box is, what it does; its inbox" }, boxName(id)),
    b.title && b.label ? h("span", { class: "dim" }, ` (${b.label})`) : null,
    " ", h("span", { class: "mono" }, id), b.hostname ? h("span", { class: "dim" }, ` · ${b.hostname}`) : null, b.work ? h("span", { class: "dim" }, ` · ${b.work}`) : null);
}
function openBoxCard(id) {
  const b = boxOf(id);
  const dlg = $("#box-dialog");
  $("#box-title").textContent = b.title || b.label || id;
  const st = b.closed_at || b.retired_at ? "closed" : b.listening || b.listening_here ? "listening" : b.active ? "active" : "unknown";
  const mine = S.msgs.filter((m) => m.to === id && isUnread(m)).length;
  $("#box-content").replaceChildren(
    b.activity ? h("div", { class: "activity" }, h("strong", {}, "Activity" + (b.activity_ms ? " · " + fmtFull.format(new Date(b.activity_ms)) : "")), b.activity)
      : h("p", { class: "notice" }, "This session has not described its activity (mmbox.py activity \"…\")."),
    h("dl", { class: "meta" },
      h("dt", {}, "Label"), h("dd", {}, b.label || "—"),
      h("dt", {}, "Id"), h("dd", { class: "mono" }, id),
      h("dt", {}, "Role"), h("dd", {}, b.role === "machine" ? "machine box" : "session"),
      h("dt", {}, "Machine"), h("dd", {}, machineName(machineKey(b))),
      h("dt", {}, "Project"), h("dd", {}, [b.work, b.project && `space ${b.project}`].filter(Boolean).join(" · ") || "—"),
      h("dt", {}, "State"), h("dd", {}, st, b.idle_s !== undefined ? ` · ${ago(b.idle_s)}` : ""),
      b.opened_ms ? [h("dt", {}, "Opened"), h("dd", {}, fmtFull.format(new Date(b.opened_ms)))] : null,
      h("dt", {}, "Unread"), h("dd", {}, String(mine)),
      rightsOf({ type: "box", key: id }) ? [h("dt", {}, "Handling"), h("dd", {}, RIGHTS_TXT[b.rights.rights] || "?",
        h("span", { class: "dim" }, { follows: " · follows its workstation", local: " · as the sessions of this computer", own: " · own setting" }[b.rights.source] || ""))] : null,
      h("dt", {}, "Servers"), h("dd", {}, (b.servers || []).map((u) => (S.servers.get(u) || {}).name || u).join(", ") || "—"),
      b.agent ? [h("dt", {}, "Agent"), h("dd", {}, b.agent + (b.user ? ` · ${b.user}` : ""))] : null,
      b.role !== "machine" && b.role !== "operator" ? [h("dt", {}, "Model"), h("dd", {}, modelName(b) || "not declared (older client, or no --model)")] : null));
  dlg.returnValue = "";
  dlg.addEventListener("close", () => {
    if (dlg.returnValue === "inbox") gotoBox(id);
    else if (dlg.returnValue === "write") openCompose({ to: id });
  }, { once: true });
  dlg.showModal();
}
function gotoBox(id) {
  showTab("messages");
  const b = boxOf(id);
  const mk = machineKey(b);
  S.expanded.add("machines"); S.expanded.add("m" + mk);
  if (b.role !== "machine") S.expanded.add("p" + mk + ":" + (b.work || ""));
  S.node = { type: "box", key: id }; S.limit = 300;
  closeReader(false);
  renderTree(); renderList();
  $(`#tree [data-nid="${CSS.escape("b" + id)}"]`)?.scrollIntoView({ block: "nearest" });
}
// ---------------------------------------------------------------- the open message takes the place of the list (or of the
// conversation), as in a mail client; the list stays laid out underneath, so the back button finds it where it was.
const RD = { open: null, from: "list", msg: null };
function markRow(id) {
  for (const r of $$(".msg", $("#list"))) { const on = r.dataset.id === id; r.setAttribute("aria-selected", String(on)); r.tabIndex = on ? 0 : -1; }
}
function readerSeq() {
  return RD.from === "chat" ? $$(".bubble", $("#chat-log")).map((b) => b.dataset.id) : $$(".msg", $("#list")).map((r) => r.dataset.id);
}
function readerNav() {
  const seq = readerSeq(), i = seq.indexOf(RD.open);
  $("#reader-pos").textContent = i >= 0 ? `${i + 1} of ${seq.length}` : "";
  $("#reader-prev").disabled = i <= 0;
  $("#reader-next").disabled = i < 0 || i >= seq.length - 1;
}
function readerStep(d) {
  const seq = readerSeq(), i = seq.indexOf(RD.open);
  if (i >= 0 && seq[i + d]) openMessage(seq[i + d]);
}
function closeReader(focus = true) {
  if (!RD.open) return;
  saveDraft();
  RD.open = RD.msg = null;
  $("#view-messages").classList.remove("reading");
  $(".list-pane").inert = false;
  $("#reader").hidden = true;
  attRelease("detail"); AT.detail = null;
  if (!focus) return;
  const el = S.selected && (RD.from === "chat" ? $(`#chat-log .bubble[data-id="${CSS.escape(S.selected)}"]`) : $("#m-" + CSS.escape(S.selected)));
  if (el) {
    // the list kept its place: scroll only if j / k moved the selection out of sight
    const r = el.getBoundingClientRect(), c = el.parentElement.closest(".list, .chat-log").getBoundingClientRect();
    if (r.bottom <= c.top || r.top >= c.bottom) el.scrollIntoView({ block: "nearest" });
    el.focus({ preventScroll: true });
  }
  else (RD.from === "chat" ? $("#chat-log") : $("#list")).focus({ preventScroll: true });
}
async function openMessage(id) {
  if (!id) return;
  if ($("#view-messages").hidden) showTab("messages");
  const changed = RD.open !== id;
  if (changed) saveDraft();
  if (!RD.open) RD.from = S.node.type === "box" && !$("#chat").hidden ? "chat" : "list";
  S.selected = id; markRow(id);
  RD.open = id;
  $("#view-messages").classList.add("reading");
  $(".list-pane").inert = true;
  $("#reader").hidden = false;
  const back = RD.from === "chat" ? "Back to the conversation" : "Back to the list";
  $("#reader-back").setAttribute("aria-label", back); $("#reader-back").title = back + " (Esc)";
  const light = S.byId.get(id) || (CH.data?.messages || []).find((m) => m.id === id);
  if (changed) { $("#reader-scroll").scrollTop = 0; renderDetail(light, true); resetReply(light); }
  readerNav();
  if (!$("#reader").contains(document.activeElement)) $("#reader-scroll").focus({ preventScroll: true });  // the arrows and Page Down scroll the message
  try {
    const full = await api("/api/message?id=" + encodeURIComponent(id));
    if (RD.open === id && !full.error) { renderDetail(full, false); if (RP.id === id) RP.msg = full; }
  } catch (e) { toast(e.message, true); }
}
$("#reader-back").append(svg(ICON.back));
$("#reader-prev").append(svg(ICON.up));
$("#reader-next").append(svg(ICON.down));
$("#reader-back").addEventListener("click", () => closeReader());
$("#reader-prev").addEventListener("click", () => readerStep(-1));
$("#reader-next").addEventListener("click", () => readerStep(1));
document.addEventListener("keydown", (e) => {
  if (!RD.open || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || $("#view-messages").hidden || document.querySelector("dialog[open]")) return;
  const t = e.target;
  if (t.closest?.('input, textarea, select, [contenteditable], [role="menu"], #tree')) return;
  const go = { Escape: () => closeReader(), u: () => closeReader(), k: () => readerStep(-1), j: () => readerStep(1), r: () => openReply() }[e.key];
  if (go) { e.preventDefault(); go(); }
});

// ---------------------------------------------------------------- the reply, under the open message
const RP = { id: null, msg: null, drafts: new Map(), senders: null, sent: new Map() };
// what this console sent from the open message: id, then its state as the next snapshots see it
function renderSent() {
  const el = $("#reply-sent"), list = RP.sent.get(RP.id) || [];
  el.replaceChildren(...list.map((x) => {
    const sm = x.id && S.byId.get(x.id);
    const st = !x.id ? "sent (no id in the client's answer)" : sm ? stateText(sm) : "sent, waiting for the next refresh";
    return h("div", { class: "sent-note" }, svg(ICON.check, "ico tiny"),
      h("span", {}, `Sent ${fmtTime.format(new Date(x.at))} to ${boxName(x.to)}: `, h("strong", {}, st)),
      x.id ? h("span", { class: "mono dim" }, " " + x.id) : null,
      sm ? h("button", { class: "btn text ripple", type: "button", onclick: () => openMessage(x.id) }, "Open") : null);
  }));
}
function replyTarget(m) {
  if (!m?.from) return null;
  // a message one of this computer's boxes sent to a box elsewhere: write again to that box; otherwise answer the author
  if (!m.broadcast && boxOf(m.from).local && m.to && !boxOf(m.to).local) return { to: m.to, as: m.from };
  return { to: m.from, as: m.broadcast ? null : m.to };
}
function saveDraft() {
  if (!RP.id || $("#reply-form").hidden) return;
  const text = $("#reply-text").value;
  if (!text.trim()) { RP.drafts.delete(RP.id); return; }
  RP.drafts.set(RP.id, { text, subject: $("#reply-subject").value, kind: $("#reply-kind").value, expected: $("#reply-expected").checked, from: $("#reply-from").value });
}
function resetReply(m) {
  RP.id = m?.id ?? RD.open; RP.msg = m || null; renderSent();
  $("#reply-form").hidden = true; $("#reply-error").textContent = "";
  const d = RP.drafts.get(RP.id);
  if (d && m) openReply(m, d, false);
}
async function openReply(m = RP.msg, draft = null, focus = true) {
  const t = replyTarget(m);
  if (!t) return;
  const f = $("#reply-form");
  if (!f.hidden && !draft) { if (focus) { $("#reply-text").focus({ preventScroll: true }); f.scrollIntoView({ block: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" }); } return; }
  const subj = m.subject || (m.lock ? `lock ${m.lock.name}` : "");
  $("#reply-head").textContent = `${t.to === m.from ? "Reply to" : "Write again to"} ${boxName(t.to)}`;
  $("#reply-subject").value = draft?.subject ?? (/^re:/i.test(subj) ? subj : "Re: " + subj);
  $("#reply-kind").value = draft?.kind ?? (t.to === m.from ? "reply" : m.kind || "info");
  // writing again to a box that was asked something: the operator wants to hear back
  $("#reply-expected").checked = draft?.expected ?? (t.to !== m.from && (!!m.reply_expected || ["task", "request"].includes(m.kind)));
  $("#reply-text").value = draft?.text ?? "";
  $("#reply-error").textContent = "";
  f.hidden = false;
  const sel = $("#reply-from");
  sel.replaceChildren(h("option", { value: "" }, "…"));
  replyValid();
  if (focus) { $("#reply-text").focus({ preventScroll: true }); f.scrollIntoView({ block: "nearest", behavior: reduceMotion.matches ? "auto" : "smooth" }); }
  try {
    const r = await api("/api/senders?to=" + encodeURIComponent(t.to));
    if (RP.id !== m.id) return;
    const want = draft?.from || (t.as && boxOf(t.as).profile);
    RP.senders = new Map(r.senders.map((s) => [s.profile, s]));
    sel.replaceChildren(...(r.senders.length ? r.senders.map((s) => h("option", { value: s.profile, selected: s.profile === want }, `${s.role === "machine" ? "machine box" : s.profile} — ${s.label}`))
      : [h("option", { value: "" }, "no box here is paired with it")]));
  } catch (e) { $("#reply-error").textContent = e.message; }
  replyValid();
}
// where the answer will land: the box the message is sent from, and whether anybody listens there now
function replyRoute() {
  const el = $("#reply-route"), s = RP.senders?.get($("#reply-from").value);
  if (!s) { el.replaceChildren(); return; }
  const b = boxOf(s.id), name = s.role === "machine" ? `the machine box ${b.label || s.label}` : `the session box ${boxName(s.id)}`;
  const gone = b.closed_at || b.retired_at, live = b.listening || b.listening_here;
  el.className = "reply-route" + (gone ? " warn" : "");
  el.replaceChildren(`Sent as ${name}: its answer comes back to that box`,
    s.role === "machine" ? ", where any session of this computer may take it." : gone ? ", which is closed: nobody will take the answer there; this console still shows it."
      : live ? ", which listens now. This console shows it in the thread." : ", which does not listen now: the answer waits there; this console shows it in the thread.");
}
function replyValid() {
  replyRoute();
  const ok = !!$("#reply-from").value && !!$("#reply-subject").value.trim() && !!$("#reply-text").value.trim();
  $("#reply-send").disabled = !ok; return ok;
}
for (const id of ["#reply-text", "#reply-subject"]) $(id).addEventListener("input", replyValid);
$("#reply-from").addEventListener("change", replyValid);
$("#reply-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $("#reply-form").requestSubmit(); } });
$("#reply-discard").addEventListener("click", () => {
  RP.drafts.delete(RP.id); $("#reply-form").hidden = true; $("#reply-text").value = "";
  ($("#detail .reply-btn") || $("#reader-back")).focus();
});
$("#reply-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const m = RP.msg, t = replyTarget(m);
  if (!t || !replyValid()) return;
  const btn = $("#reply-send");
  busy(btn, true); $("#reply-error").textContent = "";
  const r = await action({ action: "send", to: t.to, from: $("#reply-from").value, kind: $("#reply-kind").value, re: m.id,
    reply_expected: $("#reply-expected").checked, subject: $("#reply-subject").value.trim(), body: $("#reply-text").value }).catch((x) => ({ ok: false, err: x.message }));
  busy(btn, false);
  if (!r.ok) { $("#reply-error").textContent = ((r.err || "") + "\n" + (r.out || "")).trim().split("\n").filter(Boolean).at(-1) || "failed"; replyValid(); return; }
  RP.drafts.delete(m.id); $("#reply-text").value = ""; $("#reply-form").hidden = true;
  const line = (r.out || "sent").trim().split("\n").filter(Boolean).at(-1) || "sent";
  const sid = (/^sent (\S+)/m.exec(r.out || "") || [])[1] || null;
  RP.sent.set(m.id, [...(RP.sent.get(m.id) || []), { id: sid, at: Date.now(), to: t.to, subject: $("#reply-subject").value.trim(), line }]);
  renderSent();
  toast(line);
  ($("#detail .reply-btn") || $("#reader-back")).focus();
  setTimeout(() => poll(true), 800);
});

function renderDetail(m, partial) {
  const d = $("#detail");
  if (AT.detail !== (m?.id ?? null)) { attRelease("detail"); AT.detail = m?.id ?? null; }
  if (!m) { d.replaceChildren(h("p", { class: "empty" }, "Loading…")); return; }
  const chips = h("div", { class: "chips" },
    m.kind ? h("span", { class: "chip " + m.kind }, m.kind) : null, stateChip(m),
    m.handled ? h("span", { class: "chip" }, HANDLED_TXT[m.handled] || m.handled) : null,
    m.reply_expected ? h("span", { class: "chip request" }, "reply expected") : null,
    decisionWaits(m) ? h("span", { class: "chip queued" }, "your decision waits") : null,
    m.level ? h("span", { class: "chip", title: "what the source gives of the content" }, LEVEL_TXT[m.level] || m.level) : null,
    m.lock ? h("span", { class: "chip" }, `lock ${m.lock.name} · ${m.lock.event}`) : null);
  const meta = h("dl", { class: "meta" },
    h("dt", {}, "From"), h("dd", {}, boxRef(m.from)),
    h("dt", {}, m.broadcast ? "Broadcast to" : "To"),
    h("dd", {}, m.broadcast ? h("span", {}, m.scope === "network" ? "the whole network" : "the sessions of this computer", (m.to_boxes || []).length ? ` (${m.to_boxes.map(boxName).join(", ")})` : "") : boxRef(m.to)),
    m.via_machine ? [h("dt", {}, "Via"), h("dd", {}, "the machine box")] : null,
    m.via_session ? [h("dt", {}, "Answered by"), h("dd", {}, m.via_session)] : null,
    h("dt", {}, "Sent"), h("dd", {}, m.ts ? fmtFull.format(new Date(tsOf(m))) : "?", " by ",
      h("button", { class: "boxref", type: "button", onclick: () => openBoxCard(m.from) }, m.sender_title || boxName(m.from)),
      h("span", { class: "dim" }, ` (${boxLabel(m.from)})`)),
    m.read_at ? (() => {
      const rb = m.read_by || m.to, mach = m.via_machine || boxOf(rb).role === "machine";
      return [h("dt", {}, "Taken"), h("dd", {}, fmtFull.format(new Date(Date.parse(m.read_at))),
        rb ? h("span", { class: "dim" }, " by " + boxName(rb) + (mach ? " (machine box)" : "")) : null,
        m.taken_by ? [h("span", { class: "dim" }, ", for the session "), m.taken_by.id ? boxRef(m.taken_by.id) : h("span", {}, m.taken_by.label || "?")]
          : mach ? h("span", { class: "dim" }, m.read_by ? ", for a session of this computer (taken before its client named it)" : ", for one of that computer's sessions (the service does not say which)") : null)];
    })() : null,
    (m.answers || []).length ? [h("dt", {}, "Answered"), h("dd", {}, m.answers.flatMap((id, i) => {
      const a = S.byId.get(id);
      return [i ? ", " : null, h("button", { class: "boxref", type: "button", onclick: () => openMessage(id) }, a ? `${when(tsOf(a))} by ${boxName(a.from)}` : id)];
    }))] : null,
    m.work || m.project ? [h("dt", {}, "Project"), h("dd", {}, [m.work, m.project && `space ${m.project}`].filter(Boolean).join(" · "))] : null,
    m.topic ? [h("dt", {}, "Topic"), h("dd", {}, m.topic)] : null,
    m.re ? [h("dt", {}, "In reply to"), h("dd", {}, S.byId.get(m.re) ? h("button", { class: "boxref", type: "button", onclick: () => openMessage(m.re) }, S.byId.get(m.re).subject || m.re) : h("span", { class: "mono" }, m.re))] : null,
    h("dt", {}, "Id"), h("dd", { class: "mono" }, m.id),
    (m.servers || []).length ? [h("dt", {}, "Servers"), h("dd", {}, m.servers.map((u) => (S.servers.get(u) || {}).name || u).join(", "))] : null,
    h("dt", {}, "Sources"), h("dd", { class: "dim" }, (m.sources || []).join(", ")),
    m.note_status && m.note_status !== "verified" && m.note_status !== "local" ? [h("dt", {}, "Annotation"), h("dd", {}, m.note_status)] : null,
    m.reason ? [h("dt", {}, "Note"), h("dd", {}, m.reason)] : null);
  const thread = S.msgs.filter((x) => x.re === m.id || x.id === m.re || (m.re && x.re === m.re && x.id !== m.id));
  const fw = m.forwarded;
  const fwBlock = fw ? h("div", { class: "notice" }, h("strong", {}, "Forwarded to the machine box"), h("br"),
    `Original message from ${fw.from ? boxName(fw.from) : fw.from_label || "?"}` + (fw.stored_at ? `, stored on ${fmtFull.format(new Date(Date.parse(fw.stored_at)))}` : "") + (fw.reason ? ` — ${fw.reason}` : "")) : null;
  const actBlock = m.sender_activity ? h("div", { class: "activity" }, h("strong", {}, `Activity of ${m.sender_title || boxName(m.from)} when sending`), m.sender_activity) : null;
  const body = typeof m.body === "string" ? m.body : m.preview;
  d.replaceChildren(h("div", { class: "detail" },
    h("h2", {}, m.subject || (m.lock ? `Lock ${m.lock.name}` : "(no subject)")), chips, meta, fwBlock, actBlock,
    body ? h("pre", { class: "body", tabindex: "0", "aria-label": "Message body" }, body + (partial && m.body_len > (m.preview || "").length ? "…" : ""))
      : h("p", { class: "notice" }, m.level === "meta" || m.level === "subject"
        ? "The content is not sent to the collector at this annotation level (a setting of the sending computer)."
        : "Unknown content: neither this computer nor an annotated collector kept its body."),
    m.attachments?.length ? [h("h3", { class: "dim att-head" }, `Attachments (${m.attachments.length})`), attachmentsBlock(m, "detail")] : null,
    decisionBlock(m),
    thread.length ? [h("h3", { class: "dim" }, "Same thread"), h("ul", { class: "thread" }, thread.sort((a, b) => tsOf(a) - tsOf(b)).map((x) =>
      h("li", {}, h("button", { type: "button", onclick: () => openMessage(x.id) }, `${when(tsOf(x))} · ${boxName(x.from)}: ${x.subject || x.id}`))))] : null,
    h("div", { class: "actions" },
      replyTarget(m) ? h("button", { class: "btn filled ripple reply-btn", type: "button", onclick: () => openReply(RP.id === m.id && RP.msg ? RP.msg : m) },
        m.broadcast ? "Reply to the author" : replyTarget(m).to === m.from ? "Reply" : "Write again") : null,
      h("button", { class: "btn ripple", type: "button", onclick: () => navigator.clipboard?.writeText(m.id).then(() => toast("Id copied")) }, "Copy the id")),
    h("details", { class: "raw" }, h("summary", {}, "Raw data"), h("pre", {}, JSON.stringify(m, null, 1)))));
}

// ---------------------------------------------------------------- a message its box asked its user about (`ask` rights)
const DC = { notes: new Map() };
const asksUser = (m) => m && m.handled === "ask" && !m.broadcast && !!m.to;
const questionsAbout = (m) => [...pendingQ(), ...(S.snap?.questions?.recent || [])].filter((q) => q.re === m.id);
const decisionOf = (m) => (S.snap?.questions?.decisions || {})[m.id];
function decisionWaits(m) {
  if (!asksUser(m) || decisionOf(m)) return false;
  const qs = questionsAbout(m);
  return !qs.length || qs.some((q) => q.state === "pending");
}
function decisionBlock(m) {
  if (!asksUser(m)) return null;
  const who = boxName(m.to), d = decisionOf(m), qs = questionsAbout(m);
  const pend = qs.find((q) => q.state === "pending");
  const lines = [];
  if (d) lines.push(h("p", { class: "decide-done " + d.decision }, d.decision === "accept" ? "You accepted it" : "You declined it",
    ` on ${fmtFull.format(new Date(d.at * 1000))}`, d.to !== d.box ? ` (sent to the machine box of ${who}: its session had closed)` : "",
    d.text ? h("span", { class: "dim" }, ` — “${d.text}”`) : null));
  for (const q of qs) {
    if (q.state === "pending") lines.push(h("p", {}, "Its question waits in the console: ", h("strong", {}, q.subject || q.id), " ",
      h("button", { class: "btn small ripple", type: "button", onclick: () => openQuestions(q.id) }, "Answer the question")));
    else if (q.state === "withdrawn") lines.push(h("p", { class: "dim" }, `Its question “${q.subject || q.id}” was withdrawn by the session`
      + (q.closed_at ? ` on ${fmtFull.format(new Date(q.closed_at * 1000))}` : "") + ": you answered it in the session's own chat."));
    else if (q.state === "answered" && q.answer && !q.answer.decision) lines.push(h("p", { class: "dim" }, `You answered its question “${q.subject || q.id}”`
      + (q.closed_at ? ` on ${fmtFull.format(new Date(q.closed_at * 1000))}` : "")
      + `: ${[...(q.answer.chosen || []), q.answer.text].filter(Boolean).join(" · ") || "—"}`));
  }
  const settled = !!d || (qs.length && !pend);
  const nid = "decide-note-" + m.id.replace(/[^\w-]/g, "");
  const note = h("textarea", { id: nid, placeholder: " ", rows: "2", maxlength: "8000",
    oninput: (e) => DC.notes.set(m.id, e.target.value) });
  note.value = DC.notes.get(m.id) || "";
  const err = h("p", { class: "error", role: "alert" });
  const send = async (decision, btn) => {
    busy(btn, true); err.textContent = "";
    const r = await action({ action: "decide", id: m.id, decision, text: note.value }).catch((x) => ({ ok: false, err: x.message }));
    busy(btn, false);
    if (!r.ok) { err.textContent = r.err || "failed"; return; }
    DC.notes.delete(m.id);
    toast(`${decision === "accept" ? "Accepted" : "Declined"}: decision sent to ${r.rerouted ? "the machine box of " + who : who}`);
    const sq = (S.snap.questions ||= {});
    sq.decisions = { ...(sq.decisions || {}), [m.id]: { decision, text: note.value.trim(), at: Date.now() / 1000, to: r.to, box: m.to, id: r.id } };
    const full = await api("/api/message?id=" + encodeURIComponent(m.id)).catch(() => null);
    if (RD.open === m.id) renderDetail(full && !full.error ? full : m, false);
    poll(true);
  };
  const form = h("div", { class: "decide-form" },
    h("div", { class: "field" }, note, h("label", { for: nid }, "A note for the session (optional): a reason, a condition")),
    err,
    h("div", { class: "actions" },
      h("button", { class: "btn danger ripple", type: "button", onclick: (e) => send("decline", e.currentTarget) }, "Decline"),
      h("button", { class: "btn filled ripple", type: "button", onclick: (e) => send("accept", e.currentTarget) }, "Accept")));
  return h("section", { class: "decide" + (settled ? " settled" : ""), "aria-label": "Your decision on this message" },
    h("strong", {}, `${who} asked you what to do with this message`),
    lines,
    settled ? h("details", {}, h("summary", {}, "Send a decision anyway"), form) : form);
}

// ---------------------------------------------------------------- boxes, servers, activity
function renderBoxes() {
  const q = $("#b-search").value.trim().toLowerCase();
  const rows = [...S.boxes.values()].filter((b) => (!F.boxActive || isActive(b)) && (!q || [b.title, b.label, b.id, b.work, b.hostname, b.alias, b.profile, b.activity].join(" ").toLowerCase().includes(q)))
    .sort((a, b) => machineName(machineKey(a)).localeCompare(machineName(machineKey(b))) || (a.role !== "machine") - (b.role !== "machine") || (a.work || "").localeCompare(b.work || "") || (a.label || "").localeCompare(b.label || ""));
  $("#boxes-caption").textContent = `${rows.length} box(es) · state: listening, active (open on a service), closed`;
  $("#boxes-body").replaceChildren(...rows.map((b) => h("tr", {},
    h("td", {}, h("span", { class: "dot " + (b.closed_at || b.retired_at ? "closed" : b.listening || b.listening_here ? "listening" : b.active ? "active" : ""), title: b.closed_at ? "closed" : b.listening ? "listening" : b.active ? "active" : "unknown" })),
    h("td", {}, (S.snap?.pinned || []).includes(b.id) ? h("button", { class: "pin-btn on ripple", type: "button", "aria-label": `Unpin the conversation with ${boxName(b.id)}`,
        title: "Pinned conversation: click to unpin", onclick: () => setPin(b.id, false) }, svg(ICON.pin)) : null,
      h("button", { class: "boxref", type: "button", onclick: () => openBoxCard(b.id) }, b.title || b.label || b.alias || b.id), " ",
      b.title && b.label ? h("span", { class: "dim" }, `(${b.label}) `) : null,
      b.role === "machine" ? h("span", { class: "chip" }, "machine") : null, b.local ? h("span", { class: "chip info" }, "here") : null,
      b.declared === false ? h("span", { class: "chip", title: "older client: declares neither machine nor project" }, "not declared") : null,
      h("div", { class: "mono" }, b.id), b.activity ? h("div", { class: "dim" }, b.activity) : null),
    h("td", {}, machineName(machineKey(b))),
    h("td", {}, b.work || h("span", { class: "dim" }, "—"), b.project ? h("div", { class: "dim" }, "space " + b.project) : null),
    h("td", {}, (b.servers || []).map((u) => (S.servers.get(u) || {}).name || u).join(", ") || "—"),
    h("td", {}, b.waiting ? h("span", { class: "badge" }, b.waiting) : "0"),
    h("td", {}, b.listening ? "listening" : b.idle_s !== undefined ? ago(b.idle_s) : b.closed_at ? "closed " + fmtShort.format(new Date(Date.parse(b.closed_at))) : "—"),
    h("td", {}, h("button", { class: "btn small ripple", type: "button", onclick: () => openCompose({ to: b.id }) }, "Write")))));
}
function renderServers() {
  const snap = S.snap;
  if (!snap) return;
  const op = snap.operator;
  $("#operator").replaceChildren("Operator key of this console: ", h("code", {}, op.id), " · fingerprint ", h("code", {}, op.fingerprint));
  $("#servers").replaceChildren(...snap.servers.map((s) => {
    const col = s.collector;
    const opHere = s.operator;
    const level = h("select", { id: "lvl-" + s.name, "aria-label": `Annotations sent by this computer to the collector of ${s.name}` },
      ["off", "meta", "subject", "full"].map((l) => h("option", { value: l, selected: l === s.col_level }, { off: "none", meta: "metadata", subject: "+ subjects", full: "+ full content" }[l])));
    const howto = !opHere ? h("div", { class: "howto" }, "For this console to read the whole table and the collector of this service, its operator restarts the gateway with:",
      h("pre", {}, s.deploy ? `mmbox-server.sh up --name ${s.deploy.name || "mmbox"} --collector --operator ${op.id}` + (s.deploy.where && s.deploy.where.startsWith("ssh:") ? ` --ssh ${s.deploy.where.slice(4)}` : "")
        : `MMBOX_COLLECTOR=1 MMBOX_OPERATORS=${op.id}`)) : null;
    return h("section", { class: "card", "aria-labelledby": "srv-" + s.name },
      h("h3", { id: "srv-" + s.name }, h("span", { class: "dot " + (s.paused ? "paused" : s.ok ? "listening" : "closed") }), s.name,
        s.paused ? h("span", { class: "chip queued" }, "receiving stopped") : null,
        s.ok ? h("span", { class: "chip ok" }, `${s.ms ?? "?"} ms`) : h("span", { class: "chip queued" }, "unreachable"),
        !s.known ? h("span", { class: "chip request", title: "used by a box but missing from servers.json" }, "not on the list") : null),
      h("dl", { class: "meta" },
        h("dt", {}, "URL"), h("dd", { class: "mono" }, s.url),
        s.address ? [h("dt", {}, "Address"), h("dd", { class: "mono" }, s.address)] : null,
        s.note ? [h("dt", {}, "Note"), h("dd", {}, s.note)] : null,
        s.error ? [h("dt", {}, "Error"), h("dd", {}, s.error)] : null,
        h("dt", {}, "Features"), h("dd", {}, (s.features || []).join(", ") || "—"),
        h("dt", {}, "Used by"), h("dd", {}, (s.users || []).join(", ") || "no box here"),
        h("dt", {}, "Active boxes"), h("dd", {}, s.table_error ? s.table_error : `${s.active} (${s.table_scope === "all" ? "all: operator" : "ours and our peers'"})`),
        s.deploy ? [h("dt", {}, "Deployment"), h("dd", {}, `${s.deploy.where} · ${s.deploy.engine} · ${s.deploy.name}`)] : null,
        h("dt", {}, "Collector"), h("dd", {}, !col ? ((s.features || []).includes("collector") ? "active, no operator" : "none") :
          [`active, ${col.keep_days} d · key `, h("code", {}, col.fingerprint || "not registered"), opHere ? " (this console)" : " (another operator)",
            s.col_read && s.col_read !== "ok" ? h("div", { class: "error" }, "reading: " + s.col_read) : null])),
      howto,
      col ? h("div", { class: "row" }, h("div", { class: "field select" }, level, h("label", { for: "lvl-" + s.name }, "What this computer annotates")),
        h("button", { class: "btn small ripple", type: "button", onclick: (e) => setLevel(e.currentTarget, s, level.value) }, "Apply"),
        s.col_pinned ? h("span", { class: "dim" }, "pinned key " + s.col_pinned) : null) : null,
      opHere && col && !col.registered ? h("button", { class: "btn small ripple", type: "button", onclick: async (e) => { const btn = e.currentTarget; busy(btn, true); const r = await action({ action: "register", url: s.url }); busy(btn, false); toast(r.ok ? "Key registered" : "Failed: " + (r.error || r.err), !r.ok); poll(true); } }, "Register the console's key") : null,
      h("div", { class: "actions" },
        s.known ? h("button", { class: "btn small ripple" + (s.paused ? " filled" : ""), type: "button", "aria-pressed": String(!!s.paused),
          title: s.paused ? "Resume receiving from this service" : "Stop all receiving from this service (kept across restarts)",
          onclick: () => setReception(s.url, s.paused ? "resume" : "pause") }, svg(s.paused ? ICON.play : ICON.stop), s.paused ? "Resume" : "Stop receiving") : null,
        s.known ? h("button", { class: "btn small ripple", type: "button", onclick: () => openServer("edit", s) }, "Edit") : h("button", { class: "btn small ripple", type: "button", onclick: () => openServer("add", s) }, "Add to the list"),
        s.known ? h("button", { class: "btn small ripple", type: "button", onclick: () => confirmRun(`Move all the boxes of this computer to ${s.name}?`,
          "The remote peers are told through the old service; each of them must do the same on its side. What still waits on the old service is no longer read there.",
          { action: "server_use", name: s.name }) }, "Use this service") : null,
        s.known ? h("button", { class: "btn small text ripple", type: "button", onclick: () => confirmRun(`Forget ${s.name}?`,
          "Only the entry in this computer's list goes; the service is not touched. Refused while a box here uses it.", { action: "server_remove", name: s.name }) }, "Remove from the list") : null));
  }));
}
function renderActivity() {
  const rows = (S.snap?.activity || []).slice().sort((a, b) => b.t - a.t).slice(0, 1000);
  const TXT = { open: "opened", close: "closed", expire: "expired", active: "active (back)", removed: "removed", empty: "boxes deleted" };
  $("#activity-body").replaceChildren(...(rows.length ? rows.map((a) => h("tr", {},
    h("td", {}, fmtFull.format(new Date(a.t))), h("td", {}, h("span", { class: "chip " + ({ open: "ok", active: "ok", close: "request", expire: "queued", removed: "queued", empty: "request" }[a.ev] || "") }, TXT[a.ev] || a.ev), a.reason ? h("div", { class: "dim" }, a.reason) : null),
    h("td", {}, h("button", { class: "boxref", type: "button", onclick: () => gotoBox(a.box) }, boxName(a.box)), h("div", { class: "mono" }, a.box)),
    h("td", {}, machineName(machineKey(boxOf(a.box)))), h("td", {}, (S.servers.get(a.server) || {}).name || a.server)))
    : [h("tr", {}, h("td", { colspan: "5", class: "dim" }, "No event: this console is the operator of no collector."))]));
}

// ---------------------------------------------------------------- actions
function busy(btn, on) { btn?.classList.toggle("loading", on); if (btn) btn.disabled = on; }
function showOutput(title, r) {
  $("#output-title").textContent = title;
  $("#output-text").textContent = [r.cmd ? "$ " + r.cmd : null, r.out, r.err, r.ok === false && r.code !== undefined ? `(code ${r.code})` : null].filter(Boolean).join("\n").trim() || (r.ok ? "done" : "failed");
  $("#output").showModal();
}
async function setLevel(btn, s, level) {
  busy(btn, true);
  const r = await action({ action: "collector", url: s.url, level, fingerprint: s.collector?.fingerprint && !s.operator ? s.collector.fingerprint : undefined });
  busy(btn, false);
  if (!r.ok) showOutput("Collector", r); else toast(level === "off" ? "No more annotations for " + s.name : `Annotations "${level}" for ${s.name}`);
  poll(true);
}
function confirmRun(title, text, body, { okLabel = "Confirm", after } = {}) {
  const dlg = $("#confirm");
  $("#confirm-title").textContent = title; $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = okLabel;
  dlg.returnValue = "";
  dlg.addEventListener("close", async () => {
    if (dlg.returnValue !== "ok") return;
    dlg.returnValue = "";
    toast("Running…");
    const r = await action(body);
    showOutput(title, r);
    poll(true);
    after?.(r);
  }, { once: true });
  dlg.showModal();
}
function openServer(mode, s) {
  const dlg = $("#server-dialog");
  $("#server-title").textContent = mode === "add" ? "Add a service" : `Edit ${s.name}`;
  $("#s-mode").value = mode; $("#s-orig").value = s?.name || "";
  $("#s-name").value = s?.name || ""; $("#s-url").value = s?.url || ""; $("#s-address").value = s?.address || ""; $("#s-note").value = s?.note || "";
  $("#s-url").required = mode === "add";
  $("#s-error").textContent = "";
  dlg.returnValue = "";
  dlg.showModal();
}
$("#server-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const mode = $("#s-mode").value, orig = $("#s-orig").value;
  const body = mode === "add"
    ? { action: "server_add", name: $("#s-name").value.trim(), url: $("#s-url").value.trim(), address: $("#s-address").value.trim(), note: $("#s-note").value.trim() }
    : { action: "server_edit", name: orig, rename: $("#s-name").value.trim() !== orig ? $("#s-name").value.trim() : "", url: $("#s-url").value.trim(),
        address: $("#s-address").value.trim(), no_address: !$("#s-address").value.trim(), note: $("#s-note").value.trim() };
  busy(e.submitter, true);
  const r = await action(body);
  busy(e.submitter, false);
  if (!r.ok) { $("#s-error").textContent = (r.err || r.out || "failed").trim().split("\n").at(-1); return; }
  $("#server-dialog").close("save");
  toast("Service saved");
  poll(true);
});
$("#server-add").addEventListener("click", () => openServer("add"));
$("#server-check").addEventListener("click", async (e) => { const btn = e.currentTarget; busy(btn, true); const r = await action({ action: "server_check" }); busy(btn, false); showOutput("Services", r); });

// ---------------------------------------------------------------- compose
let composeTo = null;
function boxOptions(selected) {
  const groups = new Map();
  for (const b of S.boxes.values()) {
    if (b.closed_at || b.retired_at) continue;
    const k = machineName(machineKey(b));
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(b);
  }
  return [h("option", { value: "" }, "— choose a box —"), ...[...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([k, bs]) =>
    h("optgroup", { label: k }, bs.sort((a, b) => (a.role !== "machine") - (b.role !== "machine") || (a.label || "").localeCompare(b.label || "")).map((b) =>
      h("option", { value: b.id, selected: b.id === selected }, `${b.role === "machine" ? "[machine] " : ""}${b.title ? b.title + " — " : ""}${b.label || b.alias || b.id}${b.work ? " · " + b.work : ""}${isActive(b) ? "" : " (inactive)"}`))))];
}
async function loadSenders(to) {
  const sel = $("#c-from");
  sel.replaceChildren(h("option", { value: "" }, "…"));
  const r = await api("/api/senders?to=" + encodeURIComponent(to || ""));
  sel.replaceChildren(...(r.senders.length ? r.senders.map((s) => h("option", { value: s.profile }, `${s.role === "machine" ? "machine box" : s.profile} — ${s.label}`))
    : [h("option", { value: "" }, to ? "no box here is paired with it" : "choose the recipient first")]));
  $("#c-poster").replaceChildren(...(r.posters.length ? r.posters.map((s) => h("option", { value: s.profile }, `${s.profile} — ${s.label}`)) : [h("option", { value: "" }, "no session open here")]));
}
function setMode(mode) {
  $$("#compose .mode-message").forEach((e) => (e.hidden = mode !== "message"));
  $$("#compose .mode-broadcast").forEach((e) => (e.hidden = mode !== "broadcast"));
  $("#c-to").required = $("#c-from").required = mode === "message";
}
function openCompose(pre = {}) {
  const to = pre.to ?? (S.node.type === "box" ? S.node.key : "");
  composeTo = to;
  $("#c-to").replaceChildren(...boxOptions(to));
  $("#c-kind").value = pre.kind || "info"; $("#c-re").value = pre.re || ""; $("#c-subject").value = pre.subject || "";
  $("#c-body").value = ""; $("#c-reply").checked = false; $("#c-error").textContent = "";
  $$('#compose [name="mode"]').forEach((r) => (r.checked = r.value === "message"));
  setMode("message");
  loadSenders(to).catch((e) => ($("#c-error").textContent = e.message));
  const dlg = $("#compose");
  dlg.returnValue = "";
  dlg.showModal();
  (to ? (pre.subject ? $("#c-body") : $("#c-subject")) : $("#c-to")).focus();
}
$("#compose-open").addEventListener("click", () => openCompose());
$("#c-to").addEventListener("change", (e) => loadSenders(e.target.value));
$$('#compose [name="mode"]').forEach((r) => r.addEventListener("change", () => setMode(r.value)));
$("#compose-form").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "send") return;
  e.preventDefault();
  const form = e.currentTarget;
  if (!form.reportValidity()) return;
  const mode = $('#compose [name="mode"]:checked').value;
  const body = mode === "message"
    ? { action: "send", to: $("#c-to").value, from: $("#c-from").value, kind: $("#c-kind").value, re: $("#c-re").value.trim(), reply_expected: $("#c-reply").checked, subject: $("#c-subject").value.trim(), body: $("#c-body").value }
    : { action: "broadcast", from: $("#c-poster").value, scope: $("#c-scope").value, kind: $("#c-bkind").value, topic: $("#c-topic").value.trim(), ttl: +$("#c-ttl").value || 60, subject: $("#c-subject").value.trim(), body: $("#c-body").value };
  busy(e.submitter, true);
  $("#c-error").textContent = "";
  const r = await action(body).catch((x) => ({ ok: false, err: x.message }));
  busy(e.submitter, false);
  if (!r.ok) { $("#c-error").textContent = ((r.err || "") + "\n" + (r.out || "")).trim().split("\n").filter(Boolean).at(-1) || "failed"; return; }
  $("#compose").close("send");
  toast((r.out || "sent").trim().split("\n").at(-1));
  setTimeout(() => poll(true), 800);
});

// ---------------------------------------------------------------- tabs
function showTab(name) {
  for (const t of $$(".tab")) {
    const on = t.id === "tab-" + name;
    t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
    $("#" + t.getAttribute("aria-controls")).hidden = !on;
  }
  localStorage.setItem("mmbox-console-tab", name);
}
$(".tabs").addEventListener("click", (e) => { const t = e.target.closest(".tab"); if (t) showTab(t.id.slice(4)); });
$(".tabs").addEventListener("keydown", (e) => {
  const tabs = $$(".tab"), i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  const j = e.key === "ArrowRight" ? (i + 1) % tabs.length : e.key === "ArrowLeft" ? (i - 1 + tabs.length) % tabs.length : e.key === "Home" ? 0 : e.key === "End" ? tabs.length - 1 : -1;
  if (j < 0) return;
  e.preventDefault(); tabs[j].focus(); showTab(tabs[j].id.slice(4));
});

// ---------------------------------------------------------------- filters
const bind = (sel, key, after) => {
  const el = $(sel);
  if (el.type === "checkbox") el.checked = !!F[key]; else el.value = F[key];
  el.addEventListener(el.tagName === "INPUT" && el.type !== "checkbox" ? "input" : "change", () => {
    F[key] = el.type === "checkbox" ? el.checked : el.value; saveF(); S.limit = 300; after();
  });
};
bind("#f-view", "view", renderList); bind("#f-dir", "dir", renderList); bind("#f-tier", "tier", () => { renderTree(); renderList(); });
bind("#f-period", "period", renderList); bind("#f-sort", "sort", renderList); bind("#f-group", "group", renderList);
bind("#f-source", "source", renderList); bind("#f-search", "search", renderList);
bind("#tree-active-only", "treeActive", renderTree); bind("#b-active", "boxActive", renderBoxes);
$("#tree-filter").addEventListener("input", renderTree);
$("#b-search").addEventListener("input", renderBoxes);

// ---------------------------------------------------------------- the ripple, centred on the pointer (or the control, from the keyboard)
function ripple(el, x, y) {
  if (reduceMotion.matches) return;
  const r = el.getBoundingClientRect();
  const size = Math.hypot(Math.max(x - r.left, r.right - x), Math.max(y - r.top, r.bottom - y)) * 2;
  const w = h("span", { class: "ripple-wave", "aria-hidden": "true" });
  w.style.width = w.style.height = size + "px";
  w.style.left = x - r.left - size / 2 + "px"; w.style.top = y - r.top - size / 2 + "px";
  el.append(w);
  w.addEventListener("animationend", () => w.remove());
}
document.addEventListener("pointerdown", (e) => {
  const el = e.target.closest(".ripple");
  if (el && !el.disabled && e.button === 0) ripple(el, e.clientX, e.clientY);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const t = e.target;
  const el = t.classList?.contains("ripple") ? t : t.matches?.('[role="treeitem"]') ? t.querySelector(":scope > .node") : null;
  if (el && !el.disabled) { const r = el.getBoundingClientRect(); ripple(el, r.left + r.width / 2, r.top + r.height / 2); }
});

// ---------------------------------------------------------------- alerts: late unread messages, messages forwarded to a machine box
const NKEY = "mmbox-console-notified";
const notified = new Set(JSON.parse(localStorage.getItem(NKEY) || "[]"));
function remember(key) {
  notified.add(key);
  localStorage.setItem(NKEY, JSON.stringify([...notified].slice(-3000)));
}
function notify(title, body, tag) {
  if (F.desktop && "Notification" in window && Notification.permission === "granted") {
    const n = new Notification(title, { body: body.slice(0, 300), tag, silent: false });
    n.onclick = () => { window.focus(); if (tag === "late:summary") showLate(); else openMessage(tag.replace(/^(late|fw):/, "")); };
  }
}
function watchAlerts(first) {
  S.freshLate = new Set();
  let late = 0, freshAtOpen = 0;
  for (const m of S.msgs) {
    if (isLate(m) && m.tier <= LATE_TIER) {
      late++;
      if (!notified.has("late:" + m.id)) {
        remember("late:" + m.id);
        S.freshLate.add(m.id);
        if (first) { freshAtOpen++; continue; }  // at the opening: one summary below, not one alert each
        toast(`Unread for more than ${F.lateMin} min: ${m.subject || m.id} (${boxName(m.to)})`);
        notify(`Unread: ${boxName(m.from)} → ${boxName(m.to)}`, m.subject || m.preview || m.id, "late:" + m.id);
      }
    }
    if (isForward(m) && F.notifyForward && !notified.has("fw:" + m.id)) {
      remember("fw:" + m.id);
      if (first && Date.now() - tsOf(m) > 3600e3) continue;  // an old relay: no alert at the opening
      const fw = m.forwarded || {};
      const who = fw.from ? boxName(fw.from) : fw.from_label || boxName(m.from);
      const text = [fw.subject || m.subject, fw.body || m.preview].filter(Boolean).join("\n");
      toast(`Forwarded to the machine box — ${who}: ${fw.subject || m.subject || ""}`);
      notify(who, text || "(forwarded message)", "fw:" + m.id);
    }
  }
  if (freshAtOpen) {
    toast(`${freshAtOpen} message(s) unread for more than ${F.lateMin} min`);
    notify("mmbox: messages waiting", `${freshAtOpen} message(s) unread for more than ${F.lateMin} min`, "late:summary");
  }
  const b = $("#late");
  b.hidden = !late;
  b.textContent = `${late} unread > ${F.lateMin} min`;
}
// The button counts late messages whatever the list's filters: it shows them all, so it widens the filters that would
// hide some (verbosity, period, direction, source, search) instead of opening an empty list.
function showLate() {
  showTab("messages");
  Object.assign(F, { view: "late", dir: "both", source: "all", search: "", period: "0", tier: String(Math.max(+F.tier, LATE_TIER)) });
  for (const [sel, key] of [["#f-view", "view"], ["#f-dir", "dir"], ["#f-source", "source"], ["#f-search", "search"], ["#f-period", "period"], ["#f-tier", "tier"]]) $(sel).value = F[key];
  saveF(); S.limit = 300; S.node = { type: "all", key: null }; renderTree(); renderList();
}
$("#late").addEventListener("click", showLate);

// ---------------------------------------------------------------- settings
const TUNE_HELP = {
  relay_after_s: "a message nobody has taken in a session box goes to the machine box after this delay (120 s by default)",
  relay_busy_s: "a silent session whose agent still runs keeps its messages this long (900 s by default)",
  expire_hours: "past this, a message is no longer delivered by itself: it goes to the backlog and the user decides (24 h by default)",
};
function renderSettings() {
  const t = S.snap?.tuning;
  if (t) {
    const act = document.activeElement;
    for (const [id, k] of [["#t-relay", "relay_after_s"], ["#t-busy", "relay_busy_s"], ["#t-expire", "expire_hours"]]) {
      if ($(id) !== act) $(id).value = t.effective[k];
      $(id + "-help").textContent = (t.set[k] !== undefined ? "set here · " : "default · ") + TUNE_HELP[k];
    }
    $("#t-env").textContent = Object.keys(t.env || {}).length ? "An environment variable overrides: " + Object.keys(t.env).join(", ") : "";
  }
  const cols = (S.snap?.servers || []).filter((s) => s.operator && s.col_conf);
  $("#col-settings").replaceChildren(...cols.map((s) => {
    const c = s.col_conf, id = "keep-" + s.name;
    const keep = h("input", { id, type: "number", min: "0.04", max: "366", step: "0.5", placeholder: " ", value: c.keep_days });
    return h("section", { class: "card", "aria-labelledby": "col-" + s.name },
      h("h3", { id: "col-" + s.name }, `Collector of ${s.name}`),
      h("p", { class: "help" }, `${c.count} event(s) kept` + (c.oldest_ms ? `, the oldest from ${fmtFull.format(new Date(c.oldest_ms))}` : "")
        + (c.hold_until_ms ? ` · nothing is purged before ${fmtFull.format(new Date(c.hold_until_ms))}` : "")),
      h("div", { class: "row" }, h("div", { class: "field" }, keep, h("label", { for: id }, "Keep (days)")),
        h("button", { class: "btn small filled ripple", type: "button", onclick: (e) => colConfig(e.currentTarget, s, { keep_days: +keep.value }, "Retention saved") }, "Apply")),
      h("div", { class: "actions" },
        h("button", { class: "btn small ripple", type: "button", onclick: (e) => colConfig(e.currentTarget, s, { hold_hours: 48 }, "Nothing will be purged for 48 h") }, "Keep everything 48 h longer"),
        h("button", { class: "btn small text ripple", type: "button", onclick: () => confirmRun(`Purge the collector of ${s.name}?`,
          `The ${c.count} kept event(s) are erased from the service. The boxes and their messages are not touched.`, { action: "col_config", url: s.url, purge: true }) }, "Purge everything")));
  }));
}
async function colConfig(btn, s, body, done) {
  busy(btn, true);
  const r = await action({ action: "col_config", url: s.url, ...body });
  busy(btn, false);
  r.ok ? toast(done) : toast("Failed: " + (r.err || "?"), true);
  poll(true);
}
$("#t-apply").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  busy(btn, true);
  const r = await action({ action: "tune", relay_after_s: $("#t-relay").value, relay_busy_s: $("#t-busy").value, expire_hours: $("#t-expire").value });
  busy(btn, false);
  r.ok ? toast("Delays saved for this computer") : showOutput("Delays", r);
  poll(true);
});
$("#t-reset").addEventListener("click", async (e) => {
  const btn = e.currentTarget;
  busy(btn, true);
  const r = await action({ action: "tune", reset: true });
  busy(btn, false);
  r.ok ? toast("Default delays") : showOutput("Delays", r);
  poll(true);
});
$("#n-desktop").addEventListener("change", async (e) => {
  if (e.target.checked && "Notification" in window && Notification.permission !== "granted") {
    const p = await Notification.requestPermission();
    if (p !== "granted") { e.target.checked = false; toast("Notifications refused by the browser", true); }
  }
  F.desktop = e.target.checked; saveF();
});
$("#n-desktop").checked = F.desktop && "Notification" in window && Notification.permission === "granted";
$("#n-forward").checked = F.notifyForward;
$("#n-forward").addEventListener("change", (e) => { F.notifyForward = e.target.checked; saveF(); });
$("#n-late").value = F.lateMin;
$("#n-late").addEventListener("change", (e) => { F.lateMin = Math.max(1, +e.target.value || 5); saveF(); watchAlerts(false); renderList(); });

// ---------------------------------------------------------------- the opening, after a long absence
function dur(sec) {
  if (sec >= 86400) return `${(sec / 86400).toFixed(sec >= 864000 ? 0 : 1)} d`;
  if (sec >= 3600) return `${Math.round(sec / 3600)} h`;
  return `${Math.round(sec / 60)} min`;
}
function showStartup(st) {
  S.startupShown = true;
  const dlg = $("#startup");
  $("#startup-text").replaceChildren(
    h("p", {}, st.last_open ? `Last connection ${dur(st.gap_s)} ago (on ${fmtFull.format(new Date(st.last_open * 1000))}).` : "First opening of the console."),
    h("ul", {}, st.servers.map((s) => h("li", {}, h("strong", {}, s.name), `: current retention ${s.keep_days} d; `,
      h("strong", {}, `${s.expiring_messages} message(s)`), ` (${s.expiring} event(s) of ${s.count}) will expire within ${dur(s.soon_s)} if nothing changes.`))),
    h("p", { class: "help" }, "\"Reset the TTLs to 48 h\" keeps everything the collector holds for at least 48 h more; \"Purge everything\" erases it."));
  dlg.returnValue = "";
  dlg.addEventListener("close", async () => {
    const v = dlg.returnValue;
    dlg.returnValue = "";
    for (const s of st.servers) {
      if (v === "purge") await action({ action: "col_config", url: s.url, purge: true });
      else if (v === "hold") await action({ action: "col_config", url: s.url, hold_hours: 48 });
    }
    if (v !== "purge" && v !== "hold") await action({ action: "dismiss_startup" });
    toast(v === "purge" ? "Collector(s) purged" : v === "hold" ? "Everything is kept 48 h longer" : "Left as it is");
    poll(true);
  }, { once: true });
  dlg.showModal();
}

// ---------------------------------------------------------------- questions the sessions put to the user
const Q = { skipped: new Set(), current: null, known: null, notif: null, notifIds: "", baseIcon: null };
const pendingQ = () => S.snap?.questions?.pending || [];
const NS = () => S.snap?.notify || { popup: true, badge: true, audio: false, sound: "bell" };
const canNotify = () => NS().popup && "Notification" in window && Notification.permission === "granted";
const canAudio = () => NS().audio && (navigator.userActivation ? navigator.userActivation.hasBeenActive : true);
function whoAsks(q) { return q.title || q.label || q.from; }

function openQuestions(first) {
  Q.skipped.clear();
  window.focus();
  showNextQuestion(typeof first === "string" ? first : null);
}
function showNextQuestion(first = null) {
  const dlg = $("#question");
  const list = pendingQ();
  const q = (first && list.find((x) => x.id === first)) || list.find((x) => !Q.skipped.has(x.id));
  if (!q) { Q.current = null; if (dlg.open) dlg.close(); return; }
  Q.current = q;
  const i = list.indexOf(q);
  $("#q-title").textContent = q.subject || "Question";
  $("#q-count").textContent = list.length > 1 ? `${i + 1} / ${list.length}` : "1 question";
  $("#q-from").replaceChildren("From ", h("strong", {}, whoAsks(q)), q.title && q.label ? ` (${q.label})` : "",
    [q.hostname, q.work].filter(Boolean).length ? ` · ${[q.hostname, q.work].filter(Boolean).join(" · ")}` : "",
    q.ts ? ` · asked ${when(Date.parse(q.ts))}` : "",
    q.known ? "" : h("span", { class: "chip queued", title: "this box is not a peer of this computer: its key comes from the service" }, " unknown here"));
  $("#q-desc").textContent = q.body || "(no description)";
  const a = q.about;
  $("#q-about").replaceChildren(a && (a.subject || a.body) ? h("details", { class: "q-about" },
    h("summary", {}, `Message concerned: ${a.subject || a.id}` + (a.label ? ` — from ${a.label}` : "")),
    a.body ? h("pre", {}, a.body) : null) : "");
  const multi = q.multiple !== false;
  $("#q-legend").textContent = multi ? "Proposed solutions (one or more)" : "Proposed solutions (only one)";
  $("#q-options").hidden = !q.options.length;
  $("#q-option-list").replaceChildren(...q.options.map((o, k) => h("label", { class: "q-option ripple" },
    h("input", { type: multi ? "checkbox" : "radio", name: "q-opt", value: String(k) }), h("span", {}, o))));
  $("#q-text").value = "";
  $("#q-error").textContent = "";
  validateAnswer();
  $("#q-skip").textContent = list.filter((x) => !Q.skipped.has(x.id)).length > 1 ? "Skip · next" : "Skip";
  if (!dlg.open) dlg.showModal();
  ($("#q-option-list input") || $("#q-text")).focus();
}
function validateAnswer() {
  const ok = $$("#q-option-list input:checked").length > 0 || $("#q-text").value.trim().length > 0;
  $("#q-send").disabled = !ok;
  $("#q-hint").hidden = ok;
  return ok;
}
$("#question-form").addEventListener("input", validateAnswer);
$("#question-form").addEventListener("change", validateAnswer);
$("#question-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = Q.current;
  if (!q) { $("#question").close(); return; }
  if (e.submitter?.value === "skip") { Q.skipped.add(q.id); showNextQuestion(); return; }
  if (!validateAnswer()) return;
  const choices = $$("#q-option-list input:checked").map((i) => +i.value);
  busy(e.submitter, true);
  const r = await action({ action: "answer", id: q.id, choices, text: $("#q-text").value }).catch((x) => ({ ok: false, err: x.message }));
  busy(e.submitter, false);
  validateAnswer();
  if (!r.ok) { $("#q-error").textContent = r.err || "failed"; return; }
  toast(r.rerouted ? `Answer sent to the machine box of ${whoAsks(q)} (its session is closed)` : `Answer sent to ${whoAsks(q)}`);
  S.snap.questions.pending = pendingQ().filter((x) => x.id !== q.id);
  updateQuestionSignals([], true);
  showNextQuestion();
  poll(true);
});
$("#question").addEventListener("close", () => { Q.current = null; });
$("#questions-btn").addEventListener("click", openQuestions);

// the signals: the app bar button, the persistent notification (one, never stacked), the red dot, the sound
function updateQuestionSignals(newIds, quiet = false) {
  const list = pendingQ(), n = list.length, ns = NS();
  const btn = $("#questions-btn");
  btn.hidden = !n;
  btn.textContent = n === 1 ? "1 question waiting" : `${n} questions waiting`;
  setBadge(ns.badge ? n : 0);
  const ids = list.map((q) => q.id).join(",");
  if (n && canNotify()) {
    if (ids !== Q.notifIds && (newIds.length || !Q.notif)) {
      const last = list.at(-1);
      Q.notif?.close();
      const nt = new Notification(n === 1 ? "mmbox: one question waiting" : `mmbox: ${n} questions waiting`, {
        body: `${whoAsks(last)}: ${last.subject || ""}`, tag: "mmbox-questions", requireInteraction: true, renotify: newIds.length > 0, silent: true });
      nt.onclick = () => { window.focus(); nt.close(); openQuestions(); };
      nt.onclose = () => { if (Q.notif === nt) Q.notif = null; };
      Q.notif = nt;
    }
    Q.notifIds = ids;
  } else if (Q.notif) { Q.notif.close(); Q.notif = null; Q.notifIds = ""; }
  if (!quiet && newIds.length && canAudio()) playSound(ns.sound);
}
let soundUrl = null;
async function playSound(name) {
  try {
    let src = `/sounds/${encodeURIComponent(name)}.mp3`;
    if (name === "custom") {
      if (!soundUrl) {
        const r = await fetch("/api/sound", { headers: { "X-Console-Token": token } });
        if (!r.ok) return;
        soundUrl = URL.createObjectURL(await r.blob());
      }
      src = soundUrl;
    }
    await new Audio(src).play();
  } catch (e) { /* autoplay refused before any interaction: the computer plays it instead next time */ }
}
// the tab's own red dot (the notification area's icon is the console's, drawn by the server)
function setBadge(n) {
  document.title = n ? `(${n}) mmbox console` : "mmbox console";
  if ("setAppBadge" in navigator) (n ? navigator.setAppBadge(n) : navigator.clearAppBadge()).catch(() => {});
  const link = $('link[rel="icon"]');
  Q.baseIcon ??= link.href;
  if (!n) { link.href = Q.baseIcon; return; }
  const img = new Image();
  img.onload = () => {
    const c = document.createElement("canvas"); c.width = c.height = 64;
    const x = c.getContext("2d");
    x.drawImage(img, 0, 0, 64, 64);
    x.beginPath(); x.arc(48, 16, 15, 0, 2 * Math.PI); x.fillStyle = "#e5383b"; x.fill();
    x.lineWidth = 4; x.strokeStyle = "#121212"; x.stroke();
    link.href = c.toDataURL("image/png");
  };
  img.src = Q.baseIcon;
}
// every few seconds, hidden tab included: what waits; the server learns whether this tab notifies and plays itself
async function pollPending() {
  if (!token) return;
  try {
    const r = await api(`/api/pending?notify=${canNotify() ? 1 : 0}&audio=${canAudio() ? 1 : 0}`);
    const ids = r.ids || [];
    if (Q.known === null) Q.known = new Set(pendingQ().map((q) => q.id));
    const fresh = ids.filter((i) => !Q.known.has(i));
    if (fresh.length || ids.length !== pendingQ().length || r.version !== S.version) await poll(false, true);  // it signals them
  } catch { /* the full poll reports it */ }
}

// ---------------------------------------------------------------- settings: how a question is announced
function renderQuestionSettings() {
  const ns = NS();
  $("#q-popup").checked = !!ns.popup;
  $("#q-badge").checked = !!ns.badge; $("#q-audio").checked = !!ns.audio; $("#q-none").checked = !ns.badge && !ns.audio;
  $("#q-sound").hidden = !ns.audio;
  const sounds = Object.entries(S.snap?.sounds || {});
  const custom = S.snap?.custom_sound;
  const keep = document.activeElement?.closest?.("#q-sound-list") ? document.activeElement.dataset.k : null;
  $("#q-sound-list").replaceChildren(...[...sounds, ...(custom ? [["custom", `Imported: ${custom.name}`]] : [])].map(([k, label]) =>
    h("div", { class: "sound-row" },
      h("label", {}, h("input", { type: "radio", name: "q-sound", value: k, checked: ns.sound === k, dataset: { k } }), h("span", {}, label)),
      h("button", { class: "btn icon ripple", type: "button", "aria-label": `Listen to ${label}`, title: "Listen", dataset: { k: "play-" + k }, onclick: () => { soundUrl = k === "custom" ? null : soundUrl; playSound(k); } }, svg(ICON.play)))));
  if (keep) $(`#q-sound-list [data-k="${CSS.escape(keep)}"]`)?.focus();
}
async function setNotify(patch) {
  const r = await action({ action: "notify", ...patch });
  if (!r.ok) { toast("Failed: " + (r.err || "?"), true); return; }
  S.snap.notify = r.notify;
  renderQuestionSettings();
  updateQuestionSignals([], true);
}
$("#q-popup").addEventListener("change", async (e) => {
  if (e.target.checked && "Notification" in window && Notification.permission === "default") await Notification.requestPermission();
  setNotify({ popup: e.target.checked });
});
$("#q-none").addEventListener("change", (e) => {
  if (e.target.checked) setNotify({ badge: false, audio: false });
  else setNotify({ badge: true });  // nothing chosen is not a state: back to the default, the red dot
});
$("#q-badge").addEventListener("change", (e) => setNotify({ badge: e.target.checked }));
$("#q-audio").addEventListener("change", (e) => setNotify({ audio: e.target.checked }));
$("#q-sound-list").addEventListener("change", (e) => { if (e.target.name === "q-sound") { soundUrl = null; setNotify({ sound: e.target.value }); playSound(e.target.value); } });
$("#q-import").addEventListener("click", () => $("#q-file").click());
$("#q-file").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  e.target.value = "";
  if (!f) return;
  if (f.size > 3e6) { toast("An MP3 of 3 MB at most", true); return; }
  const data = await new Promise((ok, ko) => { const rd = new FileReader(); rd.onload = () => ok(String(rd.result).split(",")[1] || ""); rd.onerror = ko; rd.readAsDataURL(f); });
  const r = await action({ action: "sound_upload", name: f.name, data });
  if (!r.ok) { toast("Import refused: " + (r.err || "?"), true); return; }
  soundUrl = null;
  toast(`Sound imported: ${f.name}`);
  await poll(true);
  playSound("custom");
});
$("#q-test").addEventListener("click", async (e) => { const btn = e.currentTarget; busy(btn, true); const r = await action({ action: "test_sound" }); busy(btn, false); if (!r.ok) toast("Failed: " + (r.err || "?"), true); });

// ---------------------------------------------------------------- toasts
function toast(text, err = false) {
  const t = h("div", { class: "toast" + (err ? " err" : "") }, text);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), err ? 8000 : 4000);
}

// ---------------------------------------------------------------- polling
let polling = false;
async function poll(force = false, background = false) {
  if (polling) return;
  polling = true;
  const sync = $("#sync");
  try {
    if (force) await action({ action: "refresh" });
    const snap = await api("/api/snapshot?version=" + (force ? 0 : S.version));
    if (!snap.unchanged) {
      S.snap = snap; S.version = snap.version; S.firstVersion ??= snap.version;
      S.boxes = new Map(snap.boxes.map((b) => [b.id, b]));
      S.servers = new Map(snap.servers.map((s) => [s.url, s]));
      S.msgs = snap.messages; S.byId = new Map(S.msgs.map((m) => [m.id, m]));
      watchAlerts(snap.version === S.firstVersion);
      renderSent(); renderTree(); renderList(); renderBoxes(); renderServers(); renderActivity(); renderSettings(); renderQuestionSettings();
      const first = Q.known === null;
      const ids = pendingQ().map((q) => q.id);
      const fresh = first ? [] : ids.filter((i) => !Q.known.has(i));
      Q.known = new Set([...(Q.known || []), ...ids]);
      updateQuestionSignals(fresh, first);
      if (Q.current && !ids.includes(Q.current.id)) showNextQuestion();  // answered or withdrawn elsewhere
      if (S.wantQuestion && ids.length) { S.wantQuestion = false; openQuestions(); }
      if (snap.startup && !S.startupShown) showStartup(snap.startup);
      if (RD.open && S.byId.has(RD.open) && !$("#detail").contains(document.activeElement)) {
        const id = RD.open;
        api("/api/message?id=" + encodeURIComponent(id)).then((m) => { if (!m.error && RD.open === id) { renderDetail(m, false); if (RP.id === id) RP.msg = m; } }).catch(() => {});
      }
      for (const e of snap.errors || []) toast(e, true);
    }
    sync.classList.remove("err");
    sync.textContent = "up to date " + fmtTime.format(new Date());
  } catch (e) {
    sync.classList.add("err");
    sync.textContent = "offline: " + e.message;
  } finally { polling = false; }
}
$("#refresh").addEventListener("click", async (e) => { const btn = e.currentTarget; busy(btn, true); await poll(true); busy(btn, false); });

// ---------------------------------------------------------------- memory: the explorer's frame and its view
// What the Redis of each service where this console is an operator holds, and this computer (the console's folder,
// the boxes' folder); the view purges the console's copy of the messages that no longer wait in a box, or empties
// every box of those services.
const MEM = { data: null, loading: false };
const pct = (a, b) => (b ? Math.min(100, (a / b) * 100) : 0);
function memBar(p) {
  const fill = h("span");
  fill.style.width = `${p}%`;
  return h("span", { class: `mem-bar${p >= 90 ? " err" : p >= 70 ? " warn" : ""}`, "aria-hidden": "true" }, fill);
}
const memDl = (rows) => h("dl", { class: "meta" }, rows.filter(Boolean).flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, String(v))]));
function redisSum(d) {
  const rows = (d?.servers || []).filter((s) => s.redis);
  const fig = rows.filter((s) => !s.redis.acl);  // a Redis user without the memory commands: counts, no figures
  const used = fig.reduce((t, s) => t + (s.redis.used || 0), 0);
  const max = fig.length && fig.every((s) => s.redis.max) ? fig.reduce((t, s) => t + s.redis.max, 0) : 0;
  return { rows, fig, used, max, waiting: rows.reduce((t, s) => t + (s.redis.boxes?.entries || 0), 0),
    boxes: rows.reduce((t, s) => t + (s.redis.boxes?.count || 0), 0) };
}
async function loadUsage(fresh = false) {
  if (!token || MEM.loading) return;
  MEM.loading = true;
  if (fresh) busy($("#mem-refresh"), true);
  try {
    MEM.data = await api(`/api/usage${fresh ? "?fresh=1" : ""}`);
    renderMemFrame();
    if ($("#mem-dialog").open) renderMemDialog();
  } catch { $("#mem-redis").textContent = "—"; }
  finally { MEM.loading = false; if (fresh) busy($("#mem-refresh"), false); }
}
function renderMemFrame() {
  const d = MEM.data;
  if (!d) return;
  const r = redisSum(d), p = pct(r.used, r.max), bar = $("#mem-redis-bar");
  const redis = r.fig.length ? (r.max ? `${fmtBytes(r.used)} / ${fmtBytes(r.max)}` : fmtBytes(r.used)) : "—";
  $("#mem-redis").textContent = redis;
  bar.style.width = `${p}%`;
  bar.parentElement.classList.toggle("warn", p >= 70 && p < 90);
  bar.parentElement.classList.toggle("err", p >= 90);
  $("#mem-local").textContent = fmtBytes(d.local.total);
  $("#mem-frame").title = r.fig.length ? "Memory: open the view" : (d.servers.find((s) => s.why || s.redis?.acl)?.why || r.rows[0]?.redis.acl || "no service");
  $("#mem-frame-sum").textContent = `Redis ${redis}${r.max ? `, ${Math.round(p)} %` : ""}; this computer ${fmtBytes(d.local.total)}`;
}
function renderMemDialog() {
  const d = MEM.data;
  if (!d) return;
  $("#mem-at").textContent = `measured at ${new Date(d.at * 1000).toLocaleTimeString()}`;
  const cards = d.servers.map((s) => {
    if (!s.redis) return h("div", { class: "mem-card" }, h("h4", {}, s.name), h("p", { class: "why" }, s.why || "no figures"));
    const R = s.redis;
    return h("div", { class: "mem-card" },
      h("h4", {}, s.name, h("span", {}, R.acl ? "—" : R.max ? `${fmtBytes(R.used)} / ${fmtBytes(R.max)}` : fmtBytes(R.used))),
      R.max && !R.acl ? memBar(pct(R.used, R.max)) : null,
      R.acl ? h("p", { class: "why" }, R.acl) : null,
      memDl([R.acl ? null : ["Peak", fmtBytes(R.peak || 0)], R.acl ? null : ["When full", R.policy || "—"], R.acl ? null : ["Keys", R.keys ?? "—"],
        ["Boxes", `${R.boxes.count} · ${R.boxes.entries} message(s) waiting${R.boxes.bytes == null ? "" : " · " + fmtBytes(R.boxes.bytes)}`],
        ["Channels", `${R.channels.count}${R.channels.bytes == null ? "" : " · " + fmtBytes(R.channels.bytes)}`],
        ["Collector", `${R.collector.entries} event(s)${R.collector.bytes == null ? "" : " · " + fmtBytes(R.collector.bytes)}`],
        R.stats.bytes == null ? null : ["Traffic counts", fmtBytes(R.stats.bytes)]]));
  });
  $("#mem-servers").replaceChildren(...(cards.length ? cards : [h("p", { class: "why" }, "No service configured.")]));
  const L = d.local, C = L.console, B = L.boxes;
  $("#mem-localbox").replaceChildren(
    h("div", { class: "mem-card" }, h("h4", {}, "Console", h("span", {}, fmtBytes(C.total))),
      memDl([["Messages kept", `${L.messages} · ${fmtBytes(C.archive)}`], ["Attached files", fmtBytes(C.attachments)],
        ["Window data", fmtBytes(C.window)], ["Settings, keys", fmtBytes(C.other)]]),
      h("span", { class: "path" }, C.path)),
    h("div", { class: "mem-card" }, h("h4", {}, "mmbox boxes", h("span", {}, fmtBytes(B.total))),
      memDl([["Boxes", `${B.profiles} (keys, logs, attached files)`]]),
      h("span", { class: "path" }, B.path)));
  $("#mem-purge-text").textContent = L.purgeable
    ? `${L.purgeable} of the ${L.messages} messages the console keeps no longer wait in a box: they go for good, pinned conversations included, with the attached files it keeps for them. Those still waiting stay. The boxes' own logs on this computer are not touched, so the message list still shows what they hold.`
    : `Every message the console keeps (${L.messages}) still waits in a box: nothing to purge.`;
  $("#mem-purge").disabled = !L.purgeable;
  const r = redisSum(d), names = r.rows.map((s) => s.name).join(", ");
  $("#mem-empty-text").textContent = r.rows.length
    ? `${r.boxes} box(es) on ${names}, ${r.waiting} message(s) waiting in them. Every box of every computer is deleted with what waits in it, taken or not, for good (this console's own box and its questions stay). A session still running registers its box again by itself at its next contact; what is sent to it meanwhile waits in its sender's queue.`
    : "No service where this console is an operator gives its memory figures.";
  $("#mem-empty").disabled = !r.rows.length || !r.boxes;
}
$("#mem-frame").addEventListener("click", () => { renderMemDialog(); $("#mem-dialog").showModal(); loadUsage(true); });
$("#mem-refresh").addEventListener("click", () => loadUsage(true));
$("#mem-purge").addEventListener("click", () => {
  const L = MEM.data.local;
  confirmRun("Purge the console's messages",
    `${L.purgeable} message(s) no longer waiting in a box are deleted from the console's copy, pinned conversations included, and cannot come back. Those still waiting stay.`,
    { action: "purge_archive" }, { okLabel: "Purge", after: () => loadUsage(true) });
});
$("#mem-empty").addEventListener("click", () => {
  const r = redisSum(MEM.data);
  confirmRun("Delete every mmbox box",
    `${r.boxes} box(es) of ${r.rows.map((s) => s.name).join(", ")} are deleted, for every computer and every session, with the ${r.waiting} message(s) waiting in them: nobody will receive those. Sessions still running come back on their own.`,
    { action: "empty_boxes" }, { okLabel: "Delete the boxes", after: () => loadUsage(true) });
});

showTab(DEEP.get("tab") || localStorage.getItem("mmbox-console-tab") || "messages");
if (DEEP.get("msg")) { const id = DEEP.get("msg"); const t = setInterval(() => { if (S.byId.has(id)) { clearInterval(t); openMessage(id); } }, 300); }
if (DEEP.get("box")) { const id = DEEP.get("box"); const t = setInterval(() => { if (S.boxes.has(id)) { clearInterval(t); openBoxCard(id); } }, 300); }
if (!token) { $("#sync").textContent = "no token: open the URL the console printed"; $("#sync").classList.add("err"); }
else {
  S.wantQuestion = DEEP.get("question") === "next";
  poll(); setInterval(() => document.visibilityState === "visible" && poll(), 5000);
  setInterval(pollPending, 4000);
  loadUsage(); setInterval(() => document.visibilityState === "visible" && loadUsage(), 60000);
}
