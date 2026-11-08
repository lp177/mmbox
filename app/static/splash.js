// The start screen's animation: one of the animations of splash-data.js (app/splash/build.py), drawn at random at each
// start, then the next one of the list after three of its loops, until console.js removes the start screen. With
// reduced motion: the first one drawn, still (its CSS shows the fixed logo), and no change.
(() => {
  const list = window.MMBOX_SPLASH || [];
  const host = document.getElementById("splash-anim");
  if (!host || !list.length) return;
  const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)");
  const LOOPS = 3;
  const asked = list.findIndex((a) => a.n === new URLSearchParams(location.search).get("splash"));  // ?splash=NN: that one first
  let i = asked >= 0 ? asked : Math.floor(Math.random() * list.length), timer = 0;
  function show() {
    const a = list[i];
    host.className = "sa sa-a" + a.n;
    host.dataset.anim = a.n;
    host.style.setProperty("--duration", a.d + "s");
    host.innerHTML = a.html;  // the page's own generated file, no outside text
    // the scenes' per-element delays: through the CSSOM (the CSP refuses style attributes in markup)
    host.querySelectorAll("[data-sa-style]").forEach((el) => { el.style.cssText = el.dataset.saStyle; });
    clearTimeout(timer);
    if (reduced?.matches) return;
    timer = setTimeout(() => {
      if (!host.isConnected) return;
      i = (i + 1) % list.length;
      show();
    }, a.d * LOOPS * 1000);
  }
  show();
  reduced?.addEventListener?.("change", show);
})();
