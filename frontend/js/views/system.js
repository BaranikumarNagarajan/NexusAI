// Agents & system: live Agent Cards, A2A flow and service status.
import { api } from "../lib/api.js";
import { $, fill, h } from "../lib/dom.js";
import { badge } from "../lib/meta.js";

function flowNode(title, sub, online) {
  return h("div", { class: `flow-node ${online ? "online" : "offline"}` }, h("strong", {}, title), h("small", {}, sub));
}
const link = (label) => h("div", { class: "flow-link", "aria-hidden": "true" }, h("span", {}, label));

const PLAIN = {
  coordinator: {
    what: "The team lead. It receives every review request, finds the right specialist, hands the job over, "
      + "then explains the result in plain words and recommends what to do next.",
    analogy: "Like a procurement manager who delegates the checking and writes the summary.",
  },
  verifier: {
    what: "The checker. It compares the invoice with the purchase order line by line using fixed rules "
      + "(supplier, currency, quantities, prices, totals) and saves the verdict.",
    analogy: "Like an auditor with a strict checklist: same documents, same answer, every time.",
  },
};

function plain(role) {
  const p = PLAIN[role];
  return h("div", { class: "plain" }, h("span", { class: "plain-tag" }, "In plain English"),
    h("p", {}, p.what), h("p", { class: "muted" }, p.analogy));
}

function agentCard(agent) {
  const role = agent.role === "coordinator" ? "Agent A · Procurement Coordinator" : "Agent B · Invoice Verification Specialist";
  if (!agent.online) {
    return h("article", { class: `card agent-card ${agent.role}` },
      h("div", { class: "agent-head" }, h("span", { class: "agent-avatar" }, agent.role === "coordinator" ? "A" : "B"),
        h("div", {}, h("h3", {}, role), h("p", { class: "muted", style: "margin:0" }, agent.url)), badge({ tone: "bad" }, "Offline")),
      plain(agent.role),
      h("p", { class: "muted" }, "The Agent Card could not be fetched. Start the system with python backend\\run.py."));
  }
  return h("article", { class: `card agent-card ${agent.role}` },
    h("div", { class: "agent-head" }, h("span", { class: "agent-avatar" }, agent.role === "coordinator" ? "A" : "B"),
      h("div", {}, h("h3", {}, agent.name), h("p", { class: "muted", style: "margin:0" }, role)), badge({ tone: "ok" }, "Online")),
    plain(agent.role),
    h("p", { class: "tech-label" }, "Technical details (read live from its Agent Card)"),
    h("dl", { class: "agent-meta" },
      h("dt", {}, "Version"), h("dd", {}, agent.version),
      h("dt", {}, "Endpoint"), h("dd", {}, h("code", {}, agent.url)),
      h("dt", {}, "Agent Card"), h("dd", {}, h("code", {}, `${agent.url}/.well-known/agent-card.json`)),
      h("dt", {}, "Protocol"), h("dd", {}, "A2A 1.0 · JSON-RPC")),
    agent.skills.map((skill) => h("div", { class: "skill" },
      h("strong", {}, skill.name), " ", h("code", {}, skill.id), h("p", {}, skill.description))));
}

function service(title, tone, status, detail, why) {
  return h("article", { class: "card service" },
    h("div", { class: "service-top" }, h("h3", {}, title), badge({ tone }, status)),
    h("p", { class: "why-line" }, why),
    h("p", {}, detail));
}

export async function refreshSystem(health) {
  const res = await api("/api/agents");
  const agents = res.ok ? res.data : [];
  const byRole = Object.fromEntries(agents.map((a) => [a.role, a]));
  const coord = byRole.coordinator || { role: "coordinator", online: false, url: "" };
  const verifier = byRole.verifier || { role: "verifier", online: false, url: "" };
  const storageOk = health?.storage === "connected";

  fill($("#flow"),
    flowNode("NexusAI API", "FastAPI · OCR intake", !!health), link("HTTP"),
    flowNode("Agent A", coord.online ? `Coordinator v${coord.version}` : "offline", coord.online), link("A2A"),
    flowNode("Agent B", verifier.online ? `Verifier v${verifier.version}` : "offline", verifier.online), link("save"),
    flowNode("Supabase", "verification_reviews", storageOk));
  fill($("#agent-cards"), agentCard(coord), agentCard(verifier));

  const llm = health?.reasoning === "llm";
  fill($("#services"),
    service("Supabase storage", storageOk ? "ok" : health?.storage === "not_configured" ? "warn" : "bad",
      { connected: "Connected", not_configured: "Not configured", error: "Error" }[health?.storage] || "Unknown",
      storageOk ? "Every final review is saved under Agent B's task ID." : "Set SUPABASE_URL and SUPABASE_SECRET_KEY in backend/.env.",
      "The memory: keeps every review so History and the dashboard survive restarts."),
    service("OCR (Tesseract)", health?.ocr === "available" ? "ok" : "warn",
      health?.ocr === "available" ? "Available" : "Not installed",
      health?.ocr === "available" ? "Scanned PDFs and PNG/JPEG/TIFF invoices are read with OCR." : "Install Tesseract or set TESSERACT_CMD.",
      "The eyes: reads the words from scanned paper invoices and photos."),
    service("AI reasoning", llm ? "ok" : "warn", llm ? (health?.reasoning_provider || "LLM") : "Built-in",
      llm ? `Model ${health?.reasoning_model}` : (health?.reasoning_detail || "Using the built-in explanation writer."),
      "The writer: turns the verdict into a short explanation and a supplier email."),
    service("Verification engine", "info", "Deterministic",
      "Decimal arithmetic, exact comparisons, no tolerance. The rules decide; AI only explains.",
      "The judge: the only part that decides Match or Discrepancy, never the AI."));
}
