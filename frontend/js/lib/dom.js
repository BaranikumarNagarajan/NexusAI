// DOM helpers. All dynamic text goes through h()/textContent, never innerHTML,
// so data from agents, OCR or users cannot inject markup.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** h("div", {class: "x", onclick}, "text", child, [children]) */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  append(el, children);
  return el;
}

/** Same as h() for inline SVG (needs the SVG namespace). */
export function s(tag, props = {}, ...children) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value !== undefined && value !== null && value !== false) el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** replaceChildren() that skips null/false (the native one renders them as "null"). */
export const fill = (el, ...children) =>
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));

/** Inline icon from a list of SVG path "d" strings. */
export const icon = (...paths) => s("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" }, paths.map((d) => s("path", { d })));

export function toast(message, kind = "info") {
  const el = h("div", { class: `toast ${kind}`, role: "status" }, message);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 5000);
}

export function copyButton(text, label = "copy") {
  return h("button", {
    type: "button", class: "copy", title: "Copy",
    onclick: async (e) => {
      e.stopPropagation();
      try { await navigator.clipboard.writeText(text); toast("Copied to clipboard"); }
      catch { toast("Copy failed", "error"); }
    },
  }, label);
}
