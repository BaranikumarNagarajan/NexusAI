// Overview dashboard: KPIs, charts and recent activity from GET /api/analytics.
import { api, detailOf } from "../lib/api.js";
import { doughnut, horizontalBars, sparkline, stackedBars, toneColor } from "../lib/charts.js";
import { $, fill, h, s } from "../lib/dom.js";
import { renderReasoning } from "../components/result.js";
import { ACTION, CHECK, RESULT, RISK, STATUSES, badge, money, resultBadge, timeAgo } from "../lib/meta.js";

let days = 14;
let openReview = () => {};
let last = null;

const ICONS = {
  total: ["M4 4h16v16H4z", "M4 9h16M9 4v16"],
  rate: ["M20 6 9 17l-5-5"],
  bad: ["M12 9v4M12 17h.01", "M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"],
  warn: ["M12 8v4l3 2", "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z"],
  value: ["M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"],
};
const kIcon = (key) => s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, ICONS[key].map((d) => s("path", { d })));

function kpi({ key, tone, label, value, unit, sub, spark }) {
  const canvas = spark ? h("canvas", { "aria-hidden": "true" }) : null;
  const card = h("article", { class: `card kpi ${tone && tone !== "brand" ? tone : ""}` },
    h("div", { class: "kpi-top" }, h("span", { class: "kpi-label" }, label), h("span", { class: "kpi-icon" }, kIcon(key))),
    h("div", { class: "kpi-value" }, value, unit ? h("small", {}, unit) : null),
    h("div", { class: "kpi-sub" }, sub || ""),
    canvas ? h("div", { class: "kpi-spark" }, canvas) : null);
  return { card, draw: () => canvas && sparkline(canvas, spark, tone || "brand") };
}

function skeletons() {
  fill($("#kpis"), Array.from({ length: 5 }, () =>
    h("article", { class: "card kpi" }, h("div", { class: "skeleton", style: "height:14px;width:60%" }),
      h("div", { class: "skeleton", style: "height:30px;width:45%;margin-top:12px" }),
      h("div", { class: "skeleton", style: "height:34px;margin-top:12px" }))));
}

function chartMessage(canvasId, message) {
  const box = $(`#${canvasId}`).parentElement;
  box.querySelector(".chart-empty")?.remove();
  if (message) box.append(h("div", { class: "chart-empty" }, message));
}

const MIGRATION_NOTE = "Apply backend/supabase/migrations/002_add_coordinator_reasoning.sql in the Supabase SQL Editor to store Agent A's reasoning.";

function renderLatest(latest, hasColumns) {
  const box = $("#latest");
  if (!latest) { fill(box, h("p", { class: "muted" }, "No reviews yet. Run your first review to see it here.")); return; }
  const meta = RESULT[latest.verification_status] || { label: latest.verification_status, tone: "neutral" };
  const r = latest.reasoning;
  fill(box, h("div", { class: "latest" },
    h("div", { class: "latest-top" },
      h("div", { class: "latest-verdict" },
        h("div", { class: "verdict-top" }, badge(meta), latest.risk_level ? badge(RISK[latest.risk_level]) : null,
          h("span", { class: "muted", style: "margin:0" }, timeAgo(latest.created_at))),
        h("h4", {}, ACTION[latest.recommended_action] || meta.label),
        h("p", { class: "muted", style: "margin:0" }, latest.summary)),
      h("button", { type: "button", class: "btn ghost small", onclick: () => openReview(latest.task_id) }, "Open details")),
    h("div", { class: "latest-meta" },
      h("div", {}, h("span", {}, "Invoice"), h("strong", {}, [latest.invoice_number, latest.po_number].filter(Boolean).join(" · ") || "—")),
      h("div", {}, h("span", {}, "Supplier"), h("strong", {}, latest.supplier_name || "—")),
      h("div", {}, h("span", {}, "Total"), h("strong", {}, money(latest.invoice_total, latest.currency)))),
    r ? renderReasoning({ source: latest.reasoning_source, model: r.model, provider: r.provider, risk_level: latest.risk_level,
      explanation: r.explanation, risk_points: (r.risk_points || []).slice(0, 3), supplier_email: null,
      fallback_reason: r.fallback_reason, fact_check: r.fact_check }, { compact: true })
      : h("p", { class: hasColumns ? "muted" : "notice", style: "margin:0" }, hasColumns
        ? "No reasoning stored for this review (it was created before reasoning was saved, or from the command line)."
        : MIGRATION_NOTE)));
}

function renderRiskActions(byRisk, byAction, hasColumns) {
  const box = $("#risk-actions");
  if (!hasColumns) { fill(box, h("p", { class: "notice" }, MIGRATION_NOTE)); return; }
  const max = Math.max(1, ...Object.values(byRisk));
  const total = Object.values(byAction).reduce((a, b) => a + b, 0);
  fill(box,
    h("div", { class: "risk-bars" }, ["high", "medium", "low"].map((level) => h("div", { class: "risk-row" },
      badge(RISK[level]),
      h("div", { class: "bar" }, h("span", { class: RISK[level].tone, style: `width:${((byRisk[level] || 0) / max) * 100}%` })),
      h("b", {}, String(byRisk[level] || 0))))),
    total ? h("ul", { class: "action-list" }, Object.entries(ACTION).map(([key, label]) => h("li", {},
      h("span", {}, label), h("b", {}, String(byAction[key] || 0)))))
      : h("p", { class: "muted" }, "Actions appear after the next review."));
}

function render(data) {
  last = data;
  renderLatest(data.latest, data.reasoning_columns);
  renderRiskActions(data.by_risk || {}, data.by_action || {}, data.reasoning_columns);
  const by = data.by_status;
  const inRange = data.daily.reduce((n, d) => n + Object.values(d.counts).reduce((a, b) => a + b, 0), 0);
  const valid = data.total - (by.INVALID_REQUEST || 0);
  const series = (status) => data.daily.map((d) => d.counts[status] || 0);
  const totals = data.daily.map((d) => Object.values(d.counts).reduce((a, b) => a + b, 0));
  const currencies = Object.entries(data.value_by_currency);

  const cards = [
    kpi({ key: "total", tone: "brand", label: "Total reviews", value: data.total, sub: `${inRange} in the last ${days} days`, spark: totals }),
    kpi({ key: "rate", tone: "ok", label: "Match rate", value: data.match_rate == null ? "—" : Math.round(data.match_rate * 100), unit: data.match_rate == null ? "" : "%",
      sub: `${by.MATCH || 0} of ${valid} valid reviews matched`, spark: series("MATCH") }),
    kpi({ key: "bad", tone: "bad", label: "Discrepancies", value: by.DISCREPANCY_FOUND || 0,
      sub: valid ? `${Math.round(((by.DISCREPANCY_FOUND || 0) / valid) * 100)}% of valid reviews held` : "No reviews yet", spark: series("DISCREPANCY_FOUND") }),
    kpi({ key: "warn", tone: "warn", label: "Needs review", value: by.NEEDS_REVIEW || 0, sub: "Missing information requested", spark: series("NEEDS_REVIEW") }),
    kpi({ key: "value", tone: "info", label: "Value reviewed", value: currencies[0] ? currencies[0][1] : "—", unit: currencies[0]?.[0] || "",
      sub: currencies.slice(1).map(([c, v]) => `${v} ${c}`).join(" · ") || "Across saved reviews" }),
  ];
  fill($("#kpis"), cards.map((c) => c.card));
  cards.forEach((c) => c.draw());

  // Review volume
  const labels = data.daily.map((d) => new Date(`${d.date}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" }));
  stackedBars($("#chart-volume"), labels, STATUSES.map((st) => ({ label: RESULT[st].label, data: series(st), tone: RESULT[st].tone })));
  chartMessage("chart-volume", inRange ? "" : `No reviews in the last ${days} days.`);

  // Outcome mix
  const values = STATUSES.map((st) => by[st] || 0);
  doughnut($("#chart-outcomes"), STATUSES.map((st) => RESULT[st].label), values, STATUSES.map((st) => RESULT[st].tone));
  fill($("#donut-center"), h("div", {}, h("strong", {}, String(data.total)), h("span", {}, "reviews")));
  fill($("#outcome-legend"), STATUSES.map((st, i) => h("li", {},
    h("i", { style: `background:${toneColor(RESULT[st].tone)}` }), RESULT[st].label,
    h("b", {}, String(values[i])), h("span", { class: "pct" }, data.total ? `${Math.round((values[i] / data.total) * 100)}%` : "0%"))));

  // Top issues
  const issues = data.top_issues;
  horizontalBars($("#chart-issues"), issues.map((i) => CHECK[i.check] || i.check), [
    { label: "Mismatch", data: issues.map((i) => i.mismatch), tone: "bad" },
    { label: "Missing info", data: issues.map((i) => i.missing), tone: "warn" },
  ]);
  chartMessage("chart-issues", issues.length ? "" : "No issues found yet — every check passed.");

  // Supplier health
  fill($("#suppliers"), data.suppliers.length ? data.suppliers.map((sp) => {
    const pct = sp.reviews ? Math.round((sp.match / sp.reviews) * 100) : 0;
    return h("div", { class: "supplier" },
      h("div", { class: "supplier-top" }, h("strong", {}, sp.supplier), h("span", {}, `${pct}% matched · ${sp.reviews} review${sp.reviews === 1 ? "" : "s"}`)),
      h("div", { class: "bar", title: `${sp.match} matched, ${sp.issues} with issues` },
        h("span", { class: "ok", style: `width:${(sp.match / sp.reviews) * 100}%` }),
        h("span", { class: "bad", style: `width:${(sp.issues / sp.reviews) * 100}%` })));
  }) : h("p", { class: "muted" }, "Supplier statistics appear after the first valid review."));
}

async function loadRecent() {
  const tbody = $("#recent-table tbody");
  const res = await api("/api/reviews?limit=6");
  if (!res.ok) { fill(tbody, h("tr", { class: "empty-row" }, h("td", { colspan: 5 }, detailOf(res)))); return; }
  fill(tbody, res.data.items.length ? res.data.items.map((r) => h("tr", {
    tabindex: 0, onclick: () => openReview(r.task_id), onkeydown: (e) => { if (e.key === "Enter") openReview(r.task_id); },
  },
  h("td", { class: "inv" }, r.invoice_number || "—", h("span", { class: "sub" }, r.po_number || "")),
  h("td", {}, r.supplier_name || "—"),
  h("td", { class: "num" }, money(r.invoice_total, r.currency)),
  h("td", {}, resultBadge(r.verification_status)),
  h("td", { class: "muted", style: "margin:0;white-space:nowrap" }, timeAgo(r.created_at)))) :
    h("tr", { class: "empty-row" }, h("td", { colspan: 5 }, "No reviews yet. Run your first review.")));
}

export function updatePipeline(health) {
  const states = health
    ? { api: "online", coordinator: health.coordinator, verifier: health.verifier, storage: health.storage === "connected" ? "online" : "offline" }
    : { api: "offline", coordinator: "offline", verifier: "offline", storage: "offline" };
  for (const [node, state] of Object.entries(states)) {
    const el = $(`#pipeline [data-node="${node}"]`);
    el?.classList.toggle("online", state === "online");
    el?.classList.toggle("offline", state !== "online");
  }
}

export async function refreshOverview() {
  if (!last) skeletons();
  const res = await api(`/api/analytics?days=${days}`);
  if (res.ok) render(res.data);
  else {
    fill($("#kpis"), h("article", { class: "card", style: "grid-column:1/-1" }, h("h3", {}, "Analytics unavailable"), h("p", { class: "muted" }, detailOf(res))));
    ["chart-volume", "chart-issues"].forEach((id) => chartMessage(id, detailOf(res)));
  }
  loadRecent();
}

export function rethemeOverview() { if (last) render(last); }

export function initOverview({ onOpenReview }) {
  openReview = onOpenReview;
  $("#range").addEventListener("click", (e) => {
    const button = e.target.closest("button[data-days]");
    if (!button) return;
    days = Number(button.dataset.days);
    document.querySelectorAll("#range button").forEach((b) => b.classList.toggle("active", b === button));
    refreshOverview();
  });
}
