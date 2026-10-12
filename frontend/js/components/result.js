// Analysis report for a verification result (review page) and saved reviews (drawer).
import { doughnut, groupedBars, toneColor } from "../lib/charts.js";
import { $$, copyButton, h, s, toast } from "../lib/dom.js";
import { ACTION, CHECK, OUTCOME, RESULT, RISK, badge, formatDate, money } from "../lib/meta.js";

export const STEPS = [
  { id: "api", label: "NexusAI API received the request" },
  { id: "coordinator", label: "Agent A discovered Agent B and delegated over A2A" },
  { id: "verifier", label: "Agent B verified the invoice against the purchase order" },
  { id: "storage", label: "Review saved to Supabase" },
  { id: "result", label: "Agent A explained the result and recommended the next step" },
];
const VERDICT_ICON = { ok: "✓", bad: "!", warn: "?", neutral: "×" };
const after = (fn) => requestAnimationFrame(() => requestAnimationFrame(fn)); // canvas is in the DOM by then

// ---------------------------------------------------------------- small pieces

export function emptyState() {
  return h("div", { class: "empty-state" },
    s("svg", { class: "empty-illus", viewBox: "0 0 220 110", "aria-hidden": "true" },
      s("line", { x1: 58, y1: 55, x2: 82, y2: 55 }), s("line", { x1: 138, y1: 55, x2: 162, y2: 55 }),
      s("rect", { x: 6, y: 30, width: 52, height: 50, rx: 12 }), s("text", { x: 32, y: 60, "text-anchor": "middle" }, "API"),
      s("rect", { class: "a", x: 84, y: 30, width: 52, height: 50, rx: 12 }), s("text", { class: "a", x: 110, y: 60, "text-anchor": "middle" }, "A ⇄ B"),
      s("rect", { x: 164, y: 30, width: 52, height: 50, rx: 12 }), s("text", { x: 190, y: 60, "text-anchor": "middle" }, "DB")),
    h("h3", {}, "Your analysis report appears here"),
    h("p", { class: "muted" }, "Upload documents or pick a sample, then press ", h("b", {}, "Run review"), " to watch both agents work."));
}

export function renderSteps(states) {
  return h("ol", { class: "steps" }, STEPS.map((step, i) => {
    const state = states[step.id] || "pending";
    const mark = { done: "✓", failed: "!", skipped: "–" }[state] || String(i + 1);
    return h("li", { class: `step ${state}` }, h("span", { class: "marker" }, mark), h("span", {}, step.label));
  }));
}

function pipeline(states) {
  const labels = { api: "API", coordinator: "Agent A", verifier: "Agent B", storage: "Supabase", result: "Reasoning" };
  return h("ol", { class: "pipe" }, Object.entries(labels).map(([id, label], i) => [
    i ? h("li", { class: "pipe-link", "aria-hidden": "true" }) : null,
    h("li", { class: `pipe-step ${states[id] || "pending"}`, title: STEPS.find((st) => st.id === id)?.label },
      h("span", {}, { done: "✓", failed: "!", skipped: "–" }[states[id]] || "•"), label),
  ]));
}

function tabs(panes) {
  const bar = h("div", { class: "report-tabs", role: "tablist" });
  const body = h("div", { class: "report-panes" });
  panes.forEach((pane, i) => {
    const button = h("button", { type: "button", role: "tab", class: i === 0 ? "active" : "", "aria-selected": String(i === 0) },
      pane.label, pane.count != null ? h("span", { class: "tab-count" }, String(pane.count)) : null);
    const content = h("div", { class: "report-pane", role: "tabpanel", hidden: i !== 0 }, pane.content);
    button.addEventListener("click", () => {
      $$("button", bar).forEach((b) => { b.classList.toggle("active", b === button); b.setAttribute("aria-selected", String(b === button)); });
      $$(".report-pane", body).forEach((p) => { p.hidden = p !== content; });
      pane.onShow?.();
    });
    bar.append(button);
    body.append(content);
  });
  return [bar, body];
}

const evidenceSide = (evidence, prefix) =>
  Object.entries(evidence || {}).find(([k]) => k.startsWith(prefix) && !k.endsWith("currency"))?.[1];

function attentionCard(f) {
  const meta = OUTCOME[f.outcome] || { label: f.outcome, tone: "neutral" };
  const ev = f.evidence || {};
  const missing = ev.missing_fields;
  const inv = evidenceSide(ev, "invoice."), po = evidenceSide(ev, "purchase_order.");
  return h("article", { class: `attention ${meta.tone}` },
    h("header", {}, h("strong", {}, `${CHECK[f.check] || f.check}${ev.sku ? ` · ${ev.sku}` : ""}`), badge(meta)),
    h("p", {}, f.explanation),
    missing ? h("div", { class: "missing-list" }, missing.map((m) => h("code", {}, m)))
      : (inv !== undefined || po !== undefined) ? h("div", { class: "compare" },
        h("div", {}, h("span", {}, "Invoice"), h("strong", {}, inv ?? "—")),
        h("div", {}, h("span", {}, "Purchase order"), h("strong", {}, po ?? "—")),
        ev.difference !== undefined ? h("div", { class: "diff" }, h("span", {}, "Difference"), h("strong", {}, String(ev.difference))) : null)
      : null);
}

function checksTable(findings) {
  if (!findings?.length) return h("p", { class: "muted" }, "No checks were run (the request was rejected before verification).");
  const order = { MISMATCH: 0, MISSING_INFORMATION: 1, PASS: 2 };
  const sorted = [...findings].sort((a, b) => (order[a.outcome] ?? 3) - (order[b.outcome] ?? 3));
  return h("div", { class: "table-wrap" }, h("table", { class: "table checks" },
    h("thead", {}, h("tr", {}, h("th", {}, "Check"), h("th", {}, "Result"), h("th", {}, "Details"))),
    h("tbody", {}, sorted.map((f) => h("tr", { class: `row-${(OUTCOME[f.outcome] || {}).tone}` },
      h("td", { class: "inv" }, CHECK[f.check] || f.check, f.evidence?.sku ? h("span", { class: "sub" }, f.evidence.sku) : null),
      h("td", {}, badge(OUTCOME[f.outcome], OUTCOME[f.outcome]?.label || f.outcome)),
      h("td", { class: "muted", style: "margin:0" }, f.explanation))))));
}

function emailPane(email) {
  if (!email) return h("div", { class: "pane-empty" }, h("strong", {}, "No supplier email needed"),
    h("p", { class: "muted" }, "Agent A only drafts an email when there is a discrepancy or missing information."));
  return h("div", { class: "email-draft" },
    h("div", { class: "email-head" },
      h("span", {}, h("b", {}, "Draft to the supplier"), h("span", { class: "muted", style: "margin:0 0 0 .5rem" }, "review before sending")),
      h("span", { class: "email-actions" },
        h("button", { type: "button", class: "link-btn", onclick: async () => {
          try { await navigator.clipboard.writeText(`Subject: ${email.subject}\n\n${email.body}`); toast("Email copied"); }
          catch { toast("Copy failed", "error"); }
        } }, "Copy"),
        h("a", { class: "link-btn", href: `mailto:?subject=${encodeURIComponent(email.subject)}&body=${encodeURIComponent(email.body)}` }, "Open in mail app"))),
    h("p", { class: "email-subject" }, email.subject),
    h("pre", { class: "email-body" }, email.body));
}

function factChip(check) {
  if (!check || check.status === "failed") return null;
  const corrected = check.status === "corrected";
  return h("span", { class: `fact-chip ${corrected ? "warn" : "ok"}`,
    title: corrected ? "The model's first draft quoted a figure that is not in the verified data; it was asked to correct it."
      : "Every number in the AI text was compared with the verified data." },
  corrected ? "↻" : "✓", ` ${check.figures} figure${check.figures === 1 ? "" : "s"} fact-checked${corrected ? " (corrected)" : ""}`);
}

/** Reasoning block: explanation, risk points and possible causes (review page, drawer, Overview). */
export function renderReasoning(reasoning, { compact = false } = {}) {
  if (!reasoning) return null;
  const fromLLM = reasoning.source === "llm";
  return h("section", { class: "why" },
    h("header", {}, h("span", { class: `ai-mark ${fromLLM ? "llm" : "builtin"}`, "aria-hidden": "true" }, fromLLM ? "AI" : "≡"),
      h("div", {}, h("h4", {}, "Why this result"),
        h("p", { class: "muted", style: "margin:0" }, fromLLM ? `Agent A · ${reasoning.model}${reasoning.provider ? ` (${reasoning.provider})` : ""}` : "Agent A · built-in explanation")),
      reasoning.risk_level ? badge(RISK[reasoning.risk_level]) : null),
    h("p", { class: "why-text" }, reasoning.explanation),
    reasoning.risk_points?.length ? h("ul", { class: "why-points" }, reasoning.risk_points.map((p) => h("li", {}, p))) : null,
    !compact && reasoning.likely_causes?.length ? h("div", { class: "causes" },
      h("h5", {}, "Possible causes ", h("span", {}, "suggestions to investigate, not findings")),
      h("ul", {}, reasoning.likely_causes.map((c) => h("li", {}, c)))) : null,
    fromLLM && reasoning.fact_check ? h("div", { class: "why-foot" }, factChip(reasoning.fact_check)) : null,
    !fromLLM && reasoning.fallback_reason ? h("p", { class: "why-note" }, `AI reasoning unavailable: ${reasoning.fallback_reason}`) : null);
}

/** Money at stake: invoice total vs PO total as two bars, plus the effect of each line mismatch. */
function impactPanel(fi) {
  if (!fi) return null;
  const cur = fi.currency || "";
  const num = (v) => Number(String(v ?? "").replace(/,/g, ""));
  if (fi.direction === "different_currency") {
    return h("div", { class: "panel impact" }, h("h5", {}, "Money at stake"),
      h("p", { class: "muted" }, `Not comparable: the invoice is in ${fi.currency} and the purchase order in ${fi.po_currency}. `
        + "Agree the currency with the supplier first."));
  }
  const inv = num(fi.invoice_total), po = num(fi.po_total);
  const max = Math.max(inv || 0, po || 0) || 1;
  const bar = (label, value, raw, tone) => h("div", { class: "impact-row" }, h("span", {}, label),
    h("div", { class: "impact-track" }, h("i", { class: tone, style: `width:${raw == null ? 0 : Math.max(2, (value / max) * 100)}%` })),
    h("b", {}, raw == null ? "—" : `${raw} ${cur}`.trim()));
  const tone = { over: "bad", under: "warn", equal: "ok" }[fi.direction] || "neutral";
  const headline = { over: `Invoice is ${fi.difference} ${cur} above the PO`, under: `Invoice is ${fi.difference} ${cur} below the PO`,
    equal: "Totals agree" }[fi.direction] || "Only one total is available";
  return h("div", { class: "panel impact" },
    h("header", {}, h("h5", {}, "Money at stake"), badge({ tone }, fi.difference_percent && fi.direction !== "equal"
      ? `${headline} (${fi.difference_percent}%)` : headline)),
    bar("Invoice", inv, fi.invoice_total, fi.direction === "over" ? "bad" : "brand"),
    bar("Purchase order", po, fi.po_total, "info"),
    fi.lines?.length ? h("ul", { class: "impact-lines" }, fi.lines.map((l) => h("li", {},
      h("code", {}, l.sku), h("span", {}, `${l.issue}: ${l.detail}`), h("b", {}, `${num(l.amount) > 0 ? "+" : ""}${l.amount} ${cur}`.trim())))) : null,
    h("p", { class: "panel-note" }, "Calculated in code from Agent B's evidence, not by the AI."));
}

/** The supplier's earlier reviews, newest first, from Supabase. */
function historyPanel(hist) {
  if (!hist) return null;
  const dup = hist.same_invoice_seen_before ? h("p", { class: "dup-warn" }, "⚠ ", h("b", {}, "Possible duplicate: "),
    `this invoice number was reviewed ${hist.same_invoice_seen_before} time(s) before. Check it has not been paid already.`) : null;
  if (!hist.previous_reviews) return h("div", { class: "panel history" }, h("h5", {}, "Supplier track record"), dup,
    h("p", { class: "muted" }, "No other invoices from this supplier have been reviewed yet."));
  return h("div", { class: "panel history" },
    h("header", {}, h("h5", {}, "Supplier track record"),
      h("span", { class: "muted" }, `${hist.previous_reviews} other invoice${hist.previous_reviews === 1 ? "" : "s"}`)),
    dup,
    h("div", { class: "history-counts" },
      h("span", { class: "hc ok" }, h("b", {}, String(hist.matches)), "match"),
      h("span", { class: "hc bad" }, h("b", {}, String(hist.discrepancies)), "discrepancy"),
      h("span", { class: "hc warn" }, h("b", {}, String(hist.needs_review)), "needs review"),
      hist.discrepancy_rate_percent != null ? h("span", { class: "hc" }, h("b", {}, `${hist.discrepancy_rate_percent}%`), "discrepancy rate") : null),
    hist.recent?.length ? h("ol", { class: "history-recent" }, hist.recent.map((r) => {
      const meta = RESULT[r.result] || { label: r.result, tone: "neutral" };
      return h("li", {}, h("i", { class: `h-dot ${meta.tone}` }), h("span", {}, r.invoice_number || "—"),
        h("small", {}, `${meta.label} · ${r.date || ""}`));
    })) : null);
}

/** "How Agent A reasoned": the steps it took, in order. */
function traceTimeline(trace) {
  if (!trace?.length) return null;
  return h("ol", { class: "trace" }, trace.map((t, i) => h("li", { style: `--d:${i * 0.08}s` },
    h("span", { class: "trace-n" }, String(i + 1)),
    h("div", {}, h("strong", {}, t.step), h("p", {}, t.detail)))));
}

function reasoningPane(reasoning) {
  if (!reasoning) return h("div", { class: "pane-empty" }, h("strong", {}, "No reasoning available"));
  return h("div", { class: "reasoning-pane" },
    h("div", { class: "rp-trace" }, h("h4", {}, "How Agent A reasoned"), traceTimeline(reasoning.trace)
      || h("p", { class: "muted" }, "This review was saved before reasoning traces were recorded.")),
    h("div", { class: "rp-side" }, impactPanel(reasoning.financial_impact), historyPanel(reasoning.supplier_history)));
}

function lineSeries(payload) {
  const amount = (l) => {
    if (l.line_total) return Number(l.line_total);
    const q = Number(l.quantity), p = Number(l.unit_price);
    return Number.isFinite(q * p) ? Math.round(q * p * 100) / 100 : 0;
  };
  const inv = new Map((payload?.invoice?.line_items || []).map((l) => [l.sku, amount(l)]));
  const po = new Map((payload?.purchase_order?.line_items || []).map((l) => [l.sku, amount(l)]));
  const labels = [...new Set([...inv.keys(), ...po.keys()])].filter(Boolean).slice(0, 12);
  return labels.length ? { labels, invoice: labels.map((k) => inv.get(k) ?? 0), po: labels.map((k) => po.get(k) ?? 0) } : null;
}

// ---------------------------------------------------------------- the report

export function renderSubmission(body, { onOpenSaved, onDashboard, payload } = {}) {
  const meta = RESULT[body.verification_result] || { label: body.verification_result, tone: "neutral" };
  const findings = body.verification?.findings || [];
  const count = (o) => findings.filter((f) => f.outcome === o).length;
  const pass = count("PASS"), mismatch = count("MISMATCH"), missing = count("MISSING_INFORMATION");
  const issues = findings.filter((f) => f.outcome !== "PASS");
  const persistence = body.persistence || {};
  const saved = persistence.status === "saved";
  const reasoning = body.reasoning;
  const totalFinding = findings.find((f) => f.check === "total_amount");
  const currency = payload?.invoice?.currency || "";
  const states = { api: "done", coordinator: "done", verifier: "done", result: "done",
    storage: saved ? "done" : persistence.status === "disabled" ? "skipped" : "failed" };

  // banner
  const banner = h("section", { class: `report-banner ${meta.tone}` },
    h("span", { class: "verdict-icon", "aria-hidden": "true" }, VERDICT_ICON[meta.tone] || "•"),
    h("div", { class: "banner-text" },
      h("div", { class: "verdict-top" }, badge(meta), reasoning?.risk_level ? badge(RISK[reasoning.risk_level]) : null),
      h("h3", {}, ACTION[body.recommended_action] || meta.label),
      h("p", {}, body.summary),
      h("div", { class: "chips-row" },
        body.invoice_number ? h("span", { class: "fact" }, `Invoice ${body.invoice_number}`) : null,
        body.po_number ? h("span", { class: "fact" }, `PO ${body.po_number}`) : null,
        payload?.invoice?.supplier_name ? h("span", { class: "fact" }, payload.invoice.supplier_name) : null,
        payload?.invoice?.total_amount ? h("span", { class: "fact" }, money(payload.invoice.total_amount, currency)) : null)));

  // summary pane
  const donutCanvas = h("canvas", { "aria-label": "Check outcomes" });
  const linesCanvas = h("canvas", { "aria-label": "Invoice vs purchase order per line" });
  const series = lineSeries(payload);
  const diff = totalFinding?.evidence?.difference;
  const minis = h("div", { class: "mini-kpis" },
    h("div", { class: "mini ok" }, h("span", {}, "Checks passed"), h("strong", {}, findings.length ? `${pass}/${findings.length}` : "—")),
    h("div", { class: "mini bad" }, h("span", {}, "Mismatches"), h("strong", {}, String(mismatch))),
    h("div", { class: "mini warn" }, h("span", {}, "Missing info"), h("strong", {}, String(missing))),
    h("div", { class: `mini ${diff && Number(diff) !== 0 ? "bad" : "info"}` }, h("span", {}, "Total vs PO"),
      h("strong", {}, totalFinding ? (diff ? `${Number(diff) > 0 ? "+" : ""}${diff} ${currency}`.trim() : totalFinding.outcome === "PASS" ? "Equal" : "n/a") : "—")));
  const chartsRow = findings.length ? h("div", { class: "report-charts" },
    h("div", { class: "rc" }, h("h5", {}, "Check outcomes"), h("div", { class: "rc-box donut-sm" }, donutCanvas,
      h("div", { class: "donut-center" }, h("div", {}, h("strong", {}, String(findings.length)), h("span", {}, "checks"))))),
    series ? h("div", { class: "rc" }, h("h5", {}, `Line amounts: invoice vs PO ${currency ? `(${currency})` : ""}`), h("div", { class: "rc-box" }, linesCanvas)) : null) : null;
  const whyBlock = renderReasoning(reasoning ? { ...reasoning } : null)
    || h("section", { class: "why" }, h("p", { class: "why-text" }, body.summary));
  const attention = issues.length
    ? h("div", { class: "block" }, h("h4", {}, `Needs attention (${issues.length})`), h("div", { class: "attention-grid" }, issues.map(attentionCard)))
    : findings.length ? h("div", { class: "all-good" }, h("span", { "aria-hidden": "true" }, "✓"),
      h("div", {}, h("strong", {}, `All ${findings.length} checks passed`), h("p", { class: "muted", style: "margin:0" }, "Supplier, currency, quantities, prices and totals agree.")))
      : null;
  const errors = body.verification?.validation_errors || [];
  const errorBlock = errors.length ? h("div", { class: "block" }, h("h4", {}, "Fields to correct"),
    h("div", { class: "attention-grid" }, errors.map((e) => h("article", { class: "attention neutral" },
      h("header", {}, h("code", {}, e.field), badge({ tone: "neutral" }, "Invalid")), h("p", {}, e.message))))) : null;
  const steps = reasoning?.next_steps?.length ? reasoning.next_steps : body.action_items || [];
  const nextSteps = steps.length ? h("div", { class: "block" }, h("h4", {}, "Next steps"),
    h("ol", { class: "next-steps" }, steps.map((item) => h("li", {}, item)))) : null;

  if (findings.length) {
    after(() => {
      doughnut(donutCanvas, ["Pass", "Mismatch", "Missing info"], [pass, mismatch, missing], ["ok", "bad", "warn"]);
      if (series) groupedBars(linesCanvas, series.labels, [
        { label: "Invoice", data: series.invoice, tone: "brand" },
        { label: "Purchase order", data: series.po, tone: "info" }], { money: true });
    });
  }

  const technical = [
    h("dl", { class: "trail" },
      h("dt", {}, "Agent A task"), h("dd", {}, h("code", {}, body.task_id), copyButton(body.task_id)),
      h("dt", {}, "Agent B task"), h("dd", {}, h("code", {}, body.verifier_task_id), copyButton(body.verifier_task_id)),
      h("dt", {}, "Supabase"), h("dd", {}, badge({ tone: saved ? "ok" : persistence.status === "failed" ? "bad" : "neutral" },
        { saved: "Saved", failed: "Not saved", disabled: "Storage off" }[persistence.status] || persistence.status),
        persistence.review_id ? h("code", {}, persistence.review_id) : null),
      h("dt", {}, "Reasoning stored"), h("dd", {}, persistence.reasoning_saved === true ? badge({ tone: "ok" }, "Yes")
        : persistence.reasoning_saved === false ? badge({ tone: "warn" }, "No") : "—"),
      h("dt", {}, "Reasoning source"), h("dd", {}, reasoning ? (reasoning.source === "llm" ? `${reasoning.provider || "LLM"} · ${reasoning.model}` : "Built-in writer") : "—")),
    persistence.reasoning_saved === false ? h("p", { class: "notice", style: "margin-top:12px" }, persistence.reasoning_detail || "") : null,
    body.verification?.assumptions?.length ? h("div", { class: "block" }, h("h4", {}, "Assumptions used"),
      h("ul", { class: "assumptions" }, body.verification.assumptions.map((a) => h("li", {}, a)))) : null,
  ];

  const [tabBar, panes] = tabs([
    { label: "Summary", content: [minis, chartsRow, whyBlock, attention, errorBlock, nextSteps] },
    { label: "AI reasoning", content: reasoningPane(reasoning) },
    { label: "All checks", count: findings.length, content: checksTable(findings) },
    { label: "Supplier email", content: emailPane(reasoning?.supplier_email) },
    { label: "Technical", content: technical },
  ]);

  return [
    pipeline(states),
    banner,
    tabBar,
    panes,
    h("div", { class: "result-actions" },
      onDashboard ? h("button", { type: "button", class: "btn primary small", onclick: onDashboard }, "View on dashboard") : null,
      saved && onOpenSaved ? h("button", { type: "button", class: "btn ghost small", onclick: () => onOpenSaved(body.verifier_task_id) }, "Open saved review") : null),
    h("p", { class: "disclaimer" }, body.disclaimer),
  ];
}

/** A saved review from GET /api/reviews/{task_id} (history drawer). */
export function renderSavedReview(r) {
  const meta = RESULT[r.verification_status] || { label: r.verification_status, tone: "neutral" };
  const findings = r.findings || [];
  const issues = findings.filter((f) => f.outcome !== "PASS");
  const passCount = findings.length - issues.length;
  return [
    h("section", { class: `report-banner ${meta.tone}` },
      h("span", { class: "verdict-icon", "aria-hidden": "true" }, VERDICT_ICON[meta.tone] || "•"),
      h("div", { class: "banner-text" },
        h("div", { class: "verdict-top" }, badge(meta), r.risk_level ? badge(RISK[r.risk_level]) : null,
          h("span", { class: "muted", style: "margin:0" }, formatDate(r.created_at))),
        h("h3", {}, ACTION[r.recommended_action] || [r.invoice_number, r.po_number].filter(Boolean).join(" · ") || meta.label),
        h("p", {}, r.summary),
        h("div", { class: "chips-row" },
          r.invoice_number ? h("span", { class: "fact" }, `Invoice ${r.invoice_number}`) : null,
          r.po_number ? h("span", { class: "fact" }, `PO ${r.po_number}`) : null,
          r.supplier_name ? h("span", { class: "fact" }, r.supplier_name) : null,
          r.invoice_total ? h("span", { class: "fact" }, money(r.invoice_total, r.currency)) : null))),
    r.reasoning ? renderReasoning({ ...r.reasoning, source: r.reasoning_source, risk_level: r.risk_level }) : null,
    r.reasoning?.next_steps?.length ? h("div", { class: "block" }, h("h4", {}, "Next steps"),
      h("ol", { class: "next-steps" }, r.reasoning.next_steps.map((item) => h("li", {}, item)))) : null,
    r.reasoning?.trace?.length ? h("details", { open: true }, h("summary", {}, "How Agent A reasoned"), reasoningPane(r.reasoning)) : null,
    issues.length ? h("div", { class: "block" }, h("h4", {}, `Needs attention (${issues.length})`), h("div", { class: "attention-grid" }, issues.map(attentionCard))) : null,
    findings.length && !issues.length ? h("div", { class: "all-good" }, h("span", { "aria-hidden": "true" }, "✓"),
      h("div", {}, h("strong", {}, `All ${passCount} checks passed`))) : null,
    (r.validation_errors || []).length ? h("div", { class: "block" }, h("h4", {}, "Fields to correct"),
      h("div", { class: "attention-grid" }, r.validation_errors.map((e) => h("article", { class: "attention neutral" },
        h("header", {}, h("code", {}, e.field), badge({ tone: "neutral" }, "Invalid")), h("p", {}, e.message))))) : null,
    findings.length ? h("details", {}, h("summary", {}, `All checks (${findings.length})`), checksTable(findings)) : null,
    r.reasoning?.supplier_email ? h("details", {}, h("summary", {}, "Supplier email draft"), emailPane(r.reasoning.supplier_email)) : null,
    h("details", {}, h("summary", {}, "Technical details"),
      h("dl", { class: "trail", style: "margin-top:10px" },
        h("dt", {}, "Agent B task"), h("dd", {}, h("code", {}, r.task_id), copyButton(r.task_id)),
        h("dt", {}, "Review ID"), h("dd", {}, h("code", {}, r.id)),
        h("dt", {}, "Task state"), h("dd", {}, r.task_state)),
      r.assumptions?.length ? h("ul", { class: "assumptions" }, r.assumptions.map((a) => h("li", {}, a))) : null),
  ];
}

export function renderError(status, message) {
  const hint = {
    502: "One of the agents failed to complete the task. Check that Agent B is running.",
    503: "The Procurement Coordinator (Agent A) is not reachable.",
    0: "Start everything with: python backend\\run.py",
  }[status] || "";
  return h("div", { class: "error-box", role: "alert" },
    h("h3", {}, status ? `Request failed (HTTP ${status})` : "Request failed"),
    h("p", {}, message),
    hint ? h("p", { class: "muted" }, hint) : null);
}

export { toneColor };
