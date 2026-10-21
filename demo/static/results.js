// The results page: test-set cards, an interactive model comparison, lesson charts and a typed
// training log, drawn from /static/results.json (a snapshot of the latest evaluation). Needs site.js.

const MODELS = ["base", "ft", "mbase", "mft"];
const NAME = { base: "English base", ft: "English fine-tuned", mbase: "multilingual base", mft: "multilingual fine-tuned" };
const FILL = { base: "checker", ft: "solid", mbase: "dots", mft: "hatch" };
const TAB = { real: "real prompts", erp_multi: "multilingual", erp_en: "English", harmful: "harmful" };
const SHORT = { erp_en: "hand-reviewed synthetic ERP prompts", erp_multi: "the English set, translations and public injections",
  harmful: "public safety data, test splits only", real: "visitors' prompts, hand-labelled, never trained on" };

// ---- Test sets: one card each, with the attack/harmless split drawn as a bar ----
function renderSets(sets) {
  $("sets").innerHTML = sets.map(s => `<div class="win stat"><div class="win-title"><span>${esc(s.key)}</span></div>
    <div class="win-body inverse">
      <div><span class="t-dim">$</span> wc -l ${esc(s.key)}</div>
      <div class="val">${s.rows}</div><div class="lbl">${esc(s.name)}</div>
      <div class="split" aria-label="${s.attacks} attacks, ${s.harmless} harmless"><b style="width:${100 * s.attacks / s.rows}%"></b><s style="width:${100 * s.harmless / s.rows}%"></s></div>
      <div class="split-key"><span><i class="a"></i>${s.attacks} attacks</span><span><i class="h"></i>${s.harmless} harmless</span></div>
      <div>${s.langs.split(" · ").map(l => `<span class="lang">${esc(l)}</span>`).join("")}</div>
      <small>${esc(SHORT[s.key])}</small>
    </div></div>`).join("");
}

// ---- Model by model: tabs pick a test set and the bars print again ----
function renderCompare(s) {
  const m = s.models, box = $("compare");
  $("setname").textContent = s.key;
  const group = (name, rows) => `<div class="group"><div class="gname">${name}</div>${rows.join("")}</div>`;
  box.innerHTML =
    group(`attacks caught, out of ${s.attacks}`, MODELS.map(id => vizRow(NAME[id], m[id].caught / s.attacks, `${m[id].caught}`, FILL[id]))) +
    group(`harmless prompts let through, out of ${s.harmless}`, MODELS.map(id => vizRow(NAME[id], m[id].allowed / s.harmless, `${m[id].allowed}`, FILL[id]))) +
    group("right category, out of seven", MODELS.map(id => vizRow(NAME[id], m[id].category_acc, `${Math.round(100 * m[id].category_acc)}%`, FILL[id])));
  [...box.querySelectorAll(".gname, .row")].forEach((el, i) => { el.classList.add("print"); el.style.animationDelay = `${i * 40}ms`; });
  // Restart the bar fill: drop .in, then add it back on the next frame.
  box.classList.remove("in");
  requestAnimationFrame(() => requestAnimationFrame(() => box.classList.add("in")));
}
function renderTabs(sets) {
  const order = ["real", "erp_multi", "erp_en", "harmful"];
  $("tabs").innerHTML = order.map(k => `<button type="button" class="choice" data-set="${k}" aria-pressed="false">${TAB[k]}</button>`).join("");
  const pick = key => {
    for (const b of $("tabs").children) b.setAttribute("aria-pressed", b.dataset.set === key);
    renderCompare(sets.find(s => s.key === key));
  };
  $("tabs").addEventListener("click", e => { const b = e.target.closest("[data-set]"); if (b) pick(b.dataset.set); });
  pick("real");
}

// ---- What we learned: each point gets the chart form that fits it ----
function renderNotes(sets) {
  const set = k => sets.find(s => s.key === k);
  const real = set("real"), en = set("erp_en"), multi = set("erp_multi");
  const p = x => Math.round(100 * x);
  const notes = [
    ["real-vs-synthetic", "Real prompts are harder",
      chartHTML({type: "dumbbell", unit: "%", min: 50, max: 100, before: "synthetic test", after: "real prompts",
        rows: [["English", p(en.models.ft.category_acc), p(real.models.ft.category_acc)],
               ["multilingual", p(multi.models.mft.category_acc), p(real.models.mft.category_acc)]]}),
      "right category, fine-tuned models"],
    ["what-tuning-buys", "Fine-tuning teaches the ERP",
      chartHTML({type: "slope", left: "base", right: "fine-tuned", min: 50, max: 100,
        rows: [["English", p(en.models.base.area_acc), p(en.models.ft.area_acc)],
               ["multilingual", p(multi.models.mbase.area_acc), p(multi.models.mft.area_acc)]]}),
      "right ERP area, English and multilingual test sets"],
    ["multilingual-base", "The adapter stops false alarms",
      chartHTML({type: "waffles", items: [["multilingual base", multi.models.mbase.allowed, multi.harmless],
                                          ["fine-tuned", multi.models.mft.allowed, multi.harmless]]}),
      "harmless prompts let through; each square is 1%"],
    ["role-matters", "Text alone misses snooping",
      `<div class="units"><div class="big">2<small> / 20</small></div>${matrix(20, 2, 20)}</div>`,
      "out-of-role requests caught by off-the-shelf models, so the role is now part of every input"],
    ["harm-needs-contrast", "Harm needs safe examples too",
      chartHTML({type: "scatter", x: "harmless kept", y: "attacks caught", min: 40, max: 100,
        points: [["no safe examples", 50, 92.5, false, "above"], ["one safe per harmful", 83.3, 60], ["one per three ✓", 66.7, 87.5, true]]}),
      "English adapter on real prompts, three training mixes"],
    ["serving", "bfloat16: same verdicts, half the cost",
      chartHTML({type: "deltas", items: [["memory", "3.8", "2.3", "GB", "▼ 38%"], ["time per prompt", "0.85", "0.39", "s", "2.2× faster"]]}),
      "int8 was tried first; it broke both models"],
  ];
  $("notes").innerHTML = notes.map(([key, head, chart, caption]) => `<div class="win"><div class="win-title"><span>${key}</span></div>
    <div class="win-body inverse"><div class="headline">${esc(head)}</div><div class="viz">${chart}</div><p class="caption dim">${esc(caption)}</p></div></div>`).join("");
  watch($("notes").querySelectorAll(".viz"));
}

// ---- Method, as the log of the runs that produced these numbers ----
typeLog($("log"), [
  "$ promptcheck train --base GLiNER2.5-Decide --lora-rank 16 --epochs 5",
  "  data    ~680 prompts · en · 7 categories, 7 areas, 8 intents",
  "  device  AMD Radeon RX 9070 XT · ROCm 7.2 · fp16 mixed precision",
  "  done    ~3 min [ok]",
  "$ promptcheck train --base GLiNER2.5-multi-Decide --lora-rank 16 --epochs 8",
  "  data    ~1,770 prompts · en fr es de pt",
  "  done    ~5 min [ok]",
  "$ promptcheck eval --held-out --labels all",
  "  guard   training refuses test files and drops any prompt found in one",
  "  sets    erp_en · erp_multi · harmful · real",
  "# caveat: the real set is 58 prompts from a handful of testers",
  "# caveat: translations are tidier than real typing",
  "# caveat: jailbreak, secret and system-prompt tests are English only",
]);

(async () => {
  const r = await (await fetch("/static/results.json")).json();
  renderSets(r.sets);
  renderTabs(r.sets);
  renderNotes(r.sets);
})();
