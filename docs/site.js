// mmbox website: the ripple, the install tabs, copy buttons, the latest release. No dependency.
"use strict";

const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

// ---------------------------------------------------------------- the ripple, centred on the pointer (or the control, from the keyboard)
function ripple(el, x, y) {
  if (reduceMotion.matches) return;
  const r = el.getBoundingClientRect();
  const size = Math.hypot(Math.max(x - r.left, r.right - x), Math.max(y - r.top, r.bottom - y)) * 2;
  const w = document.createElement("span");
  w.className = "ripple-wave";
  w.setAttribute("aria-hidden", "true");
  w.style.width = w.style.height = size + "px";
  w.style.left = x - r.left - size / 2 + "px";
  w.style.top = y - r.top - size / 2 + "px";
  el.querySelectorAll(".ripple-wave").forEach((old, i, all) => { if (i < all.length - 2) old.remove(); });  // 3 waves at most
  el.append(w);
  w.addEventListener("animationend", () => w.remove(), { once: true });
}
document.addEventListener("pointerdown", (e) => {
  const el = e.target.closest(".ripple");
  if (el && !el.disabled && e.button === 0) ripple(el, e.clientX, e.clientY);
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Enter" && e.key !== " ") return;
  const el = e.target.closest?.(".ripple");
  if (el && !el.disabled) {
    const r = el.getBoundingClientRect();
    ripple(el, r.left + r.width / 2, r.top + r.height / 2);
  }
});

// ---------------------------------------------------------------- tabs (WAI-ARIA tabs pattern: roving tabindex, arrows, Home, End)
for (const root of document.querySelectorAll("[data-tabs]")) {
  const tabs = [...root.querySelectorAll('[role="tab"]')];
  const select = (tab, focus) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
  };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t, false));
    t.addEventListener("keydown", (e) => {
      const n = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
      if (n === undefined) return;
      e.preventDefault();
      select(tabs[(n + tabs.length) % tabs.length], true);
    });
  });
  // Windows visitors see their tab first
  if (/Windows/.test(navigator.userAgent)) select(root.querySelector("#tab-win") || tabs[0], false);
}

// ---------------------------------------------------------------- copy buttons on code blocks
const status = document.getElementById("copy-status");
for (const pre of document.querySelectorAll("pre")) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "copy ripple";
  b.textContent = "Copy";
  b.setAttribute("aria-label", "Copy this code");
  b.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(pre.querySelector("code").innerText.replace(/\n$/, ""));
      b.textContent = "Copied";
      b.classList.add("done");
      status.textContent = "Code copied to the clipboard";
    } catch {
      b.textContent = "Select and copy";
      status.textContent = "Copying is not allowed here: select the code and copy it";
    }
    setTimeout(() => { b.textContent = "Copy"; b.classList.remove("done"); }, 2000);
  });
  pre.append(b);
}

// ---------------------------------------------------------------- the latest release, from the GitHub API (the static link stays if it fails)
(async () => {
  const el = document.getElementById("release");
  try {
    const r = await fetch("https://api.github.com/repos/lp177/mmbox/releases/latest", { headers: { Accept: "application/vnd.github+json" } });
    if (!r.ok) return;
    const rel = await r.json();
    const date = new Date(rel.published_at).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
    const a = document.createElement("a");
    a.href = rel.html_url;
    a.append("Latest release ");
    const tag = document.createElement("span");
    tag.className = "tag";
    tag.textContent = rel.tag_name;
    a.append(tag);
    el.replaceChildren(a, ` · ${date} · Apache 2.0 · Python 3.10+`);
  } catch { /* offline, rate-limited: keep the static link */ }
})();
