// History: paginated, filterable list of saved reviews; rows open the detail drawer.
import { api, detailOf } from "../lib/api.js";
import { $, $$, fill, h } from "../lib/dom.js";
import { formatDate, money, resultBadge } from "../lib/meta.js";

const state = { limit: 12, offset: 0, status: "", items: [], total: 0 };
let openReview = () => {};

function rows() {
  const query = $("#history-search").value.trim().toLowerCase();
  const items = query
    ? state.items.filter((r) => [r.invoice_number, r.po_number, r.supplier_name, r.summary].some((v) => (v || "").toLowerCase().includes(query)))
    : state.items;
  const empty = (text) => h("tr", { class: "empty-row" }, h("td", { colspan: 6 }, text));
  if (!items.length) {
    return [empty(query ? "No reviews on this page match your filter." : state.status ? "No reviews with this result yet." : "No reviews yet. Run one from New review.")];
  }
  return items.map((r) => h("tr", {
    tabindex: 0, onclick: () => openReview(r.task_id), onkeydown: (e) => { if (e.key === "Enter") openReview(r.task_id); },
  },
  h("td", { style: "white-space:nowrap" }, formatDate(r.created_at, "short")),
  h("td", { class: "inv" }, r.invoice_number || "—", h("span", { class: "sub" }, r.po_number || "")),
  h("td", {}, r.supplier_name || "—"),
  h("td", { class: "num" }, money(r.invoice_total, r.currency)),
  h("td", {}, resultBadge(r.verification_status)),
  h("td", { class: "summary" }, r.summary)));
}

export async function refreshHistory() {
  const params = new URLSearchParams({ limit: state.limit, offset: state.offset });
  if (state.status) params.set("status", state.status);
  const res = await api(`/api/reviews?${params}`);
  const tbody = $("#history-table tbody");
  if (!res.ok) {
    fill(tbody, h("tr", { class: "empty-row" }, h("td", { colspan: 6 }, detailOf(res))));
    $("#history-meta").textContent = "History unavailable";
    $("#page-info").textContent = "";
    return;
  }
  state.items = res.data.items;
  state.total = res.data.total;
  fill(tbody, rows());
  const from = state.total ? state.offset + 1 : 0;
  const to = Math.min(state.offset + state.limit, state.total);
  $("#history-meta").textContent = `${state.total} saved review${state.total === 1 ? "" : "s"} · click a row for details`;
  $("#page-info").textContent = state.total ? `${from}–${to} of ${state.total}` : "";
  $("#prev-page").disabled = state.offset === 0;
  $("#next-page").disabled = to >= state.total;
}

export function initHistory({ onOpenReview }) {
  openReview = onOpenReview;
  $("#history-filter").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    $$("#history-filter .chip").forEach((c) => c.classList.toggle("selected", c === chip));
    state.status = chip.dataset.status;
    state.offset = 0;
    refreshHistory();
  });
  $("#history-search").addEventListener("input", () => fill($("#history-table tbody"), rows()));
  $("#history-refresh").addEventListener("click", refreshHistory);
  $("#prev-page").addEventListener("click", () => { state.offset = Math.max(0, state.offset - state.limit); refreshHistory(); });
  $("#next-page").addEventListener("click", () => { state.offset += state.limit; refreshHistory(); });
}
