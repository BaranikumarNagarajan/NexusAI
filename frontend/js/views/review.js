// New review: documents (PDF/image + OCR) -> check data -> run through Agent A -> Agent B.
import { renderError, renderSteps, renderSubmission, emptyState } from "../components/result.js";
import { api, detailOf, withToken } from "../lib/api.js";
import { $, $$, fill, h, icon, toast } from "../lib/dom.js";
import { RESULT } from "../lib/meta.js";

const MAX_BYTES = 5 * 1024 * 1024;
const DOC_LABEL = { invoice: "Invoice", purchase_order: "Purchase order" };
const LINE_FIELDS = { invoice: ["sku", "description", "quantity", "unit_price", "line_total"], purchase_order: ["sku", "quantity", "unit_price"] };
const LINE_PLACEHOLDER = { sku: "SKU", description: "Description", quantity: "Qty", unit_price: "0.00", line_total: "0.00" };
const docs = { invoice: null, purchase_order: null }; // { file, url, extraction }
let activeTab = "form";
let hooks = { onOpenSaved: () => {}, onReviewed: () => {}, onDashboard: () => {} };

// ---------------------------------------------------------------- wizard

function setWizard(step) {
  $$(".wizard li").forEach((li, i) => {
    li.classList.toggle("done", i + 1 < step);
    li.classList.toggle("active", i + 1 === step);
  });
}

// ---------------------------------------------------------------- form <-> payload

function clean(value) {
  if (value === null || value === undefined) return undefined;
  const text = String(value).trim();
  return text === "" ? undefined : text;
}

function addLine(side, item = {}) {
  const row = h("div", { class: `line-grid ${side === "invoice" ? "line-grid-invoice" : "line-grid-po"}` },
    LINE_FIELDS[side].map((key) => h("input", {
      dataset: { key }, value: item[key] ?? "", placeholder: LINE_PLACEHOLDER[key], autocomplete: "off",
      inputmode: ["quantity", "unit_price", "line_total"].includes(key) ? "decimal" : undefined,
      "aria-label": `${DOC_LABEL[side]} line ${key.replace("_", " ")}`,
    })),
    h("button", { type: "button", class: "remove-line", "aria-label": "Remove line", onclick: () => row.remove() }, "×"));
  $(`.lines[data-side="${side}"] .line-rows`).append(row);
}

export function readForm() {
  const payload = { invoice: {}, purchase_order: {} };
  for (const input of $$("#form-pane .fields input")) {
    const [doc, field] = input.name.split(".");
    const value = clean(input.value);
    if (value !== undefined) payload[doc][field] = field === "currency" ? value.toUpperCase() : value;
  }
  for (const side of ["invoice", "purchase_order"]) {
    const rows = $$(`.lines[data-side="${side}"] .line-rows > div`).map((row) => {
      const item = {};
      $$("input", row).forEach((i) => { const v = clean(i.value); if (v !== undefined) item[i.dataset.key] = v; });
      return item;
    }).filter((item) => Object.keys(item).length);
    if (rows.length) payload[side].line_items = rows;
  }
  if (!Object.keys(payload.purchase_order).length) delete payload.purchase_order;
  return payload;
}

function fillForm(payload) {
  $$("#form-pane .fields input").forEach((input) => {
    const [doc, field] = input.name.split(".");
    const value = payload?.[doc]?.[field];
    input.value = value === undefined || value === null || typeof value === "object" ? "" : String(value);
  });
  for (const side of ["invoice", "purchase_order"]) {
    $(`.lines[data-side="${side}"] .line-rows`).replaceChildren();
    const items = Array.isArray(payload?.[side]?.line_items) ? payload[side].line_items : [];
    items.forEach((item) => addLine(side, item && typeof item === "object" ? item : {}));
    if (!items.length) addLine(side);
  }
}

function setPayload(payload) {
  fillForm(payload);
  $("#json-input").value = JSON.stringify(payload, null, 2);
  $("#json-error").textContent = "";
}

function currentPayload() {
  if (activeTab === "form") return readForm();
  try { return JSON.parse($("#json-input").value.trim()); }
  catch (err) { $("#json-error").textContent = `Invalid JSON: ${err.message}`; return null; }
}

function switchTab(tab) {
  if (tab === activeTab) return;
  if (tab === "json") $("#json-input").value = JSON.stringify(readForm(), null, 2);
  else {
    const payload = currentPayload();
    if (payload === null) return;
    fillForm(payload);
  }
  activeTab = tab;
  $("#json-error").textContent = "";
  $("#form-pane").hidden = tab !== "form";
  $("#json-pane").hidden = tab !== "json";
  $$(".tabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
}

// ---------------------------------------------------------------- JSON scenarios

async function loadSamples() {
  const res = await api("/api/samples");
  const box = $("#samples");
  if (!res.ok) { fill(box, h("span", { class: "muted" }, `Scenarios unavailable: ${detailOf(res)}`)); return; }
  fill(box, h("span", { class: "muted", style: "margin:0 .2rem 0 0" }, "Scenarios:"),
    res.data.map((s) => h("button", {
      type: "button", class: "chip", dataset: { sample: s.name }, title: `Expected: ${RESULT[s.expected_result]?.label}`,
      onclick: () => loadSample(s.name),
    }, h("span", { class: `dot ${RESULT[s.expected_result]?.tone || ""}` }), s.title)));
}

async function loadSample(name) {
  const res = await api(`/api/samples/${encodeURIComponent(name)}`);
  if (!res.ok) { toast(detailOf(res), "error"); return; }
  Object.keys(docs).forEach((doc) => docs[doc] && removeDoc(doc));
  setPayload(res.data);
  $$("#samples .chip").forEach((c) => c.classList.toggle("selected", c.dataset.sample === name));
  setWizard(2);
}

// ---------------------------------------------------------------- documents + OCR

const zone = (doc) => $(`.dropzone[data-doc="${doc}"]`);

function setZone(doc, state, message, method) {
  const el = zone(doc);
  el.classList.remove("loaded", "error", "busy");
  if (state) el.classList.add(state);
  $(".dz-status", el).textContent = message;
  $(".dz-actions", el).hidden = state !== "loaded";
  const title = $(".dz-text strong", el);
  title.querySelector(".badge")?.remove();
  if (method) title.append(methodBadge(method));
  if (state === "error" && !$("#advanced").open) toast(`${DOC_LABEL[doc]}: ${message}`, "error");
}

const methodBadge = (method) => h("span", { class: `badge ${method === "ocr" ? "warn" : "info"}` }, method === "ocr" ? "OCR" : "Text");

/** Step 2 strip: which documents the form data was read from (works while Advanced is collapsed). */
function renderLoadedDocs() {
  const box = $("#loaded-docs");
  const loaded = Object.entries(docs).filter(([, d]) => d?.extraction);
  box.hidden = !loaded.length;
  fill(box, loaded.map(([doc, d]) => {
    const fields = Object.values(d.extraction.fields);
    const found = fields.filter((f) => f.value !== null).length;
    return h("div", { class: "doc-chip" },
      h("span", { class: `doc-tag ${doc === "invoice" ? "inv" : "po"}` }, doc === "invoice" ? "INV" : "PO"),
      h("span", { class: "doc-chip-text" }, h("strong", {}, d.file.name),
        h("small", {}, `${DOC_LABEL[doc]} · ${found}/${fields.length} fields · ${d.extraction.line_items.length} line items`)),
      methodBadge(d.extraction.method),
      h("button", { type: "button", class: "link-btn", onclick: () => showPreview(doc) }, "Preview"),
      h("button", { type: "button", class: "link-btn danger", "aria-label": `Remove ${DOC_LABEL[doc]}`, onclick: () => removeDoc(doc) },
        icon("M6 6l12 12M18 6L6 18")));
  }));
}

function clearConfidence(side) {
  const selector = side ? `[name^="${side}."], [data-side="${side}"] input` : "#form-pane input";
  $$(selector).forEach((input) => {
    delete input.dataset.confidence;
    input.removeAttribute("title");
    if (input.dataset.placeholder !== undefined) input.placeholder = input.dataset.placeholder;
  });
}

function markConfidence(side, extraction) {
  clearConfidence(side);
  for (const [field, info] of Object.entries(extraction.fields)) {
    const input = $(`[name="${side}.${field}"]`);
    if (!input) continue;
    input.dataset.confidence = info.confidence;
    input.title = info.confidence === "missing" ? "Not found in the document. Enter it if you have it."
      : `Read from the document (${info.confidence} confidence): ${info.source || ""}`;
    if (info.confidence === "missing") { input.dataset.placeholder ??= input.placeholder; input.placeholder = "Not found in document"; }
  }
  $$(`.lines[data-side="${side}"] .line-rows input`).forEach((input) => {
    if (input.value) input.dataset.confidence = extraction.line_items_confidence;
  });
}

function renderExtractionSummary() {
  const box = $("#extract-summary");
  const loaded = Object.entries(docs).filter(([, d]) => d?.extraction);
  if (!loaded.length) { box.hidden = true; return; }
  const warnings = loaded.flatMap(([doc, d]) => d.extraction.warnings.map((w) => `${DOC_LABEL[doc]}: ${w}`));
  fill(box,
    h("div", { class: "legend" },
      h("strong", {}, "Extracted values are marked:"),
      h("span", {}, h("i", { class: "c-high" }), "read from a label"),
      h("span", {}, h("i", { class: "c-medium" }), "inferred / OCR, please check"),
      h("span", {}, h("i", { class: "c-missing" }), "not found"),
      h("span", {}, h("i", { class: "c-edited" }), "edited by you")),
    warnings.length ? h("ul", { class: "warn-list" }, warnings.map((w) => h("li", {}, w))) : null);
  box.hidden = false;
}

async function handleFile(doc, file) {
  if (!file) return;
  const okType = /\.(pdf|png|jpe?g|tiff?)$/i.test(file.name || "") || /^(application\/pdf|image\/(png|jpeg|tiff))$/.test(file.type);
  if (!okType) { setZone(doc, "error", "Please choose a PDF, PNG, JPEG or TIFF file."); return; }
  if (file.size > MAX_BYTES) { setZone(doc, "error", "The file is larger than 5 MB."); return; }

  const scan = /^image\//.test(file.type) || /\.(png|jpe?g|tiff?)$/i.test(file.name || "");
  setZone(doc, "busy", scan
    ? `Reading ${file.name} with OCR… a few seconds locally; on the free online server ~20–30 s for a scan, up to 2–3 min for a phone photo.`
    : `Reading ${file.name}… (scanned PDFs use OCR and take longer)`);
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("expected_type", doc);
  const res = await api("/api/documents/extract", { method: "POST", body: form });
  if (!res.ok) { setZone(doc, "error", detailOf(res)); return; }

  const extraction = res.data;
  if (docs[doc]?.url) URL.revokeObjectURL(docs[doc].url);
  docs[doc] = { file, url: URL.createObjectURL(file), extraction };
  if (activeTab !== "form") switchTab("form");
  const payload = readForm();
  payload[doc] = extraction.data;
  if (!payload.invoice) payload.invoice = {};
  fillForm(payload);
  $("#json-input").value = JSON.stringify(readForm(), null, 2);
  Object.entries(docs).forEach(([side, d]) => d?.extraction && markConfidence(side, d.extraction));
  $$("#samples .chip").forEach((c) => c.classList.remove("selected"));

  const found = Object.values(extraction.fields).filter((f) => f.value !== null).length;
  const total = Object.keys(extraction.fields).length;
  const n = extraction.line_items.length;
  setZone(doc, "loaded",
    `${file.name} · ${found}/${total} fields · ${n} line item${n === 1 ? "" : "s"}`
    + (extraction.warnings.length ? ` · ${extraction.warnings.length} note${extraction.warnings.length === 1 ? "" : "s"}` : ""),
    extraction.method);
  renderExtractionSummary();
  renderLoadedDocs();
  setWizard(2);
}

function removeDoc(doc) {
  if (docs[doc]?.url) URL.revokeObjectURL(docs[doc].url);
  docs[doc] = null;
  setZone(doc, null, "Drop a PDF or image, or click to browse");
  clearConfidence(doc);
  renderExtractionSummary();
  renderLoadedDocs();
  if ($("#pdf-preview").dataset.doc === doc) closePreview();
}

function showPreview(doc) {
  const d = docs[doc];
  if (!d) return;
  $("#preview-frame").src = d.file.type.startsWith("image/") ? d.url : `${d.url}#view=FitH`;
  $("#preview-title").textContent = `${DOC_LABEL[doc]}: ${d.file.name}`;
  $("#pdf-preview").dataset.doc = doc;
  $("#pdf-preview").hidden = false;
}

function closePreview() {
  $("#pdf-preview").hidden = true;
  $("#preview-frame").removeAttribute("src");
  delete $("#pdf-preview").dataset.doc;
}

async function fetchAsFile(url, type) {
  const response = await fetch(withToken(url));
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const blob = await response.blob();
  return new File([blob], url.split("/").pop(), { type });
}

let kaggleFiles = [];
let kaggleDatasets = [];
const DATASET_KEY = "nexusai.kaggle.dataset";
let kaggleDataset = "hq";
try { kaggleDataset = localStorage.getItem(DATASET_KEY) || "hq"; } catch { /* storage blocked */ }

async function loadPair(sample) {
  for (const [doc, url] of [["invoice", sample.invoice_url], ["purchase_order", sample.purchase_order_url]]) {
    try { await handleFile(doc, await fetchAsFile(url, "application/pdf")); }
    catch { setZone(doc, "error", "Could not download the sample document."); }
  }
}

async function loadKaggle(file, card) {
  $$("#kaggle-grid .k-card").forEach((c) => c.classList.toggle("selected", c === card));
  if (docs.purchase_order) removeDoc("purchase_order");
  const payload = readForm();
  payload.purchase_order = {};
  fillForm(payload);
  const type = file.file.toLowerCase().endsWith(".png") ? "image/png" : "image/jpeg";
  try { await handleFile("invoice", await fetchAsFile(file.url, type)); }
  catch { setZone("invoice", "error", "Could not download the Kaggle invoice."); return; }
  if ($("#auto-po").checked && docs.invoice) createDemoPO();
  else toast("Real invoice read with OCR. It has no purchase order: use \u201cCreate demo PO from invoice\u201d.");
}

function renderDemoGrid(samples, expected = {}) {
  $("#demo-count").textContent = String(samples.length);
  fill($("#demo-grid"), samples.map((sample) => {
    const result = sample.expected_result || expected[sample.scenario];
    const meta = RESULT[result] || { tone: "neutral", label: result || "Sample" };
    const card = h("button", { type: "button", class: "demo-card", onclick: () => {
      $$("#demo-grid .demo-card").forEach((c) => c.classList.toggle("selected", c === card));
      loadPair(sample);
    } },
      h("span", { class: `demo-dot ${meta.tone}` }),
      h("span", { class: "demo-text" }, h("strong", {}, sample.title),
        h("span", { class: "demo-sub" }, h("span", { class: `badge ${meta.tone}` }, meta.label), h("small", {}, "Invoice + PO · PDF"))));
    return card;
  }));
}

/** Dataset totals come as '6 204,19' or '6860.45': show them in one format. */
function displayAmount(raw) {
  let value = String(raw).replace(/[\s $]/g, "");
  if (/,\d{1,2}$/.test(value)) value = value.replace(/\./g, "").replace(",", ".");
  else value = value.replace(/,/g, "");
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : raw;
}

function renderDatasetChips() {
  fill($("#kaggle-datasets"), kaggleDatasets.map((d) => {
    const on = d.key === kaggleDataset;
    return h("button", { type: "button", role: "radio", class: `dataset-chip ${on ? "active" : ""}`, "aria-checked": String(on),
      title: `${d.description} Licence: ${d.license}.`, onclick: () => selectDataset(d.key) },
    h("strong", {}, d.title), h("small", {}, d.kind));
  }));
}

function renderKaggleGrid(data) {
  kaggleFiles = data.files || [];
  $("#kaggle-count").textContent = String(kaggleFiles.length);
  $("#kaggle-attribution").textContent = data.attribution
    ? `Source: ${data.attribution}${data.labelled ? "" : " No ground-truth labels: OCR reads everything."}` : "";
  if (!kaggleFiles.length) {
    fill($("#kaggle-grid"), h("div", { class: "k-empty" },
      h("strong", {}, data.configured === false ? "Kaggle is not configured" : "No invoices loaded from this dataset yet"),
      h("p", { class: "muted" }, data.configured === false ? "Set KAGGLE_API_KEY in backend/.env."
        : data.hint || "Press \u201cRefresh\u201d to load a random set.")));
    return;
  }
  fill($("#kaggle-grid"), kaggleFiles.map((file) => {
    const card = h("button", { type: "button", class: "k-card", title: `Load ${file.file}`, onclick: () => loadKaggle(file, card) },
      h("span", { class: "k-thumb" }, h("img", { src: withToken(file.url), alt: `Invoice ${file.invoice_number || file.file}`, loading: "lazy" })),
      h("span", { class: "k-meta" },
        h("strong", {}, file.seller || data.kind || file.file),
        h("small", {}, [file.invoice_number ? `#${file.invoice_number}` : file.file, file.items ? `${file.items} items` : ""]
          .filter(Boolean).join(" · ")),
        h("small", { class: "k-total" }, file.total ? `${String(file.total).includes("$") ? "$ " : ""}${displayAmount(file.total)}` : "")));
    return card;
  }));
}

async function refreshKaggle() {
  const res = await api(`/api/samples/kaggle/list?dataset=${kaggleDataset}`);
  renderKaggleGrid(res.ok ? res.data : { files: [], hint: detailOf(res) });
  return res.ok ? res.data.files.length : 0;
}

async function loadDatasets() {
  const res = await api("/api/samples/kaggle/datasets");
  kaggleDatasets = res.ok ? res.data : [];
  if (!kaggleDatasets.some((d) => d.key === kaggleDataset)) kaggleDataset = kaggleDatasets[0]?.key || "hq";
  renderDatasetChips();
  return refreshKaggle();
}

async function selectDataset(key) {
  kaggleDataset = key;
  try { localStorage.setItem(DATASET_KEY, key); } catch { /* not remembered */ }
  renderDatasetChips();
  if (!(await refreshKaggle())) await loadNewSet(key); // nothing cached for this dataset yet
}

/** Download a fresh random set from one dataset (replaces its previous set). */
async function loadNewSet(key) {
  const button = $("#kaggle-fetch");
  const title = kaggleDatasets.find((d) => d.key === key)?.title || key;
  button.disabled = true;
  fill(button, h("span", { class: "spinner", "aria-hidden": "true" }), h("span", {}, "Loading from Kaggle\u2026"));
  const res = await api(`/api/samples/kaggle/refresh?dataset=${key}&count=${$("#kaggle-fetch-count").value}`, { method: "POST" });
  button.disabled = false;
  fill(button, "\u21bb Refresh");
  if (!res.ok) { toast(detailOf(res), "error"); return; }
  toast(`Loaded ${res.data.added} new invoices from ${title}.`);
  await refreshKaggle();
}

/** Refresh: move on to the next dataset and load a fresh random set, so every refresh looks different. */
async function nextKaggleSet() {
  const keys = kaggleDatasets.map((d) => d.key);
  const next = keys.length ? keys[(keys.indexOf(kaggleDataset) + 1) % keys.length] : kaggleDataset;
  kaggleDataset = next;
  try { localStorage.setItem(DATASET_KEY, next); } catch { /* not remembered */ }
  renderDatasetChips();
  await loadNewSet(next);
}

async function loadSampleDocs() {
  const [synthetic, scenarios] = await Promise.all([api("/api/samples/pdfs/list"), api("/api/samples"), loadDatasets()]);
  const expected = scenarios.ok ? Object.fromEntries(scenarios.data.map((s) => [s.name, s.expected_result])) : {};
  if (synthetic.ok) renderDemoGrid(synthetic.data, expected);
  $("#source-tabs").addEventListener("click", (e) => {
    const tab = e.target.closest("button[data-source]");
    if (!tab) return;
    $$("#source-tabs button").forEach((b) => b.classList.toggle("active", b === tab));
    $$(".source-pane").forEach((pane) => { pane.hidden = pane.dataset.pane !== tab.dataset.source; });
    // The online server starts with an empty disk after a restart: fetch a set the first time the tab opens.
    if (tab.dataset.source === "kaggle" && !kaggleFiles.length && !$("#kaggle-fetch").disabled) loadNewSet(kaggleDataset);
  });
  $("#kaggle-fetch").addEventListener("click", nextKaggleSet);
}

/** Kaggle-style invoices have no PO: build a matching demo PO from the invoice (marked as edited). */
function createDemoPO() {
  const payload = readForm();
  const inv = payload.invoice || {};
  if (!inv.invoice_number && !(inv.line_items || []).length) { toast("Load or enter an invoice first.", "error"); return; }
  const hadReference = Boolean(inv.po_reference);
  const poNumber = inv.po_reference || `PO-DEMO-${inv.invoice_number || "1"}`;
  payload.purchase_order = {
    po_number: poNumber,
    supplier_name: inv.supplier_name,
    currency: inv.currency,
    total_amount: inv.total_amount,
    line_items: (inv.line_items || []).map((l) => ({ sku: l.sku, quantity: l.quantity, unit_price: l.unit_price })),
  };
  if (!hadReference) payload.invoice.po_reference = poNumber;
  fillForm(payload);
  Object.entries(docs).forEach(([side, d]) => d?.extraction && markConfidence(side, d.extraction));
  $$('[name^="purchase_order."], [data-side="purchase_order"] input').forEach((i) => { if (i.value) i.dataset.confidence = "edited"; });
  const ref = $('[name="invoice.po_reference"]');
  if (!hadReference) { ref.dataset.confidence = "edited"; ref.title = "Demo PO reference added by you"; }
  $("#json-input").value = JSON.stringify(readForm(), null, 2);
  toast("Demo PO created from the invoice. Change a quantity or price to see the agents catch it.");
  setWizard(2);
}

// ---------------------------------------------------------------- run review

function startFlow() {
  const order = ["api", "coordinator", "verifier", "storage"];
  let index = 0;
  const tick = () => {
    const states = Object.fromEntries(order.map((id, i) => [id, i < index ? "done" : i === index ? "active" : "pending"]));
    fill($("#result"),
      h("div", { class: "card-head" }, h("div", {}, h("h3", {}, "Reviewing…"),
        h("p", { class: "muted" }, "Following the request across both agents."))),
      renderSteps({ ...states, result: "pending" }));
    if (index < order.length - 1) index += 1;
  };
  tick();
  const timer = setInterval(tick, 600);
  return () => clearInterval(timer);
}

async function submit(event) {
  event.preventDefault();
  const payload = currentPayload();
  if (payload === null) return;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    toast("The request must be a JSON object with 'invoice' and 'purchase_order'.", "error");
    return;
  }
  setWizard(3);
  const button = $("#submit-btn");
  button.disabled = true;
  fill(button, h("span", { class: "spinner", "aria-hidden": "true" }), h("span", {}, "Reviewing…"));
  const stop = startFlow();
  $("#result").scrollIntoView({ behavior: "smooth", block: "start" });
  const started = performance.now();
  const res = await api("/api/reviews", { method: "POST", body: JSON.stringify(payload) });
  const elapsed = performance.now() - started;
  if (elapsed < 1400) await new Promise((r) => setTimeout(r, 1400 - elapsed)); // let the path be seen
  stop();
  if ((res.status === 200 || res.status === 422) && res.data?.verification_result) {
    fill($("#result"), renderSubmission(res.data, { onOpenSaved: hooks.onOpenSaved, onDashboard: hooks.onDashboard, payload }));
    $$(".wizard li").forEach((li) => { li.classList.remove("active"); li.classList.add("done"); });
    hooks.onReviewed();
  } else {
    fill($("#result"), renderError(res.status, detailOf(res)));
    setWizard(2);
  }
  button.disabled = false;
  fill(button, h("span", { class: "btn-label" }, "Run review"));
}

// ---------------------------------------------------------------- init

export function initReview(options) {
  hooks = { ...hooks, ...options };
  fill($("#result"), emptyState());
  $$(".tabs button").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));
  $$("[data-add-line]").forEach((b) => b.addEventListener("click", () => addLine(b.dataset.addLine)));
  $("#review-form").addEventListener("submit", submit);
  $("#reset-btn").addEventListener("click", () => {
    Object.keys(docs).forEach((doc) => docs[doc] && removeDoc(doc));
    setPayload({ invoice: {}, purchase_order: {} });
    $$("#samples .chip").forEach((c) => c.classList.remove("selected"));
    fill($("#result"), emptyState());
    setWizard(1);
  });
  for (const doc of Object.keys(docs)) {
    const el = zone(doc);
    const input = $("input[type=file]", el);
    el.addEventListener("click", (e) => { if (!e.target.closest(".dz-actions")) input.click(); });
    el.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
    input.addEventListener("change", () => { handleFile(doc, input.files[0]); input.value = ""; });
    el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("dragover"); });
    el.addEventListener("dragleave", () => el.classList.remove("dragover"));
    el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("dragover"); handleFile(doc, e.dataTransfer.files[0]); });
    $('[data-action="view"]', el).addEventListener("click", () => showPreview(doc));
    $('[data-action="remove"]', el).addEventListener("click", () => removeDoc(doc));
  }
  $("#preview-close").addEventListener("click", closePreview);
  $("#demo-po").addEventListener("click", createDemoPO);
  $("#form-pane").addEventListener("input", (e) => {
    if (e.target.matches("input[data-confidence]")) e.target.dataset.confidence = "edited";
  });
  fillForm({});
  loadSampleDocs();
  loadSamples().then(() => loadSample("matching"));
}
