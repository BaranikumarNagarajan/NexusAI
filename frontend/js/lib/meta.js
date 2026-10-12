// Labels, tones and formatters shared by every view.
import { h } from "./dom.js";

export const RESULT = {
  MATCH: { label: "Match", tone: "ok" },
  DISCREPANCY_FOUND: { label: "Discrepancy found", tone: "bad" },
  NEEDS_REVIEW: { label: "Needs review", tone: "warn" },
  INVALID_REQUEST: { label: "Invalid request", tone: "neutral" },
};
export const STATUSES = Object.keys(RESULT);
export const ACTION = {
  ROUTE_TO_APPROVER: "Route to an authorised approver",
  HOLD_AND_QUERY_SUPPLIER: "Hold the invoice and query the supplier",
  REQUEST_MISSING_INFORMATION: "Request the missing information",
  CORRECT_AND_RESUBMIT: "Correct the request and resubmit",
};
export const OUTCOME = {
  PASS: { label: "Pass", tone: "ok" },
  MISMATCH: { label: "Mismatch", tone: "bad" },
  MISSING_INFORMATION: { label: "Missing info", tone: "warn" },
};
export const CHECK = {
  invoice_number: "Invoice number", po_reference: "PO reference", supplier: "Supplier", currency: "Currency",
  line_items: "Line items", line_item_presence: "Line item presence", line_item_quantity: "Quantity",
  line_item_unit_price: "Unit price", line_total_arithmetic: "Line total maths",
  invoice_total_arithmetic: "Invoice total maths", total_amount: "Total vs PO",
};
export const RISK = {
  low: { label: "Low risk", tone: "ok" }, medium: { label: "Medium risk", tone: "warn" }, high: { label: "High risk", tone: "bad" },
};

export const badge = (meta, text) => h("span", { class: `badge ${meta?.tone || ""}` }, text ?? meta?.label ?? "");
export const resultBadge = (status) => badge(RESULT[status] || { tone: "neutral" }, RESULT[status]?.label || status);

export function formatDate(iso, style = "medium") {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso || "";
  return date.toLocaleString(undefined, style === "short"
    ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { dateStyle: "medium", timeStyle: "short" });
}

export function timeAgo(iso) {
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(seconds)) return "";
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
  return `${Math.floor(seconds / 86400)} d ago`;
}

export const money = (amount, currency) => (amount ? `${amount} ${currency || ""}`.trim() : "—");
