// Make charts fill their box. Side-by-side boxes are stretched to the same height; when one box holds less,
// its registered chart(s) are redrawn taller to use the space instead of leaving an empty gap.
// Charts register with fill(container, draw, opts); call fitAll() after rendering a tab.

const fillers = new Set();

/** Register a chart. draw(height) must render it into `container` at that height. opts: {base, min, max, weight, hideBelow} */
export function fill(container, draw, opts = {}) {
  const f = { container, draw, base: opts.base ?? 200, min: opts.min ?? 120, max: opts.max ?? 900,
              weight: opts.weight ?? 1, hideBelow: opts.hideBelow ?? 0, h: opts.base ?? 200 };
  for (const g of fillers) if (g.container === container) fillers.delete(g);
  fillers.add(f);
  container.style.display = "";
  container.innerHTML = "";
  draw(f.h);
  return f;
}

// Empty space at the bottom of a box: its inner bottom minus the lowest visible element inside it.
function gapOf(card) {
  const cr = card.getBoundingClientRect();
  let bottom = cr.top;
  for (const e of card.querySelectorAll("*")) {
    if (e.offsetParent === null || getComputedStyle(e).position === "absolute") continue;
    const r = e.getBoundingClientRect();
    if (r.width && r.height) bottom = Math.max(bottom, r.bottom);
  }
  return cr.bottom - parseFloat(getComputedStyle(card).paddingBottom || 0) - bottom;
}

function redraw(f, h) {
  f.h = Math.round(h);
  f.container.innerHTML = "";
  f.draw(f.h);
}

/** Grow registered charts into their box's spare height (spread by weight when a box has several). */
export function fitAll() {
  const byCard = new Map();
  for (const f of fillers) {
    if (!f.container.isConnected) { fillers.delete(f); continue; }
    const card = f.container.closest(".card");
    if (!card || card.offsetParent === null) continue;
    if (!byCard.has(card)) byCard.set(card, []);
    byCard.get(card).push(f);
  }
  for (const [card, fs] of byCard) {
    const gap = gapOf(card);
    if (gap < 8) continue;
    const W = fs.reduce((a, f) => a + f.weight, 0);
    for (const f of fs) {
      const target = f.h + (gap * f.weight) / W;
      if (f.hideBelow && target < f.hideBelow) { f.container.style.display = "none"; continue; }
      if (f.container.style.display === "none") f.container.style.display = "";
      redraw(f, Math.max(f.min, Math.min(f.max, target)));
    }
  }
}

/** Back to base heights (before re-fitting, e.g. after a resize changes which boxes sit side by side). */
export function resetAll() {
  for (const f of fillers) if (f.container.isConnected) { f.container.style.display = ""; redraw(f, f.base); }
}

let t;
window.addEventListener("resize", () => {
  clearTimeout(t);
  t = setTimeout(() => { resetAll(); requestAnimationFrame(() => fitAll()); }, 200);
});

/** Fit after the browser has laid out the latest render. */
export const fitSoon = () => requestAnimationFrame(() => requestAnimationFrame(fitAll));
