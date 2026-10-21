// Shared by every page: helpers, Hoot the mascot, scroll reveals and the menu bar.
// Page scripts load after this one and use these globals.

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const $ = id => document.getElementById(id);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Links shared before the site had pages pointed at sections of the one page.
const MOVED = { "#try": "/playground#try", "#score": "/playground#score", "#labels": "/playground#labels",
  "#about": "/about", "#how": "/#how" };
if (location.pathname === "/" && MOVED[location.hash] && MOVED[location.hash] !== "/" + location.hash) {
  location.replace(MOVED[location.hash]);
}

// ---- The mascot: Hoot, a night-watch owl in a guard's cap. '#' is ink, 'o' is paper.
// Faces swap a few rows of the neutral sprite. ----
const BOT = [
  "......########......",
  ".....####oo####.....",
  ".....##########.....",
  "..################..",
  "....############....",
  "...##ooo####ooo##...",
  "...#ooooo##ooooo#...",
  "...#oo#oo##oo#oo#...",
  "...#ooooo##ooooo#...",
  "...##ooo#oo#ooo##...",
  "....#####oo#####....",
  "...##############...",
  "..##o##########o##..",
  "..##o##oooooo##o##..",
  "..##o#oooooooo#o##..",
  "..##o#oo#oo#oo#o##..",
  "...#o##oooooo##o#...",
  "....############....",
  "......##....##......",
  ".....####..####.....",
];
const SHUT = "...##############...";
const FACES = {
  blink: {5: SHUT, 6: SHUT, 7: "...#ooooo##ooooo#...", 8: SHUT},
  think: {5: SHUT, 6: SHUT},
  happy: {5: SHUT, 6: "...###o######o###...", 7: "...##o#o####o#o##...", 8: "...#o###o##o###o#..."},
  stern: {5: SHUT, 6: "...#ooo######ooo#..."},
};
function botSVG(face) {
  const rows = BOT.map((r, y) => (FACES[face] || {})[y] ?? r);
  let rects = "";
  rows.forEach((r, y) => r.replace(/#+|o+/g, (run, x) => {
    rects += `<rect class="${run[0] === "#" ? "k" : "p"}" x="${x}" y="${y}" width="${run.length}" height="1.04"/>`;
  }));
  return `<svg viewBox="0 0 20 20" shape-rendering="crispEdges">${rects}</svg>`;
}
function setFace(el, face) { el.dataset.face = face; el.innerHTML = botSVG(face); }
function say(el, text) { el.textContent = text; el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop"); }
// Each mascot blinks now and then, unless it is pulling a face.
function blinker(el) {
  if (reduced) return;
  const loop = () => setTimeout(() => {
    if (el.dataset.face === "neutral") { el.innerHTML = botSVG("blink"); setTimeout(() => { if (el.dataset.face === "neutral") el.innerHTML = botSVG("neutral"); }, 140); }
    loop();
  }, 2200 + Math.random() * 3000);
  loop();
}

// ---- Scroll reveal. Pages can listen for "revealed" to animate something when it shows. ----
function reveal() {
  const els = document.querySelectorAll(".reveal");
  const done = el => { el.classList.add("in"); document.dispatchEvent(new CustomEvent("revealed", {detail: el})); };
  if (reduced || !("IntersectionObserver" in window)) { els.forEach(done); return; }
  const io = new IntersectionObserver(entries => entries.forEach(e => {
    if (e.isIntersecting) { done(e.target); io.unobserve(e.target); }
  }), {threshold: 0.15});
  els.forEach(el => io.observe(el));
}

// ---- Pictures: block bars, square grids and typed logs. Wrap them in a .viz element and pass
// it to watch() so they animate when they scroll into view. ----
const blocks = (frac, fill = "solid", n = 20) => {
  const on = Math.round(n * Math.max(0, Math.min(1, frac)));
  return `<span class="bar ${fill}">${Array.from({length: n}, (_, i) => `<i class="${i < on ? "on" : ""}" style="--i:${i}"></i>`).join("")}</span>`;
};
const vizRow = (label, frac, text, fill = "solid") =>
  `<div class="row"><span>${esc(label)}</span>${blocks(frac, fill)}<span class="v">${esc(text)}</span></div>`;
const matrix = (total, on, cols = 10) =>
  `<div class="matrix" style="--cols:${cols}">${Array.from({length: total}, (_, i) => `<i class="${i < on ? "on" : ""}" style="--i:${i}"></i>`).join("")}</div>`;

// ---- Chart forms, each chosen for the job (see results.js and changelog.json). Two colours
// only: identity comes from direct labels and fill texture, never from hue. ----
const pctOf = (v, min, max) => 100 * (v - min) / (max - min);

// Before -> after for a few items: hollow square is before, solid is after.
function dumbbell({rows, min = 0, max = 100, unit = "", before = "before", after = "after"}) {
  const row = ([label, a, b]) => {
    const pa = pctOf(a, min, max), pb = pctOf(b, min, max);
    const tip = `${label}: ${a}${unit} → ${b}${unit}`;
    return `<div class="db-row" title="${esc(tip)}"><span>${esc(label)}</span>
      <span class="db-track"><i class="db-line" style="left:${Math.min(pa, pb)}%;width:${Math.abs(pb - pa)}%;transform-origin:${pb >= pa ? "left" : "right"}"></i>
        <i class="db-from" style="left:${pa}%"></i><i class="db-to" style="left:${pb}%"></i></span>
      <span class="v">${a} → ${b}${esc(unit)}</span></div>`;
  };
  return `<div class="db">${rows.map(row).join("")}
    <div class="db-key"><span><i class="db-from"></i>${esc(before)}</span><span><i class="db-to"></i>${esc(after)}</span><span class="axis">${min}–${max}${esc(unit)}</span></div></div>`;
}

// One value per item on a shared, possibly zoomed axis: a dot plot, not bars, so a zoomed
// axis cannot exaggerate lengths.
function dots({rows, min = 0, max = 100, unit = ""}) {
  const ticks = [min, (min + max) / 2, max];
  return `<div class="dp">${rows.map(([label, v]) => `<div class="db-row" title="${esc(`${label}: ${v}${unit}`)}"><span>${esc(label)}</span>
      <span class="db-track"><i class="dp-stem" style="width:${pctOf(v, min, max)}%"></i><i class="db-to" style="left:${pctOf(v, min, max)}%"></i></span>
      <span class="v">${Math.round(v)}${esc(unit)}</span></div>`).join("")}
    <div class="dp-axis"><span></span><span class="ticks">${ticks.map(t => `<b>${t}${esc(unit)}</b>`).join("")}</span><span></span></div></div>`;
}

// Two columns joined by lines: change for a few series between two states.
function slope({rows, left, right, min = 0, max = 100, unit = "%"}) {
  const W = 340, H = 190, x1 = 128, x2 = 252, top = 30, bot = 172;
  const y = v => bot - (bot - top) * (v - min) / (max - min);
  // Keep end labels at least 15 units apart when values are close.
  const spread = ys => { const o = ys.map((v, i) => [v, i]).sort((a, b) => a[0] - b[0]);
    for (let k = 1; k < o.length; k++) o[k][0] = Math.max(o[k][0], o[k - 1][0] + 15); return o.sort((a, b) => a[1] - b[1]).map(z => z[0]); };
  const ly = spread(rows.map(r => y(r[1]))), ry = spread(rows.map(r => y(r[2])));
  // Name the right-hand labels too when lines cross or labels had to be nudged apart;
  // otherwise the reader cannot tell which end belongs to which line.
  const order = k => rows.map((r, i) => [r[k], i]).sort((a, b) => a[0] - b[0]).map(z => z[1]).join();
  const nameRight = order(1) !== order(2) || ry.some((v, i) => Math.abs(v - y(rows[i][2])) > 0.5);
  const lines = rows.map(([name, a, b], i) => `<g><title>${esc(`${name}: ${a}${unit} → ${b}${unit}`)}</title>
      <line class="sl-line" x1="${x1}" y1="${y(a)}" x2="${x2}" y2="${y(b)}" pathLength="1"/>
      <rect class="mk-from" x="${x1 - 5}" y="${y(a) - 5}" width="10" height="10"/><rect class="mk-to" x="${x2 - 5}" y="${y(b) - 5}" width="10" height="10"/>
      <text class="sv" x="${x1 - 12}" y="${ly[i] + 5}" text-anchor="end">${esc(name)} ${a}${unit}</text>
      <text class="sv" x="${x2 + 12}" y="${ry[i] + 5}">${b}${unit}${nameRight ? ` ${esc(name)}` : ""}</text></g>`).join("");
  return `<svg class="chart-svg slope" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(rows.map(r => `${r[0]} ${r[1]} to ${r[2]}${unit}`).join("; "))}">
    <line class="ax" x1="${x1}" y1="${top - 6}" x2="${x1}" y2="${bot + 6}"/><line class="ax" x1="${x2}" y1="${top - 6}" x2="${x2}" y2="${bot + 6}"/>
    <text class="sh" x="${x1}" y="14" text-anchor="middle">${esc(left)}</text><text class="sh" x="${x2}" y="14" text-anchor="middle">${esc(right)}</text>
    ${lines}</svg>`;
}

// A trade-off between two measures: one point per option, the chosen one solid. A point may say
// where its label goes ("above", otherwise beside it) to keep labels clear of other points.
function scatter({points, x, y, min = 40, max = 100}) {
  const W = 320, H = 210, L = 42, R = 300, T = 16, B = 176;
  const px = v => L + (R - L) * (v - min) / (max - min), py = v => B - (B - T) * (v - min) / (max - min);
  const ticks = [min, (min + max) / 2, max];
  return `<svg class="chart-svg scatter" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(points.map(p => `${p[0]}: ${x} ${p[1]}%, ${y} ${p[2]}%`).join("; "))}">
    ${ticks.map(t => `<line class="grid" x1="${L}" y1="${py(t)}" x2="${R}" y2="${py(t)}"/><text class="tk" x="${L - 6}" y="${py(t) + 4}" text-anchor="end">${t}</text>
      <line class="grid" x1="${px(t)}" y1="${T}" x2="${px(t)}" y2="${B}"/><text class="tk" x="${px(t)}" y="${B + 14}" text-anchor="middle">${t}</text>`).join("")}
    <text class="sh" x="${(L + R) / 2}" y="${H - 4}" text-anchor="middle">${esc(x)} %</text>
    <text class="sh" x="12" y="${(T + B) / 2}" text-anchor="middle" transform="rotate(-90 12 ${(T + B) / 2})">${esc(y)} %</text>
    <text class="tk" x="${R}" y="${T + 10}" text-anchor="end">better ↗</text>
    ${points.map(([name, vx, vy, chosen, where], i) => {
      const above = where === "above", left = vx > 80;
      const tx = above ? px(vx) - 8 : px(vx) + (left ? -12 : 12), ty = above ? py(vy) - 12 : py(vy) + 5;
      return `<g class="pt" style="--i:${i}"><title>${esc(`${name}: ${x} ${vx}%, ${y} ${vy}%`)}</title>
        <rect class="${chosen ? "mk-to" : "mk-from"}" x="${px(vx) - 6}" y="${py(vy) - 6}" width="12" height="12"/>
        <text class="sv" x="${tx}" y="${ty}" text-anchor="${left && !above ? "end" : "start"}">${esc(name)}</text></g>`;
    }).join("")}
  </svg>`;
}

// Part of a whole as 100 squares, side by side for comparison.
function waffles({items}) {
  return `<div class="waffles">${items.map(([label, on, total]) => `<div title="${esc(`${label}: ${on} of ${total}`)}">
      ${matrix(100, Math.round(100 * on / total), 10)}<div class="w-lbl"><b>${on}</b> of ${total}</div><div class="w-sub">${esc(label)}</div></div>`).join("")}</div>`;
}

// Before -> after as figures with a change badge: for two or three numbers, no chart needed.
function deltas({items}) {
  return `<div class="deltas">${items.map(([label, a, b, unit, change]) => `<div class="delta">
      <div class="w-sub">${esc(label)}</div><div class="d-val"><span class="was">${esc(a)}</span> → ${esc(b)}<small> ${esc(unit)}</small></div>
      <span class="d-chip">${esc(change)}</span></div>`).join("")}</div>`;
}

// Dashboard dials for infrastructure numbers: the dashed needle is before. The solid one revs
// past after, settles on it, then idles with a small jitter, all in chunky steps.
function gauges({items}) {
  const dial = ([label, before, after, max, unit, chip]) => {
    const cx = 110, cy = 104, r = 84, deg = v => 180 * Math.min(v, max) / max;
    const at = (d, rr) => [cx - rr * Math.cos(d * Math.PI / 180), cy - rr * Math.sin(d * Math.PI / 180)];
    const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => {
      const [x1, y1] = at(180 * f, r - 4), [x2, y2] = at(180 * f, r + 4), [tx, ty] = at(180 * f, r + 16);
      return `<line class="ax" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"/><text class="tk" x="${tx}" y="${ty + 4}" text-anchor="middle">${+(max * f).toFixed(2)}</text>`;
    }).join("");
    return `<div class="gauge" title="${esc(`${label}: ${before} → ${after} ${unit}`)}">
      <svg class="chart-svg" viewBox="0 0 220 128" role="img" aria-label="${esc(`${label}: ${before} to ${after} ${unit}`)}">
        <path class="g-track" d="M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}"/>${ticks}
        <line class="g-old" x1="${cx}" y1="${cy}" x2="${cx - r + 10}" y2="${cy}" style="transform: rotate(${deg(before)}deg)"/>
        <g class="g-jit"><line class="g-new" x1="${cx}" y1="${cy}" x2="${cx - r + 10}" y2="${cy}" style="--a: ${deg(after)}"/></g>
        <rect class="mk-to" x="${cx - 6}" y="${cy - 6}" width="12" height="12"/>
      </svg>
      <div class="w-sub">${esc(label)}</div>
      <div class="d-val"><span class="was">${esc(before)}</span> → ${esc(after)}<small> ${esc(unit)}</small></div>
      <span class="d-chip">${esc(chip)}</span></div>`;
  };
  return `<div class="gauges">${items.map(dial).join("")}</div>
    <div class="db-key"><span><i class="g-key old"></i>float32</span><span><i class="g-key"></i>bfloat16</span></div>`;
}

// Progress across releases: a slope chart with one column per release. A series only has
// points where its model changed; its line runs straight through the other columns. A
// narrower W draws a phone-sized version with bigger relative text and no column subtitles.
function progress({cols, series, min, max, unit = "%", ticks = [], W = 560}) {
  const narrow = W < 480, H = 300, L = narrow ? 46 : 70, R = W - (narrow ? 100 : 90), T = narrow ? 44 : 62, B = 268;
  const x = i => L + (R - L) * i / (cols.length - 1), y = v => B - (B - T) * (v - min) / (max - min);
  const head = cols.map((c, i) => `<text class="sv" x="${x(i)}" y="20" text-anchor="middle">${esc(c.label)}</text>
      ${narrow ? "" : `<text class="sh" x="${x(i)}" y="38" text-anchor="middle">${esc(c.sub)}</text>`}
      <line class="ax" x1="${x(i)}" y1="${T - 12}" x2="${x(i)}" y2="${B + 6}"/>`).join("");
  const grid = ticks.map(t => `<line class="grid" x1="${L - 10}" y1="${y(t)}" x2="${R + 10}" y2="${y(t)}"/>
      <text class="tk" x="${L - 14}" y="${y(t) + 4}" text-anchor="end">${t}${unit}</text>`).join("");
  const lines = series.map(sr => {
    const pts = cols.map((c, i) => [i, sr.points[c.key]]).filter(p => p[1] != null);
    const d = pts.map(([i, v], k) => `${k ? "L" : "M"} ${x(i)} ${y(v)}`).join(" ");
    const [li, lv] = pts[pts.length - 1];
    return `<g class="pg ${sr.style}"><title>${esc(sr.name)}: ${pts.map(([i, v]) => `${cols[i].label} ${v}${unit}`).join(", ")}</title>
      <path class="pg-line" d="${d}" pathLength="1"/>
      ${pts.map(([i, v]) => `<g class="pt" style="--i:${i}"><rect class="${sr.style === "solid" ? "mk-to" : "mk-from"}" x="${x(i) - 6}" y="${y(v) - 6}" width="12" height="12"/>
        <text class="tk" x="${x(i)}" y="${y(v) + (sr.below ? 22 : -12)}" text-anchor="middle">${v}${unit}</text></g>`).join("")}
      <text class="sv" x="${x(li) + 14}" y="${y(lv) + 5}">${esc(sr.name)}</text></g>`;
  }).join("");
  return `<svg class="chart-svg progress" viewBox="0 0 ${W} ${H}" role="img"
    aria-label="${esc(series.map(sr => `${sr.name}: ${cols.filter(c => sr.points[c.key] != null).map(c => `${c.label} ${sr.points[c.key]}${unit}`).join(", ")}`).join("; "))}">
    ${grid}${head}${lines}</svg>`;
}

const CHARTS = {dumbbell, dots, slope, scatter, waffles, deltas, gauges, progress};
const chartHTML = spec => `${spec.title ? `<div class="gname">${esc(spec.title)}</div>` : ""}${CHARTS[spec.type](spec)}`;

// Adds .in to each element when it first scrolls into view, then calls back.
function watch(els, then) {
  els = [...els];
  const done = el => { el.classList.add("in"); then?.(el); };
  if (reduced || !("IntersectionObserver" in window)) { els.forEach(done); return; }
  const io = new IntersectionObserver(entries => entries.forEach(e => {
    if (e.isIntersecting) { done(e.target); io.unobserve(e.target); }
  }), {threshold: 0.2});
  els.forEach(el => io.observe(el));
}

// Types lines into a terminal when it scrolls into view. Lines starting with "#" are comments;
// "[ok]" becomes a small boxed tick.
function typeLog(el, lines) {
  const fmt = t => esc(t).replace("[ok]", '<span class="ok">✓ ok</span>');
  watch([el], async () => {
    for (const text of lines) {
      const d = document.createElement("div");
      if (text.startsWith("#")) d.className = "c";
      el.append(d);
      if (reduced) { d.innerHTML = fmt(text); continue; }
      for (let i = 1; i <= text.length; i++) { d.innerHTML = esc(text.slice(0, i)) + '<span class="cursor"></span>'; await sleep(text.startsWith("$") ? 22 : 6); }
      d.innerHTML = fmt(text);
      await sleep(text.startsWith("$") ? 260 : 60);
    }
  });
}

// ---- What's new, as a git log: one line per release, opened like a commit for the details.
// Charts live above the log, not in it. ----
function logHTML(items, {since = "2026-10-18"} = {}) {
  const when = d => new Date(d + "T12:00:00").toLocaleDateString("en-GB", {day: "numeric", month: "short"});
  return `<div class="win log reveal"><div class="win-title"><span>git log</span></div><div class="win-body inverse">
    <div><span class="t-dim">$</span> git log --oneline --since ${since} <span class="t-dim"># open a line for details</span></div>
    ${items.map(e => `<details class="commit">
      <summary><span class="hash">${esc(e.commit || "")}</span><time datetime="${e.date}">${when(e.date)}</time>
        <span class="lang">${esc(e.tag)}</span><span class="ttl">${esc(e.title)}</span><span class="mk" aria-hidden="true"></span></summary>
      <div class="body"><p>${esc(e.summary)}</p>${e.points ? `<ul>${e.points.map(p => `<li>${esc(p)}</li>`).join("")}</ul>` : ""}</div>
    </details>`).join("")}
  </div></div>`;
}

// ---- Menu bar: the current page, a clock and the prompt count ----
function tick() {
  $("clock-time").textContent = new Date().toLocaleTimeString("en-GB", {hour: "2-digit", minute: "2-digit"});
}
function showCount(total) { $("clock-count").textContent = plural(total, "prompt"); }

for (const a of document.querySelectorAll(".menubar .links a")) {
  if (a.getAttribute("href") === location.pathname) a.setAttribute("aria-current", "page");
}
for (const el of document.querySelectorAll(".bot[data-auto]")) { setFace(el, "neutral"); blinker(el); }
tick(); setInterval(tick, 10000);
reveal();
// The playground refreshes the count itself after each prompt.
fetch("/api/stats").then(r => r.json()).then(s => showCount(s.total)).catch(() => {});
