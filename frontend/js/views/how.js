// How it works: an animated, step-by-step walk of one invoice through the system (for team demos).
import { $, $$, fill, h, icon, s } from "../lib/dom.js";

const STEP_MS = 7000; // at Normal speed; Slow (the default, for presenting) doubles every timing
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)");
const PACE_KEY = "nexusai.how.pace";
let pace = 2;
try { pace = Number(localStorage.getItem(PACE_KEY)) === 1 ? 1 : 2; } catch { /* storage blocked: keep Slow */ }
const stepMs = () => STEP_MS * pace;

const ICONS = {
  docs: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z", "M14 3v5h5M9 13h6M9 17h4"],
  api: ["M4 7h16M4 12h16M4 17h10", "M18 15l3 2-3 2"],
  agentA: ["M12 3l8 4.5v9L12 21l-8-4.5v-9z", "M9.5 14.5 12 8l2.5 6.5M10.3 12.5h3.4"],
  agentB: ["M12 3l8 4.5v9L12 21l-8-4.5v-9z", "M8.5 12.5l2.3 2.3 4.7-5"],
  db: ["M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"],
  ai: ["M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1", "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z"],
  ui: ["M3 13h8V3H3zM13 21h8V11h-8zM3 21h8v-6H3zM13 3v6h8V3z"],
};

const NODES = [
  { id: "docs", name: "Documents", sub: "PDF · image" },
  { id: "api", name: "NexusAI API", sub: "Reads & extracts" },
  { id: "agentA", name: "Agent A", sub: "Coordinator", svc: "coordinator" },
  { id: "agentB", name: "Agent B", sub: "Verifier", svc: "verifier" },
  { id: "db", name: "Supabase", sub: "Saved record", svc: "storage" },
  { id: "ai", name: "AI model", sub: "Explains", svc: "reasoning" },
  { id: "ui", name: "Dashboard", sub: "Team view" },
];
const LINKS = ["upload", "data", "A2A", "save", "explain", "show"];

let model = "AI model";
let provider = "Ollama Cloud";
const STEPS = [
  {
    title: "An invoice and its purchase order arrive",
    lead: "Someone picks a demo pair, a real scanned invoice from Kaggle, or uploads their own PDF or photo.",
    points: ["Invoice: what the supplier says we owe.", "Purchase order (PO): what we agreed to buy.",
      "Real invoices come without a PO, so NexusAI can build a demo PO to compare against."],
    tech: "Demo PDFs · Kaggle dataset · drag-and-drop upload",
    viz: vizDocs,
  },
  {
    title: "The documents are read",
    lead: "The API turns each document into clean data: invoice number, supplier, currency, line items and totals.",
    points: ["Digital PDFs: the text is read directly.", "Scans and photos: OCR reads the words from the image.",
      "Every value is colour-marked so a person can check anything uncertain."],
    tech: "FastAPI · pdfplumber · Tesseract OCR",
    viz: vizRead,
  },
  {
    title: "Agent A takes the case",
    lead: "The Procurement Coordinator does not check numbers itself. It finds the right specialist and hands the job over.",
    points: ["Finds Agent B by reading its public Agent Card.", "Sends the task over A2A, an open agent-to-agent standard.",
      "Each agent is an independent service that could run on a different machine."],
    tech: "A2A protocol · JSON-RPC SendMessage · /.well-known/agent-card.json",
    viz: vizA2A,
  },
  {
    title: "Agent B verifies every detail",
    lead: "The Invoice Verification Specialist compares invoice and PO with fixed rules. Same input, same answer, every time.",
    points: ["Exact decimal arithmetic with no tolerance.", "Outcome: Match, Discrepancy found, Needs review or Invalid request.",
      "No AI in this step: the rules decide."],
    tech: "Deterministic rule engine · Decimal maths",
    viz: vizChecks,
  },
  {
    title: "The result is saved",
    lead: "Agent B stores the verified result in the database under its task ID, so every decision can be audited later.",
    points: ["One row per review in verification_reviews.", "Agent A's next action and explanation are added to the same row.",
      "History and the dashboard read from here."],
    tech: "Supabase (PostgreSQL) · row-level security",
    viz: vizDb,
  },
  {
    title: "Agent A explains it in plain language",
    lead: "Agent A gathers context, chooses the next action, then asks the AI model to explain the findings, suggest causes and draft an email to the supplier.",
    points: ["Context first: the money at stake and the supplier's track record, including possible duplicate invoices.",
      () => (provider.includes("local")
        ? `Runs locally in Ollama (${model}): invoice data never leaves this computer.`
        : `Runs on ${provider} (${model}), so it also works when NexusAI is deployed online.`),
      "Fact check: every figure the AI writes is compared with the verified data; one wrong number and it must rewrite.",
      "Guardrails: the AI can never approve a payment. If it is unavailable, a built-in writer takes over."],
    tech: () => `${provider} · ${model} · JSON output + guardrails`,
    viz: vizAI,
  },
  {
    title: "The team sees the outcome",
    lead: "The report, charts and history update immediately. A person always makes the final payment decision.",
    points: ["Overview: KPIs, outcome mix, top issues and supplier health.", "History: every saved review, searchable.",
      "NexusAI gives guidance only, never payment approval."],
    tech: "Dashboard · Chart.js · live from Supabase",
    viz: vizUi,
  },
];

let index = 0;
let playing = true;
let timer = null;
let cleanups = [];
let built = false;

// ---------------------------------------------------------------- stage

function buildStage() {
  const stage = $("#how-stage");
  const parts = [];
  NODES.forEach((node, i) => {
    if (i) parts.push(h("div", { class: "s-link", dataset: { i: String(i - 1) }, "aria-hidden": "true" },
      h("span", { class: "s-label" }, LINKS[i - 1]), h("i", { class: "packet" })));
    parts.push(h("button", { type: "button", class: "s-node", dataset: { i: String(i), svc: node.svc || "" },
      "aria-label": `Step ${i + 1}: ${node.name}`, onclick: () => { pause(); go(i); } },
    h("span", { class: "s-dot", title: "" }),
    h("span", { class: "s-icon" }, icon(...ICONS[node.id])),
    h("strong", {}, node.name),
    h("small", { dataset: { sub: node.id } }, node.sub)));
  });
  fill(stage, parts);
}

function paintStage() {
  $$("#how-stage .s-node").forEach((el) => {
    const i = Number(el.dataset.i);
    el.classList.toggle("done", i < index);
    el.classList.toggle("active", i === index);
    el.setAttribute("aria-current", i === index ? "step" : "false");
  });
  $$("#how-stage .s-link").forEach((el) => {
    const i = Number(el.dataset.i);
    el.classList.toggle("done", i < index - 1);
    el.classList.remove("flowing");
    if (i === index - 1) { void el.offsetWidth; el.classList.add("flowing"); }
  });
}

// ---------------------------------------------------------------- explanation

const text = (v) => (typeof v === "function" ? v() : v);

function renderExplain() {
  cleanups.forEach((fn) => fn());
  cleanups = [];
  const step = STEPS[index];
  fill($("#how-explain"),
    h("div", { class: "x-text" },
      h("span", { class: "x-step" }, `Step ${index + 1} of ${STEPS.length}`),
      h("h3", {}, step.title),
      h("p", { class: "x-lead" }, step.lead),
      h("ul", { class: "x-points" }, step.points.map((p, i) => h("li", { style: `--d:${0.15 + i * 0.12}s` }, text(p)))),
      h("p", { class: "x-tech" }, h("span", {}, "Under the hood"), text(step.tech))),
    h("div", { class: "x-viz" }, step.viz()));
  const bar = $("#how-progress");
  bar.classList.remove("run");
  void bar.offsetWidth;
  bar.style.animationDuration = `${stepMs()}ms`;
  bar.classList.toggle("run", playing);
  $("#how-count").textContent = `${index + 1} / ${STEPS.length}`;
}

function go(i) {
  index = (i + STEPS.length) % STEPS.length;
  paintStage();
  renderExplain();
  schedule();
}

function schedule() {
  clearTimeout(timer);
  if (playing) timer = setTimeout(() => go(index + 1), stepMs());
}

function setPlaying(value) {
  playing = value;
  const button = $("#how-play");
  fill(button, value ? icon("M8 5v14M16 5v14") : icon("M7 4l13 8-13 8z"));
  button.setAttribute("aria-label", value ? "Pause" : "Play");
  $("#how-progress").classList.toggle("run", value);
  if (value) go(index); else clearTimeout(timer);
}
const pause = () => setPlaying(false);

// ---------------------------------------------------------------- visuals

function paper(tag, lines, cls = "") {
  return h("div", { class: `paper ${cls}` }, h("span", { class: "paper-tag" }, tag),
    Array.from({ length: lines }, (_, i) => h("i", { style: `width:${[90, 70, 80, 55, 75, 60][i % 6]}%` })));
}

function vizDocs() {
  return h("div", { class: "viz viz-docs" },
    h("div", { class: "fly", style: "--d:.1s" }, paper("INVOICE", 6, "inv")),
    h("div", { class: "fly", style: "--d:.45s" }, paper("PURCHASE ORDER", 5, "po")),
    h("div", { class: "viz-caption fade", style: "--d:.9s" }, "INV-1001 · Acme Office Supplies · 435.00 GBP"));
}

function vizRead() {
  const fields = [["Invoice number", "INV-1001", "hi"], ["Supplier", "Acme Office Supplies", "hi"], ["Currency", "GBP", "hi"],
    ["PO reference", "PO-5001", "hi"], ["Line items", "3 rows", "md"], ["Total", "435.00", "hi"]];
  return h("div", { class: "viz viz-read" },
    h("div", { class: "scan-wrap" }, paper("INVOICE", 6, "inv"), h("span", { class: "scanline" })),
    h("ul", { class: "fields-out" }, fields.map(([k, v, c], i) =>
      h("li", { class: `fade c-${c}`, style: `--d:${0.3 + i * 0.25}s` }, h("span", {}, k), h("b", {}, v)))));
}

function vizA2A() {
  return h("div", { class: "viz viz-a2a" },
    h("div", { class: "a2a-row" },
      h("div", { class: "agent-pill a" }, h("b", {}, "A"), "Coordinator"),
      h("div", { class: "wire" }, h("span", { class: "env" }, icon("M3 6h18v12H3z", "M3 7l9 6 9-6")), h("span", { class: "env back" })),
      h("div", { class: "agent-pill b" }, h("b", {}, "B"), "Verifier")),
    h("pre", { class: "code" },
      [["1", '{ "jsonrpc": "2.0",'], ["2", '  "method": "SendMessage",'], ["3", '  "params": { "message": {'],
        ["4", '    "parts": [ invoice + purchase_order ] } } }']]
        .map(([n, line], i) => h("span", { class: "fade", style: `--d:${0.2 + i * 0.3}s` }, h("em", {}, n), line))));
}

function vizChecks() {
  const checks = [["Invoice number", 1], ["PO reference", 1], ["Supplier", 1], ["Currency", 1], ["Quantities", 1],
    ["Unit prices", 0, "12.50 vs 11.00"], ["Line totals", 1], ["Invoice total", 1]];
  return h("div", { class: "viz viz-checks" },
    h("ul", { class: "checklist" }, checks.map(([name, ok, note], i) =>
      h("li", { class: `pop ${ok ? "ok" : "bad"}`, style: `--d:${0.15 + i * 0.32}s` },
        h("span", { class: "mark" }, ok ? "✓" : "✕"), name, note ? h("small", {}, note) : null))),
    h("div", { class: "verdict-chip pop", style: `--d:${0.3 + checks.length * 0.32}s` }, "Discrepancy found"));
}

function vizDb() {
  const rows = [["INV-0998", "Match", "ok"], ["INV-0999", "Needs review", "warn"], ["INV-1000", "Match", "ok"]];
  return h("div", { class: "viz viz-db" },
    h("div", { class: "db-table" },
      h("div", { class: "db-row head" }, h("span", {}, "task_id"), h("span", {}, "invoice"), h("span", {}, "result")),
      rows.map(([inv, res, tone], i) => h("div", { class: "db-row" }, h("span", {}, `7f3${i}…`), h("span", {}, inv),
        h("span", { class: `badge ${tone}` }, res))),
      h("div", { class: "db-row new" }, h("span", {}, "a91c…"), h("span", {}, "INV-1001"),
        h("span", { class: "badge bad" }, "Discrepancy"))),
    h("p", { class: "viz-caption fade", style: "--d:1.4s" }, "+ recommended action · risk level · AI explanation"));
}

function vizAI() {
  const message = "Invoice INV-1001 does not agree with PO-5001 on the unit price: the invoice charges 12.50 per unit "
    + "while the purchase order agreed 11.00. Hold the invoice and ask the supplier to confirm or correct it.";
  const out = h("p", { class: "typed" });
  const box = h("div", { class: "viz viz-ai" },
    h("div", { class: "ai-head" }, h("span", { class: "ai-badge" }, icon(...ICONS.ai)),
      h("div", {}, h("strong", {}, model), h("small", {}, provider.includes("local") ? `${provider} · running on this computer` : `${provider} · hosted model`))),
    out,
    h("div", { class: "ai-tags fade", style: "--d:4.2s" },
      h("span", { class: "badge bad" }, "High risk"), h("span", { class: "badge brand" }, "Hold & query supplier"),
      h("span", { class: "badge" }, "Supplier email drafted")));
  if (REDUCED.matches) out.textContent = message;
  else {
    let n = 0;
    const t = setInterval(() => { n += 2; out.textContent = message.slice(0, n); if (n >= message.length) clearInterval(t); }, 28 * pace);
    cleanups.push(() => clearInterval(t));
  }
  return box;
}

function vizUi() {
  const bars = [40, 65, 30, 80, 55, 90, 70];
  return h("div", { class: "viz viz-ui" },
    h("div", { class: "mini-tiles" },
      [["Reviews", "128"], ["Match rate", "72%"], ["Open issues", "9"]].map(([k, v], i) =>
        h("div", { class: "fade", style: `--d:${0.1 + i * 0.15}s` }, h("span", {}, k), h("b", {}, v)))),
    h("div", { class: "mini-bars" }, bars.map((v, i) =>
      h("i", { class: `grow ${i === bars.length - 1 ? "new" : ""}`, style: `--h:${v}%;--d:${0.4 + i * 0.12}s` }))));
}


// ---------------------------------------------------------------- system map (looping SVG animation)

const CYCLE = 12; // seconds for one full review round trip at Normal speed
const MAP_NODES = [
  { id: "browser", x: 30, y: 150, w: 150, label: "Your browser", sub: "Web app" },
  { id: "api", x: 240, y: 150, w: 150, label: "NexusAI API", sub: "Reads documents" },
  { id: "a", x: 470, y: 40, w: 170, label: "Agent A", sub: "Coordinator" },
  { id: "b", x: 760, y: 40, w: 170, label: "Agent B", sub: "Verifier" },
  { id: "ai", x: 470, y: 270, w: 170, label: "AI model", sub: "Ollama" },
  { id: "db", x: 760, y: 270, w: 170, label: "Supabase", sub: "Database" },
];
// [path, start, end (fractions of the cycle), colour, caption, node that lights up on arrival]
const MAP_MOVES = [
  ["M180 200 H240", 0.02, 0.10, "brand", "1 · You send an invoice and its purchase order", "api"],
  ["M390 200 C430 200 430 90 470 90", 0.12, 0.22, "brand", "2 · The API passes the clean data to Agent A", "a"],
  ["M640 80 H760", 0.24, 0.34, "brand", "3 · Agent A delegates the check to Agent B over A2A", "b"],
  ["M845 120 V290", 0.36, 0.45, "ok", "4 · Agent B saves the verified result", "db"],
  ["M760 105 H640", 0.47, 0.56, "ok", "5 · Agent B reports the verdict back to Agent A", "a"],
  ["M535 120 V290", 0.58, 0.66, "ai", "6 · Agent A asks the AI model to explain it", "ai"],
  ["M575 290 V120", 0.70, 0.78, "ai", "7 · The AI writes the explanation and the supplier email", "a"],
  ["M470 108 C420 108 430 215 390 215 H180", 0.80, 0.94, "ok", "8 · The report appears and the dashboard updates", "browser"],
];
const COLOR = { brand: "var(--brand)", ok: "#10b981", ai: "var(--brand-2)" };

function buildMap() {
  const animate = !REDUCED.matches;
  const cycle = CYCLE * pace;
  const loop = (attrs) => s("animate", { dur: `${cycle}s`, repeatCount: "indefinite", ...attrs });
  const svg = s("svg", { viewBox: "0 0 960 400", class: "map", role: "img",
    "aria-label": "Animated map of one review travelling through NexusAI" });
  [["Web", 15, 130, 180, 120], ["Server", 225, 130, 180, 120], ["Agents talk over A2A", 455, 10, 490, 120],
    ["Data & AI", 455, 240, 490, 120]].forEach(([label, x, y, w, hgt]) => {
    svg.append(s("rect", { class: "zone", x, y, width: w, height: hgt, rx: 16 }),
      s("text", { class: "zone-label", x: x + 12, y: y + 18 }, label));
  });
  MAP_MOVES.forEach(([d]) => svg.append(s("path", { class: "route", d })));
  MAP_NODES.forEach((n) => {
    const glow = s("rect", { class: "m-glow", x: n.x - 4, y: n.y + 16, width: n.w + 8, height: 68, rx: 15, opacity: 0 });
    if (animate) {
      MAP_MOVES.filter((m) => m[5] === n.id).forEach(([, , end]) => glow.append(loop({ attributeName: "opacity",
        values: "0;0;1;0;0", keyTimes: `0;${end - 0.01};${end + 0.01};${Math.min(end + 0.1, 0.99)};1`, additive: "sum" })));
    }
    svg.append(glow, s("g", { class: `m-node m-${n.id}` },
      s("rect", { class: "m-box", x: n.x, y: n.y + 20, width: n.w, height: 60, rx: 12 }),
      s("text", { class: "m-label", x: n.x + n.w / 2, y: n.y + 46, "text-anchor": "middle" }, n.label),
      s("text", { class: "m-sub", x: n.x + n.w / 2, y: n.y + 64, "text-anchor": "middle", "data-sub": n.id }, n.sub)));
  });
  MAP_MOVES.forEach(([d, start, end, tone, caption], i) => {
    const next = MAP_MOVES[i + 1]?.[1] ?? 0.99;
    if (!animate) {
      if (i === 0) svg.append(s("text", { class: "m-caption", x: 480, y: 392, "text-anchor": "middle" },
        "One review: browser → API → Agent A → Agent B → database → AI model → back to you"));
      return;
    }
    const dot = s("circle", { r: 7, fill: COLOR[tone], class: "m-packet", opacity: 0 },
      s("animateMotion", { dur: `${cycle}s`, repeatCount: "indefinite", path: d, keyPoints: "0;0;1;1",
        keyTimes: `0;${start};${end};1`, calcMode: "linear" }),
      loop({ attributeName: "opacity", values: "0;0;1;1;0;0", keyTimes: `0;${start};${start + 0.005};${end};${end + 0.005};1` }));
    const text = s("text", { class: "m-caption", x: 480, y: 392, "text-anchor": "middle", opacity: 0 }, caption,
      loop({ attributeName: "opacity", values: "0;0;1;1;0;0", keyTimes: `0;${start};${start + 0.01};${next - 0.01};${next};1` }));
    svg.append(dot, text);
  });
  fill($("#how-map"), svg);
}

// ---------------------------------------------------------------- live status + init

function paintSpeed() {
  $('[data-view="how"]').style.setProperty("--pace", String(pace));
  $$(".speed button").forEach((b) => {
    const on = Number(b.dataset.pace) === pace;
    b.classList.toggle("active", on);
    b.setAttribute("aria-checked", String(on));
  });
}

function setPace(value) {
  pace = value;
  try { localStorage.setItem(PACE_KEY, String(value)); } catch { /* not remembered */ }
  paintSpeed();
  buildMap();
  if (lastHealth !== undefined) paintStatus(lastHealth);
  go(index); // restart the current step at the new speed
}

let lastHealth;
function paintStatus(health) {
  lastHealth = health;
  const svcState = {
    coordinator: health?.coordinator === "online", verifier: health?.verifier === "online",
    storage: health?.storage === "connected", reasoning: health?.reasoning === "llm",
  };
  $$("#how-stage .s-node").forEach((el) => {
    const svc = el.dataset.svc;
    const dot = $(".s-dot", el);
    dot.className = `s-dot ${!svc ? "" : !health ? "bad" : svcState[svc] ? "ok" : svc === "reasoning" ? "warn" : "bad"}`;
    dot.title = !svc ? "" : svcState[svc] ? "Live: online" : "Live: not available";
  });
  if (health?.reasoning_model) model = health.reasoning_model;
  if (health?.reasoning_provider) provider = health.reasoning_provider;
  const aiSub = $('#how-stage [data-sub="ai"]');
  if (aiSub) aiSub.textContent = health?.reasoning === "llm" ? model : "Built-in writer";
  const mapSub = $('#how-map [data-sub="ai"]');
  if (mapSub) mapSub.textContent = health?.reasoning === "llm" ? `${health.reasoning_provider || "LLM"} · ${model}` : "Built-in writer";
}

export function showHow(health) {
  if (!built) {
    buildStage();
    buildMap();
    $("#how-prev").addEventListener("click", () => { pause(); go(index - 1); });
    $("#how-next").addEventListener("click", () => { pause(); go(index + 1); });
    $("#how-play").addEventListener("click", () => setPlaying(!playing));
    $$(".speed button").forEach((b) => b.addEventListener("click", () => setPace(Number(b.dataset.pace))));
    paintSpeed();
    built = true;
  }
  paintStatus(health);
  setPlaying(true);
}

export function hideHow() {
  clearTimeout(timer);
  cleanups.forEach((fn) => fn());
  cleanups = [];
}
