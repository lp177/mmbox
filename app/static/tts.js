"use strict";
// mmbox console: messages read aloud (mmbox_tts.py). Loaded after console.js, whose helpers it uses ($, h, svg, nm,
// action, api, toast, busy, openMessage…). The console plays the audio itself (GStreamer): this page shows its player
// and drives it. A message's audio is made only when it is played (its Listen button, or a reading rule), then kept.

const TT = { st: { st: "idle" }, at: 0, cfgV: 0, timer: null, raf: 0, scrub: null, lastV: -1 };
const TTS_ICON = {
  speaker: "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05A4.47 4.47 0 0 0 16.5 12zM14 3.23v2.06a7 7 0 0 1 0 13.42v2.06A9 9 0 0 0 14 3.23z",
  pause: "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
  replay10: "M11.99 5V1l-5 5 5 5V7c3.31 0 6 2.69 6 6s-2.69 6-6 6-6-2.69-6-6h-2c0 4.42 3.58 8 8 8s8-3.58 8-8-3.58-8-8-8zm-1.1 11h-.85v-3.26l-1.01.31v-.69l1.77-.63h.09V16zm4.28-1.76c0 .32-.03.6-.1.82s-.17.42-.29.57-.28.26-.45.33-.37.1-.59.1-.41-.03-.59-.1-.33-.18-.46-.33-.23-.34-.3-.57-.11-.5-.11-.82v-.74c0-.32.03-.6.1-.82s.17-.42.29-.57.28-.26.45-.33.37-.1.59-.1.41.03.59.1.33.18.46.33.23.34.3.57.11.5.11.82v.74zm-.85-.86c0-.19-.01-.35-.04-.48s-.07-.23-.12-.31-.11-.14-.19-.17-.16-.05-.25-.05-.18.02-.25.05-.14.09-.19.17-.09.18-.12.31-.04.29-.04.48v.97c0 .19.01.35.04.48s.07.24.12.32.11.14.19.17.16.05.25.05.18-.02.25-.05.14-.09.19-.17.09-.19.11-.32.04-.29.04-.48v-.97z",
  forward10: "M18 13c0 3.31-2.69 6-6 6s-6-2.69-6-6 2.69-6 6-6v4l5-5-5-5v4c-4.42 0-8 3.58-8 8s3.58 8 8 8 8-3.58 8-8h-2zm-7.46 2.22c-.06.05-.12.09-.2.12s-.17.04-.27.04a.69.69 0 0 1-.45-.15c-.06-.05-.1-.1-.13-.17s-.05-.14-.05-.22h-.85c0 .21.04.39.12.55s.19.28.33.38.29.18.46.23.35.07.53.07c.21 0 .41-.03.6-.08s.34-.14.48-.24.24-.24.32-.39.12-.33.12-.53c0-.23-.06-.44-.18-.61s-.3-.3-.54-.39c.1-.05.2-.1.28-.17s.15-.14.2-.22.1-.16.13-.25.04-.18.04-.27c0-.2-.04-.37-.11-.53s-.17-.28-.3-.38-.28-.18-.46-.23-.37-.08-.59-.08c-.19 0-.38.03-.54.08s-.32.13-.44.23-.23.21-.3.34-.11.29-.11.45h.85c0-.07.02-.14.05-.2s.06-.11.11-.15.1-.08.16-.1.13-.04.2-.04c.17 0 .3.04.38.13s.13.21.13.36c0 .07-.01.14-.03.2s-.05.11-.1.16-.1.08-.17.1-.15.04-.25.04h-.45v.65h.45c.1 0 .19.01.27.03s.15.06.2.11.1.11.13.18.04.15.04.24c0 .17-.05.3-.15.39zm3.89-2.3c-.08-.17-.18-.32-.3-.44s-.27-.21-.44-.26-.36-.09-.57-.09-.39.03-.57.09-.33.15-.45.26-.23.27-.3.44-.08.38-.08.62v.74c0 .25.02.46.08.63s.18.33.3.44.27.21.45.27.36.08.57.08.39-.03.57-.09.32-.15.44-.27.23-.27.3-.44.08-.38.08-.62v-.74c0-.24-.03-.45-.08-.62zm-.81 1.5c0 .17-.01.31-.03.42s-.05.2-.1.27-.1.11-.16.14-.14.04-.22.04-.15-.01-.22-.04-.12-.07-.16-.14-.08-.16-.1-.27-.04-.25-.04-.42v-.97c0-.17.01-.31.04-.42s.06-.2.1-.26.1-.11.16-.14.13-.04.21-.04.15.01.22.04.12.07.16.14.08.15.1.26.04.25.04.42v.97z",
  skipNext: "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
  close: "M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z",
  download: "M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z",
  eye: "M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
};
const tts = () => S.snap?.tts || null;  // the settings (never a key), from the snapshot or the last action
const ttsReady = () => { const t = tts(); return !!(t && t.available?.length && t.playback); };
const ttsShowPlay = () => ttsReady() && !!tts().show_play;
const ttsLoaded = (st = TT.st) => ["playing", "paused", "ended"].includes(st.st);
const ttsBusy = (st = TT.st) => st.st === "generating" || ttsLoaded(st);
const fmtClock = (s) => { s = Math.max(0, Math.round(s || 0)); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, "0")}`; };
const RATE_TXT = (r) => `${+(+r).toFixed(2)}×`;

function ttsPos() {  // where the audio is now: the last position the console gave, and the time since, at its speed
  const st = TT.st;
  if (!ttsLoaded(st)) return 0;
  if (st.st !== "playing") return st.pos || 0;
  return Math.min(st.dur || 0, (st.pos || 0) + ((performance.now() - TT.at) / 1000) * (st.rate || 1));
}
function ttsSet(st) {
  if (!st || typeof st !== "object" || !("st" in st)) return;
  const was = TT.st;
  TT.st = st; TT.at = performance.now();
  if (st.v !== undefined) TT.lastV = Math.max(TT.lastV, st.v);
  if (st.cfg && S.snap) { S.snap.tts = st.cfg; TT.cfgV = st.cfg_v; ttsCfgChanged(); ttsDownNews(st.cfg); }
  else if (st.cfg_v) TT.cfgV ||= st.cfg_v;
  if (st.error && st.error.at !== was.error?.at && st.error.id) toast(`Reading aloud failed: ${st.error.err}`, true);
  ttsPaint();
  ttsSchedule();
}
function ttsSchedule() {  // every second while something is loaded or being made; otherwise /api/pending says when
  clearTimeout(TT.timer);
  const job = tts()?.local?.job && !tts().local.job.done;
  if (ttsBusy() || job) TT.timer = setTimeout(ttsFetch, ttsBusy() ? 1000 : 1500);
  cancelAnimationFrame(TT.raf);
  if (TT.st.st === "playing") { const tick = () => { ttsPaintTime(); TT.raf = requestAnimationFrame(tick); }; TT.raf = requestAnimationFrame(tick); }
}
async function ttsFetch() {
  if (!token) return;
  try { ttsSet(await api(`/api/tts_state?cv=${TT.cfgV}`)); } catch { ttsSchedule(); }
}
async function ttsCtl(op, value) {
  const r = await action({ action: "tts_ctl", op, ...(value !== undefined ? { value } : {}) });
  if (!r.ok) { toast("Player: " + (r.err || "?"), true); return; }
  ttsSet(r.state);
}
async function ttsPlay(id, btn) {
  if (TT.st.id === id && ttsLoaded()) { ttsCtl(TT.st.st === "playing" ? "pause" : "play"); return; }
  busy(btn, true);
  const r = await action({ action: "tts_play", id, manual: true });
  busy(btn, false);
  if (!r.ok) { toast("Reading aloud: " + (r.err || "?"), true); return; }
  ttsSet(r.state);
  setTimeout(ttsFetch, 300);
}

// ---------------------------------------------------------------- the player: in the open message, and the bar under every view
// One player per place, painted from TT.st: the open message shows its own when it is the one loaded (else its Listen
// button); the bar shows whatever plays when the open message is not it.
function ttsControls(where) {
  const b = (k, icon, label, onclick, cls = "") => h("button", { class: `btn icon ripple tts-${k} ${cls}`, type: "button", "aria-label": label, title: label, onclick }, svg(icon));
  const seek = h("input", { type: "range", class: "tts-seek", min: "0", max: "1", step: "0.1", value: "0", "aria-label": "Position in the message",
    oninput: (e) => { TT.scrub = +e.target.value; ttsPaintTime(); },
    onchange: (e) => { TT.scrub = null; ttsCtl("seek", +e.target.value); } });
  const rate = h("select", { class: "tts-rate", "aria-label": "Speed", title: "Speed (the pitch stays)", onchange: (e) => ttsCtl("rate", +e.target.value) },
    (tts()?.rates || [0.75, 1, 1.25, 1.5, 1.75, 2]).map((r) => h("option", { value: String(r) }, RATE_TXT(r))));
  return h("div", { class: "tts-ctl", role: "group", "aria-label": where === "bar" ? "Player" : "Player of this message" },
    b("back", TTS_ICON.replay10, "Back 10 seconds", () => ttsCtl("seek_by", -10)),
    b("toggle", ICON.play, "Play", () => ttsCtl("toggle"), "tts-main"),
    b("fwd", TTS_ICON.forward10, "Forward 10 seconds", () => ttsCtl("seek_by", 10)),
    h("span", { class: "tts-time mono", "aria-hidden": "true" }, "0:00"), seek, h("span", { class: "tts-dur mono", "aria-hidden": "true" }, "0:00"),
    rate,
    b("next", TTS_ICON.skipNext, "Next message read aloud", () => ttsCtl("next"), "tts-next"),
    b("stop", ICON.stop, "Stop", () => ttsCtl("stop")));
}
function ttsBlock(m) {  // the open message's place: its Listen button, or its player when it is loaded
  if (!m || !ttsShowPlay()) return null;
  return h("div", { class: "tts", dataset: { mid: m.id } },
    h("button", { class: "btn tonal small ripple tts-listen", type: "button", title: "Read this message aloud (p)",
      onclick: (e) => ttsPlay(m.id, e.currentTarget) }, svg(TTS_ICON.speaker), h("span", {}, "Listen")),
    h("a", { class: "btn icon ripple tts-dl", href: "#", "aria-label": "Download the audio", title: "Download the audio (made now if it is not yet)",
      onclick: (e) => { e.preventDefault(); ttsDownload(m, e.currentTarget); } }, svg(TTS_ICON.download)),
    h("span", { class: "tts-gen dim", role: "status" }),
    ttsControls("message"));
}
async function ttsDownload(m, btn) {
  busy(btn, true);
  try {
    const r = await fetch(`/api/tts_audio?id=${encodeURIComponent(m.id)}`, { headers: { "X-Console-Token": token } });
    if (!r.ok) { toast("Audio: " + ((await r.json().catch(() => ({}))).error || r.status), true); return; }
    const url = URL.createObjectURL(await r.blob());
    const a = h("a", { href: url, download: `${(m.subject || m.id).replace(/[^\w.-]+/g, "_").slice(0, 60)}.wav` });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  } finally { busy(btn, false); }
}
function ttsPaintOne(el, here) {
  const st = TT.st, mine = here === null ? true : st.id === here;
  const loaded = mine && ttsLoaded(st), gen = mine && st.st === "generating";
  el.classList.toggle("on", loaded);
  const listen = el.querySelector(".tts-listen");
  if (listen) { listen.hidden = loaded; listen.classList.toggle("loading", gen); listen.disabled = gen; }
  const g = el.querySelector(".tts-gen");
  if (g) g.textContent = gen ? ttsMaking(st, "Making the audio") : "";
  el.title = loaded && ttsFallback(st) ? `Read by ${ttsName(st.engine)}: ${ttsName(st.preferred)} was unavailable` : "";
  const ctl = el.querySelector(".tts-ctl");
  ctl.hidden = !loaded;
  if (!loaded) return;
  const tg = el.querySelector(".tts-toggle"), playing = st.st === "playing";
  tg.replaceChildren(svg(playing ? TTS_ICON.pause : ICON.play));
  tg.setAttribute("aria-label", playing ? "Pause" : st.st === "ended" ? "Play again" : "Play"); tg.title = tg.getAttribute("aria-label") + " (p)";
  const seek = el.querySelector(".tts-seek");
  seek.max = String(Math.max(0.1, st.dur || 0));
  el.querySelector(".tts-dur").textContent = fmtClock(st.dur);
  const rate = el.querySelector(".tts-rate");
  if (document.activeElement !== rate) {
    const v = String(+(+st.rate || 1).toFixed(2));
    if (![...rate.options].some((o) => o.value === v)) rate.append(h("option", { value: v }, RATE_TXT(v)));
    rate.value = v;
  }
  const nx = el.querySelector(".tts-next");
  nx.hidden = !(st.queue || []).length;
  if (!nx.hidden) { nx.title = `Next message read aloud (${st.queue.length} waiting)`; nx.setAttribute("aria-label", nx.title); }
}
const ttsName = (e) => tts()?.names?.[e] || e || "?";
const ttsFallback = (st) => !!st.engine && !!st.preferred && st.engine !== st.preferred;
function ttsMaking(st, what) {  // "Making the audio with Local (Piper), Mistral (Voxtral) unavailable… 2/5"
  const by = ttsFallback(st) ? ` with ${ttsName(st.engine)} (${ttsName(st.preferred)} unavailable)` : "";
  return `${what}${by}…` + (st.gen?.total > 1 ? ` ${st.gen.done}/${st.gen.total}` : "");
}
function ttsPaintTime() {
  const pos = TT.scrub ?? ttsPos(), dur = TT.st.dur || 0;
  for (const el of $$(".tts.on, #tts-bar.on")) {
    const seek = el.querySelector(".tts-seek");
    if (TT.scrub === null && document.activeElement !== seek) seek.value = String(pos);
    seek.style.setProperty("--p", `${dur ? (pos / dur) * 100 : 0}%`);
    seek.setAttribute("aria-valuetext", `${fmtClock(pos)} of ${fmtClock(dur)}`);
    el.querySelector(".tts-time").textContent = fmtClock(pos);
  }
}
function ttsPaint() {
  for (const el of $$("#detail .tts")) ttsPaintOne(el, el.dataset.mid);
  const bar = $("#tts-bar"), st = TT.st;
  const inMsg = RD.open && RD.open === st.id && !$("#reader").hidden && !$("#view-messages").hidden && $("#detail .tts");
  const show = (ttsLoaded(st) || st.st === "generating") && !inMsg;
  bar.hidden = !show;
  document.body.classList.toggle("tts-bar-on", show);
  if (show) {
    ttsPaintOne(bar, null);
    bar.classList.toggle("on", ttsLoaded(st));
    const t = $("#tts-bar-title");
    t.textContent = st.title || "…";
    t.title = "Open the message";
    $("#tts-bar-sub").textContent = st.st === "generating" ? ttsMaking(st, "making the audio")
      : [st.project && `Project ${st.project}`, st.who, ttsFallback(st) && `read by ${ttsName(st.engine)}`].filter(Boolean).join(" · ");
    $("#tts-bar .tts-ctl").hidden = !ttsLoaded(st);
    $("#tts-bar-close").hidden = !ttsBusy(st);
  }
  ttsPaintTime();
}
$("#tts-bar-title").addEventListener("click", () => TT.st.id && openMessage(TT.st.id));
$("#tts-bar-close").append(svg(TTS_ICON.close));
$("#tts-bar-close").addEventListener("click", () => ttsCtl("stop"));
$("#tts-bar-icon").append(svg(TTS_ICON.speaker));
$("#tts-bar-close").before(ttsControls("bar"));
$("#tts-bar .tts-stop").remove();  // the bar's ✕ stops
// p: play or pause: the open message, else what is loaded
document.addEventListener("keydown", (e) => {
  if (e.key !== "p" || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || document.querySelector("dialog[open]")) return;
  if (e.target.closest?.('input, textarea, select, [contenteditable], [role="menu"], #tree')) return;
  const open = RD.open && !$("#reader").hidden && !$("#view-messages").hidden ? RD.open : null;
  if (open && ttsShowPlay() && TT.st.id !== open) { e.preventDefault(); ttsPlay(open, $("#detail .tts-listen")); }
  else if (ttsLoaded()) { e.preventDefault(); ttsCtl("toggle"); }
});

// ---------------------------------------------------------------- read by itself: the items the operator chose (menus and Settings)
const TTS_TYPE = { box: "box", project: "project", machine: "computer", server: "service" };
const ttsRuleKey = (type, key) => `${type}:${key}`;
const ttsRuleOn = (type, key) => !!tts()?.auto?.[ttsRuleKey(type, key)];
function ttsNodeRule(node) {  // a tree item as a reading rule: {type, key, label}, or null
  if (!node?.key) return null;
  if (node.type === "box") {
    const b = S.boxes.get(node.key);
    return b && b.role !== "operator" ? { type: "box", key: b.id, label: isProjectBox(b) ? `${b.work || b.label} (project box)` : b.role === "machine" ? `${b.label || b.id} (machine box)` : boxName(b.id) } : null;
  }
  if (node.type === "machine") return { type: "machine", key: node.key, label: machineName(node.key) };
  if (node.type === "server") return { type: "server", key: node.key, label: (S.servers.get(node.key) || {}).name || node.key };
  if (node.type === "project") { const [mk, wp] = node.key.split("\u0000"); return { type: "project", key: node.key, label: `${wp || "(no project)"} on ${machineName(mk)}` }; }
  return null;
}
async function ttsRule(rule, on) {
  const r = await action({ action: "tts_rule", ...rule, on });
  if (!r.ok) { toast("Failed: " + (r.err || "?"), true); return; }
  S.snap.tts = r.tts; ttsCfgChanged();
  const what = `${TTS_TYPE[rule.type][0].toUpperCase()}${TTS_TYPE[rule.type].slice(1)} ${nm(rule.label)}`;
  toast(on ? `${what}: what reaches it is read aloud by itself` : `${what}: no longer read aloud`);
}
function ttsMenuItem(node) {  // the ⋮ menu's entry (console.js openRightsMenu)
  const it = $("#rights-tts"), sep = $("#rights-tts-sep"), rule = ttsNodeRule(node);
  it.hidden = sep.hidden = !rule || !ttsReady();
  if (it.hidden) return;
  it.setAttribute("aria-checked", String(ttsRuleOn(rule.type, rule.key)));
  it.dataset.rule = JSON.stringify(rule);
}
$("#rights-tts").addEventListener("click", (e) => {
  const it = e.currentTarget, rule = JSON.parse(it.dataset.rule || "null");
  if (!rule) return;
  const on = it.getAttribute("aria-checked") !== "true";
  it.setAttribute("aria-checked", String(on));
  setTimeout(() => RM.el.hidePopover(), 160);
  ttsRule(rule, on);
});
const ttsTreeMark = (type, key) => (["box", "machine", "server", "project"].includes(type) && ttsRuleOn(type, key))
  ? h("span", { class: "tts-mark", title: "Read aloud by itself (menu ⋮, or Settings)" }, svg(TTS_ICON.speaker, "ico tiny")) : null;

// ---------------------------------------------------------------- Settings: the voices, the keys, the local engine, the rules
const TC = { shown: "" };
function ttsCfgChanged() {
  renderTtsSettings();
  if (RD.open && $("#detail")) {  // the Listen button follows "show the Play button"
    const has = !!$("#detail .tts");
    if (has !== ttsShowPlay()) api("/api/message?id=" + encodeURIComponent(RD.open)).then((m) => { if (!m.error) renderDetail(m, false); }).catch(() => {});
  }
  if (RM.el.matches(":popover-open")) S.treeDirty = true;  // drawn when the menu closes (its trigger stays put)
  else renderTree();
  ttsSchedule();
}
function keyRow(p) {
  const t = tts(), k = t.keys[p] || {}, name = t.names[p];
  const id = `tts-key-${p}`;
  const field = $(`#${id}`);
  const status = $(`#${id}-status`);
  field.placeholder = " ";
  $(`#${id}-clear`).hidden = !k.set;
  $(`#${id}-test`).hidden = !k.set;
  status.classList.toggle("error", k.set && k.ok === false);
  status.textContent = !k.set ? `No ${name} key.` : `Key ${k.hint} saved` + (k.ok === true ? ` · ${k.msg}` : k.ok === false ? ` · ${k.msg}` : "") + (k.at ? ` (tested ${when(Date.parse(k.at))})` : "");
}
function renderTtsSettings() {
  const t = tts();
  if (!t || !$("#set-tts-card")) return;
  const sig = JSON.stringify(t);
  if (sig === TC.shown) return;
  TC.shown = sig;
  keyRow("mistral"); keyRow("gradium");
  // the local engine
  const L = t.local || {}, job = L.job && !L.job.done ? L.job : null;
  $("#tts-local-status").textContent = job ? `${job.step}…` : L.installed
    ? `Installed (Piper ${L.version || ""}, ${fmtBytes(L.size || 0)}): ${(L.voices || []).length} voice(s).`
    : L.can_install === false ? "Python's venv is missing on this computer: sudo apt install python3-venv." : "Not installed.";
  $("#tts-local-err").textContent = L.job?.done && L.job.err ? `Last operation failed: ${L.job.err}` : "";
  const bar = $("#tts-local-bar");
  bar.hidden = !job;
  if (job) bar.firstElementChild.style.width = `${Math.max(3, job.pct || 0)}%`;
  $("#tts-local-install").hidden = !!L.installed; $("#tts-local-install").disabled = !!job || L.can_install === false;
  $("#tts-local-uninstall").hidden = !L.installed; $("#tts-local-uninstall").disabled = !!job;
  const list = $("#tts-local-voices");
  list.replaceChildren(...(L.catalog || []).map((v) => {
    const have = (L.voices || []).includes(v.id);
    return h("div", { class: "tts-voice-row" },
      h("span", { class: "tts-voice-name" }, v.label, h("span", { class: "dim mono" }, ` ${v.id}`)),
      have ? h("span", { class: "chip ok" }, "downloaded") : null,
      L.installed ? h("button", { class: "btn small text ripple", type: "button", disabled: !!job,
        onclick: () => ttsLocal(have ? "remove_voice" : "voices", [v.id]) }, have ? "Remove" : "Download") : null);
  }));
  list.hidden = !L.installed && !job;
  // which engine, which voice
  const av = t.available || [];
  const eng = $("#tts-engine");
  eng.replaceChildren(eng.querySelector("legend"), ...av.map((p) => h("label", {}, h("input", { type: "radio", name: "tts-engine", value: p, checked: t.engine === p }), h("span", {}, t.names[p]))));
  $("#tts-engine-row").hidden = av.length < 2;
  const fb = $("#tts-fallback"), ch = t.chain || [], down = t.down || {}, failed = ch.filter((e) => down[e]);
  const every = Math.round(t.upgrade_every_s || 30);
  fb.classList.toggle("warn", failed.length > 0);
  if (failed.length) {
    const e = failed[0], d = down[e];
    fb.textContent = `${ttsName(e)} failed at ${fmtTime.format(new Date(d.since * 1000))} (${d.err}): ${ttsName(t.engine_now)} reads meanwhile. `
      + `It is tested again only when a message is read, in parallel, at most every ${every} s, and reads again as soon as it works`
      + (d.test_err ? `; last test at ${fmtTime.format(new Date(d.last_test * 1000))}: ${d.test_err}.` : ".");
  } else fb.textContent = ch.length > 1 ? `In this order: ${ch.map(ttsName).join(", then ")}. A cloud voice that fails (quota, key, network) hands over to the next one; `
    + `it is tested again only when a message is read (in parallel, at most every ${every} s) and comes back as soon as it works. A message already read plays its audio again.` : "";
  fb.hidden = !fb.textContent;
  $("#tts-voice-row").hidden = !t.engine;
  for (const lg of ["fr", "en"]) {
    const sel = $(`#tts-voice-${lg}`), cur = (t.voices?.[t.engine] || {})[lg] || "";
    if (document.activeElement === sel) continue;
    if (t.engine === "local") {
      const opts = (L.catalog || []).filter((v) => v.lang === lg && (L.voices || []).includes(v.id));
      sel.replaceChildren(h("option", { value: "" }, `Default (${t.piper_default?.[lg] || "first downloaded"})`), ...opts.map((v) => h("option", { value: v.id }, v.label)));
    } else {
      const vs = (t.voice_list?.[t.engine] || []).filter((v) => !v.lang || v.lang === lg);
      const def = vs.find((v) => v.id === t.voice_default?.[t.engine]?.[lg]);
      sel.replaceChildren(h("option", { value: "" }, def ? `Default: ${def.label}` : "Default voice"), ...vs.map((v) => h("option", { value: v.id }, v.label || v.id)));
      if (cur && !vs.some((v) => v.id === cur)) sel.append(h("option", { value: cur }, cur));
    }
    sel.value = cur;
  }
  // what shows, what reads by itself
  const ready = ttsReady();
  for (const [id, k] of [["#tts-show", "show_play"], ["#tts-auto-op", "auto_operator"]]) {
    const el = $(id);
    el.disabled = !ready;
    if (document.activeElement !== el) el.checked = ready && !!t[k];
  }
  $("#tts-none").hidden = ready;
  $("#tts-none").textContent = !t.playback ? (t.playback_error || "This computer cannot play audio.")
    : "No voice yet: save an API key (Mistral or Gradium) or install the local engine. Until then, the switches below stay off.";
  if (document.activeElement !== $("#tts-rate-default")) $("#tts-rate-default").value = String(+(+t.rate || 1).toFixed(2));
  if (document.activeElement !== $("#tts-max")) $("#tts-max").value = String(t.max_chars || 6000);
  if (document.activeElement !== $("#tts-keep-days")) $("#tts-keep-days").value = String(t.cache_keep_days ?? 30);
  if (document.activeElement !== $("#tts-keep-mb")) $("#tts-keep-mb").value = String(t.cache_max_mb ?? 300);
  if (document.activeElement !== $("#tts-lang")) $("#tts-lang").value = t.lang || "auto";
  const rules = Object.entries(t.auto || {});
  $("#tts-rules-head").hidden = !rules.length && !t.auto_operator;
  $("#tts-rules").replaceChildren(
    ...(t.auto_operator ? [h("li", { class: "tts-rule" }, svg(TTS_ICON.speaker, "ico tiny"), h("span", { class: "tts-rule-name" }, "The operator box", h("span", { class: "dim" }, " · questions, recaps, replies to you")),
      h("button", { class: "btn small text ripple", type: "button", onclick: () => ttsSettings({ auto_operator: false }, "The operator box is no longer read aloud") }, "Turn off"))] : []),
    ...rules.map(([rk, r]) => h("li", { class: "tts-rule" }, svg(TTS_ICON.speaker, "ico tiny"),
      h("span", { class: "tts-rule-name" }, h("span", { class: "uname" }, r.label), h("span", { class: "dim" }, ` · ${TTS_TYPE[r.type] || r.type}`)),
      r.type === "box" && S.boxes.has(r.key) ? h("button", { class: "btn small text ripple", type: "button", onclick: () => gotoBox(r.key) }, "Show") : null,
      h("button", { class: "btn small text ripple", type: "button", onclick: () => ttsRule({ type: r.type, key: r.key, label: r.label }, false) }, "Turn off"))));
  const c = t.cache || {};
  $("#tts-cache").textContent = c.files ? `${c.files} message audio file(s) kept: ${fmtBytes(c.bytes || 0)} of ${fmtBytes((t.cache_max_mb ?? 300) * 1e6)}.` : "No audio kept yet.";
  $("#tts-cache-clear").hidden = !c.files;
}
async function ttsSettings(set, done) {
  const r = await action({ action: "tts_settings", set });
  if (!r.ok) { toast("Failed: " + (r.err || "?"), true); return; }
  S.snap.tts = r.tts; ttsCfgChanged();
  if (done) toast(done);
}
async function ttsLocal(op, voices) {
  if (op === "uninstall" && !(await confirmAsk("Uninstall the local engine?", "Piper, its private Python environment and every downloaded voice are removed from this computer. The cloud voices (API keys) stay.", "Uninstall"))) return;
  const r = await action({ action: "tts_local", op, voices });
  if (!r.ok) { toast("Local engine: " + (r.err || "?"), true); return; }
  S.snap.tts = r.tts; TC.shown = ""; ttsCfgChanged();
  setTimeout(ttsFetch, 500);
}
for (const p of ["mistral", "gradium"]) {
  const id = `tts-key-${p}`;
  $(`#${id}-form`).addEventListener("submit", async (e) => {
    e.preventDefault();
    const field = $(`#${id}`), btn = $(`#${id}-save`), val = field.value.trim();
    if (!val) { field.focus(); toast("Paste the API key first", true); return; }
    busy(btn, true);
    const r = await action({ action: "tts_key", provider: p, key: val });
    busy(btn, false);
    if (r.tts) { S.snap.tts = r.tts; TC.shown = ""; ttsCfgChanged(); }
    if (!r.ok) { toast(`${tts()?.names?.[p] || p}: ${r.err || "?"}`, true); return; }
    field.value = "";
    toast(r.tested ? `${tts().names[p]}: key saved and accepted` : `${tts().names[p]}: key saved, but the test failed: ${r.out}`, !r.tested);
  });
  $(`#${id}-test`).addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    busy(btn, true);
    const r = await action({ action: "tts_key_test", provider: p });
    busy(btn, false);
    if (r.tts) { S.snap.tts = r.tts; TC.shown = ""; ttsCfgChanged(); }
    toast(r.ok ? `${tts().names[p]}: ${r.out}` : `${tts()?.names?.[p] || p}: ${r.err || "?"}`, !r.ok);
  });
  $(`#${id}-clear`).addEventListener("click", async () => {
    if (!(await confirmAsk(`Remove the ${tts().names[p]} key?`, "It is erased from this computer's keyring. Messages already read keep their audio.", "Remove"))) return;
    const r = await action({ action: "tts_key_clear", provider: p });
    if (r.tts) { S.snap.tts = r.tts; TC.shown = ""; ttsCfgChanged(); }
    toast(r.ok ? "Key removed" : "Failed: " + (r.err || "?"), !r.ok);
  });
  $(`#${id}-reveal`).append(svg(TTS_ICON.eye));
  $(`#${id}-reveal`).addEventListener("click", (e) => {
    const f = $(`#${id}`), show = f.type === "password";
    f.type = show ? "text" : "password";
    e.currentTarget.setAttribute("aria-pressed", String(show));
    e.currentTarget.setAttribute("aria-label", show ? "Hide the key" : "Show the key");
  });
}
$("#tts-local-install").addEventListener("click", () => ttsLocal("install"));
$("#tts-local-uninstall").addEventListener("click", () => ttsLocal("uninstall"));
$("#tts-engine").addEventListener("change", (e) => e.target.name === "tts-engine" && ttsSettings({ engine: e.target.value }, `Voices of ${tts().names[e.target.value]}`));
for (const lg of ["fr", "en"]) $(`#tts-voice-${lg}`).addEventListener("change", (e) => ttsSettings({ voice: { engine: tts().engine, lang: lg, voice: e.target.value } }, "Voice saved"));
$("#tts-show").addEventListener("change", (e) => ttsSettings({ show_play: e.target.checked }, e.target.checked ? "Each message shows a Listen button" : "No Listen button in the messages"));
$("#tts-auto-op").addEventListener("change", (e) => ttsSettings({ auto_operator: e.target.checked }, e.target.checked ? "What reaches the operator box is read aloud by itself" : "The operator box is no longer read aloud"));
$("#tts-rate-default").addEventListener("change", (e) => ttsSettings({ rate: +e.target.value }));
$("#tts-max").addEventListener("change", (e) => { const v = Math.round(+e.target.value); if (v >= 300 && v <= 100000) ttsSettings({ max_chars: v }, "Saved"); else toast("Between 300 and 100,000 characters", true); });
$("#tts-keep-days").addEventListener("change", (e) => {
  const v = +e.target.value;
  if (v >= 0.04 && v <= 3650) ttsSettings({ cache_keep_days: v }, `Audio kept ${v} day(s) after it was last played`);
  else { toast("Between 0.04 (one hour) and 3650 days", true); e.target.value = String(tts()?.cache_keep_days ?? 30); }
});
$("#tts-keep-mb").addEventListener("change", (e) => {
  const v = Math.round(+e.target.value);
  if (v >= 10 && v <= 1000000) ttsSettings({ cache_max_mb: v }, `Audio kept: ${fmtBytes(v * 1e6)} at most`);
  else { toast("Between 10 MB and 1,000,000 MB", true); e.target.value = String(tts()?.cache_max_mb ?? 300); }
});
$("#tts-lang").addEventListener("change", (e) => ttsSettings({ lang: e.target.value }, "Saved"));
$("#tts-cache-clear").addEventListener("click", async (e) => {
  const btn = e.currentTarget; busy(btn, true);
  const r = await action({ action: "tts_cache_clear" }); busy(btn, false);
  if (r.tts) { S.snap.tts = r.tts; TC.shown = ""; ttsCfgChanged(); }
  toast(r.ok ? r.out : "Failed: " + (r.err || "?"), !r.ok);
});
// the rate select of the Settings card: the same speeds as the player
$("#tts-rate-default").replaceChildren(...[0.75, 1, 1.25, 1.5, 1.75, 2].map((r) => h("option", { value: String(r) }, RATE_TXT(r))));

// a cloud voice that failed, or came back: said once in a toast (Settings says it for good)
function ttsDownNews(cfg) {
  const now = new Set(Object.keys(cfg?.down || {}));
  if (TT.down) {
    for (const e of now) if (!TT.down.has(e)) toast(`${ttsName(e)} unavailable (${String(cfg.down[e].err || "").replace(/^\w+( \(\w+\))?: /, "")}): ${ttsName(cfg.engine_now)} reads meanwhile`, true);
    for (const e of TT.down) if (!now.has(e)) toast(`${ttsName(e)} works again: it reads the next messages`);
  }
  TT.down = now;
}

// ---------------------------------------------------------------- what console.js calls
function ttsOnSnap(snap) {  // each new snapshot: its settings (/api/snapshot computes them at each request)
  if (snap.tts_cfg_v) TT.cfgV = snap.tts_cfg_v;
  ttsDownNews(snap.tts);
  renderTtsSettings();
  if (TT.lastV < 0) ttsFetch();
}
function ttsPending(r) {  // every 4 s: the player changed (a message read by itself started, or ended)
  if (r.tts_v === undefined || r.tts_v === TT.lastV) return;
  TT.lastV = r.tts_v;
  if (r.tts_st !== TT.st.st || ttsBusy()) ttsFetch();
}
