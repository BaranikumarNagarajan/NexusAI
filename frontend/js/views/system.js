// Agents & system: what NexusAI is made of, in plain English, with the live status of every part.
import { api } from "../lib/api.js";
import { $, fill, h } from "../lib/dom.js";
import { badge } from "../lib/meta.js";

function flowNode(title, sub, online) {
  return h("div", { class: `flow-node ${online ? "online" : "offline"}` }, h("strong", {}, title), h("small", {}, sub));
}
const link = (label) => h("div", { class: "flow-link", "aria-hidden": "true" }, h("span", {}, label));

// ---------------------------------------------------------------- plain-English content

const INTRO = [
  ["1", "The team", "Two AI agents work together: one coordinates, one checks. They talk to each other over A2A, an open standard for agents."],
  ["2", "The helpers", "Four services support them: a database that remembers, OCR that reads scans, an AI model that explains, and fixed rules that decide."],
  ["3", "Live status", "Every badge on this page is checked right now. Green means working; amber means running in a reduced mode; red means down."],
];

const AGENTS = {
  coordinator: {
    title: "Agent A · Procurement Coordinator",
    job: "Receives every review request, finds the specialist (Agent B), hands the check over, then turns the "
      + "result into a clear next step and a plain-language explanation.",
    never: "Never checks the numbers itself and never approves a payment.",
    like: "A procurement manager who delegates the checking and writes the summary for the team.",
  },
  verifier: {
    title: "Agent B · Invoice Verification Specialist",
    job: "Compares the invoice with the purchase order line by line (supplier, currency, quantities, prices, "
      + "totals) using fixed rules, then saves the verdict.",
    never: "Never guesses: missing information is reported as missing, not treated as a match.",
    like: "An auditor with a strict checklist: same documents, same answer, every time.",
  },
};

const GLOSSARY = [
  ["A2A", "Agent-to-Agent: an open protocol that lets independent AI agents find each other and exchange tasks."],
  ["Agent Card", "An agent's public profile (name, skills, address). Agent A reads Agent B's card to find it."],
  ["Skill", "One job an agent offers, e.g. verify_invoice_against_po. Agent A checks Agent B has it first."],
  ["Task ID", "The tracking number of one piece of work. Every saved review is filed under Agent B's task ID."],
  ["OCR", "Optical character recognition: turning a scanned page or photo into text the system can read."],
  ["LLM", "Large language model: the AI that writes the explanation. It only explains; the rules decide."],
];

// ---------------------------------------------------------------- building blocks

function agentCard(agent) {
  const text = AGENTS[agent.role];
  const avatar = h("span", { class: "agent-avatar" }, agent.role === "coordinator" ? "A" : "B");
  const head = h("div", { class: "agent-head" }, avatar,
    h("div", {}, h("h3", {}, text.title), h("p", { class: "muted", style: "margin:0" },
      agent.online ? `${agent.name} · v${agent.version}` : "Not reachable right now")),
    badge({ tone: agent.online ? "ok" : "bad" }, agent.online ? "Online" : "Offline"));
  const plain = h("dl", { class: "plain-list" },
    h("dt", {}, "Its job"), h("dd", {}, text.job),
    h("dt", {}, "It never"), h("dd", {}, text.never),
    h("dt", {}, "Think of it as"), h("dd", {}, text.like));
  if (!agent.online) {
    return h("article", { class: `card agent-card ${agent.role}` }, head, plain,
      h("p", { class: "muted" }, "Its Agent Card could not be fetched. Locally, start everything with python backend\\run.py."));
  }
  return h("article", { class: `card agent-card ${agent.role}` }, head, plain,
    h("details", { class: "tech" },
      h("summary", {}, "Technical details (read live from its Agent Card)"),
      h("dl", { class: "agent-meta" },
        h("dt", {}, "Version"), h("dd", {}, agent.version),
        h("dt", {}, "Endpoint"), h("dd", {}, h("code", {}, agent.url)),
        h("dt", {}, "Agent Card"), h("dd", {}, h("code", {}, `${agent.url}/.well-known/agent-card.json`)),
        h("dt", {}, "Protocol"), h("dd", {}, "A2A 1.0 · JSON-RPC")),
      agent.skills.map((skill) => h("div", { class: "skill" },
        h("strong", {}, skill.name), " ", h("code", {}, skill.id), h("p", {}, skill.description)))));
}

function service({ title, nickname, tone, status, what, why, now }) {
  return h("article", { class: "card service" },
    h("div", { class: "service-top" }, h("div", {}, h("h3", {}, title), h("p", { class: "nickname" }, nickname)), badge({ tone }, status)),
    h("dl", { class: "plain-list compact" },
      h("dt", {}, "What it is"), h("dd", {}, what),
      h("dt", {}, "Why we need it"), h("dd", {}, why),
      h("dt", {}, "Right now"), h("dd", {}, now)));
}

// ---------------------------------------------------------------- render

export async function refreshSystem(health) {
  const res = await api("/api/agents");
  const agents = res.ok ? res.data : [];
  const byRole = Object.fromEntries(agents.map((a) => [a.role, a]));
  const coord = byRole.coordinator || { role: "coordinator", online: false, url: "" };
  const verifier = byRole.verifier || { role: "verifier", online: false, url: "" };
  const storageOk = health?.storage === "connected";
  const llm = health?.reasoning === "llm";
  const ocrOk = health?.ocr === "available";

  fill($("#system-intro"), INTRO.map(([n, title, body]) => h("div", { class: "intro-tile" },
    h("span", { class: "intro-n" }, n), h("div", {}, h("strong", {}, title), h("p", {}, body)))));

  fill($("#flow"),
    flowNode("NexusAI API", "Front door · reads documents", !!health), link("HTTP"),
    flowNode("Agent A", coord.online ? "Coordinator" : "offline", coord.online), link("A2A"),
    flowNode("Agent B", verifier.online ? "Verifier" : "offline", verifier.online), link("save"),
    flowNode("Supabase", "Database", storageOk));
  fill($("#agent-cards"), agentCard(coord), agentCard(verifier));

  fill($("#services"),
    service({
      title: "Supabase", nickname: "The memory", tone: storageOk ? "ok" : health?.storage === "not_configured" ? "warn" : "bad",
      status: { connected: "Connected", not_configured: "Not configured", error: "Error" }[health?.storage] || "Unknown",
      what: "A hosted PostgreSQL database.",
      why: "Keeps every review, so History, the dashboard and the audit trail survive restarts.",
      now: storageOk ? "Every final review is saved under Agent B's task ID." : "Not saving: set SUPABASE_URL and SUPABASE_SECRET_KEY.",
    }),
    service({
      title: "OCR (Tesseract)", nickname: "The eyes", tone: ocrOk ? "ok" : "warn", status: ocrOk ? "Available" : "Not installed",
      what: "Open-source text recognition.",
      why: "Reads the words from scanned invoices and photos, so they can be checked like digital PDFs.",
      now: ocrOk ? "Scans and photos can be read (printed text; handwriting is not supported)." : "Install Tesseract or set TESSERACT_CMD.",
    }),
    service({
      title: "AI model", nickname: "The writer", tone: llm ? "ok" : "warn", status: llm ? (health?.reasoning_provider || "LLM") : "Built-in",
      what: llm ? `A language model (${health?.reasoning_model}) used by Agent A.` : "Agent A's built-in explanation writer.",
      why: "Turns the verdict into a short explanation, possible causes, next steps and a supplier email. Every figure it writes is fact-checked.",
      now: llm ? "Ready." : (health?.reasoning_detail || "No model reachable: the built-in writer is used, so reviews still work."),
    }),
    service({
      title: "Verification rules", nickname: "The judge", tone: "info", status: "Deterministic",
      what: "Fixed checks with exact decimal arithmetic inside Agent B.",
      why: "The only part that decides Match, Discrepancy or Needs review. The AI never decides.",
      now: "Always on. Same documents give the same answer, every time.",
    }));

  fill($("#glossary"), GLOSSARY.map(([term, meaning]) => h("div", { class: "term" }, h("dt", {}, term), h("dd", {}, meaning))));
}
