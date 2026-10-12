// NexusAI web app: hash router, theme, live status, and the review drawer.
import { renderSavedReview } from "./components/result.js";
import { api, detailOf, hasToken, setToken } from "./lib/api.js";
import { $, $$, fill, h, toast } from "./lib/dom.js";
import { initHistory, refreshHistory } from "./views/history.js";
import { initOverview, refreshOverview, rethemeOverview, updatePipeline } from "./views/overview.js";
import { hideHow, showHow } from "./views/how.js";
import { initReview } from "./views/review.js";
import { refreshSystem } from "./views/system.js";

const ROUTES = {
  overview: { title: "Overview", crumb: "Workspace", load: () => refreshOverview() },
  review: { title: "New review", crumb: "Workspace", load: () => {} },
  history: { title: "History", crumb: "Workspace", load: () => refreshHistory() },
  how: { title: "How it works", crumb: "Platform", load: () => showHow(health) },
  system: { title: "Agents & system", crumb: "Platform", load: () => refreshSystem(health) },
};
let health = null;
let current = null;

// ---------------------------------------------------------------- router

function route() {
  const name = (location.hash.replace(/^#\/?/, "") || "overview").split("?")[0];
  const target = ROUTES[name] ? name : "overview";
  $$(".view").forEach((v) => { v.hidden = v.dataset.view !== target; });
  $$(".nav a[data-route]").forEach((a) => a.classList.toggle("active", a.dataset.route === target));
  $("#page-title").textContent = ROUTES[target].title;
  $("#crumb").textContent = `NexusAI / ${ROUTES[target].crumb}`;
  document.title = `${ROUTES[target].title} · NexusAI`;
  $("#top-new").hidden = target === "review";
  closeMenu();
  if (current !== target) window.scrollTo({ top: 0 });
  if (target !== "how") hideHow();
  current = target;
  ROUTES[target].load();
}

// ---------------------------------------------------------------- status

async function refreshStatus() {
  const res = await api("/api/health");
  health = res.ok ? res.data : null;
  const pill = $("#health-pill");
  const dot = (svc, tone, title) => {
    const li = $(`#side-status [data-svc="${svc}"]`);
    $(".dot", li).className = `dot ${tone}`;
    li.title = title;
  };
  if (!health) {
    pill.className = "health-pill bad";
    pill.textContent = "API offline";
    ["coordinator", "verifier", "storage", "ocr", "reasoning"].forEach((svc) => dot(svc, "bad", detailOf(res)));
  } else {
    const allUp = health.status === "ok";
    pill.className = `health-pill ${allUp ? "ok" : "warn"}`;
    pill.textContent = allUp ? "All systems operational" : "Degraded";
    dot("coordinator", health.coordinator === "online" ? "ok" : "bad", `Agent A ${health.coordinator}`);
    dot("verifier", health.verifier === "online" ? "ok" : "bad", `Agent B ${health.verifier}`);
    dot("storage", { connected: "ok", not_configured: "warn", error: "bad" }[health.storage], `Supabase ${health.storage.replace("_", " ")}`);
    dot("ocr", health.ocr === "available" ? "ok" : "warn", health.ocr === "available" ? "Tesseract OCR available" : "OCR not installed");
    dot("reasoning", health.reasoning === "llm" ? "ok" : "warn",
      health.reasoning === "llm" ? `${health.reasoning_provider || "LLM"} · ${health.reasoning_model}` : `Built-in writer: ${health.reasoning_detail || ""}`);
  }
  updatePipeline(health);
  if (current === "system") refreshSystem(health);
}

// ---------------------------------------------------------------- drawer

async function openReview(taskId) {
  const drawer = $("#drawer");
  const body = $("#drawer-body");
  fill(body, h("div", { class: "skeleton", style: "height:120px" }), h("div", { class: "skeleton", style: "height:220px;margin-top:14px" }));
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  $("#drawer-scrim").hidden = false;
  $("#drawer-close").focus();
  const res = await api(`/api/reviews/${encodeURIComponent(taskId)}`);
  fill(body, res.ok ? renderSavedReview(res.data)
    : h("div", { class: "error-box" }, h("h3", {}, "Could not load review"), h("p", {}, detailOf(res))));
}

function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#drawer-scrim").hidden = true;
}

// ---------------------------------------------------------------- theme + menu

function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem("nexusai-theme"); } catch { /* storage blocked */ }
  if (saved) document.documentElement.dataset.theme = saved;
  $("#theme-toggle").addEventListener("click", () => {
    const now = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = now === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("nexusai-theme", next); } catch { /* storage blocked */ }
    rethemeOverview();
  });
}

const closeMenu = () => { $("#sidebar").classList.remove("open"); $("#scrim").hidden = true; };

function init() {
  initTheme();
  $("#menu-btn").addEventListener("click", () => { $("#sidebar").classList.add("open"); $("#scrim").hidden = false; });
  $("#scrim").addEventListener("click", closeMenu);
  $("#drawer-close").addEventListener("click", closeDrawer);
  $("#drawer-scrim").addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("#system-refresh").addEventListener("click", () => refreshSystem(health));
  $("#signin-form").addEventListener("submit", signIn);
  $("#signout").addEventListener("click", () => { setToken(null); location.reload(); });
  window.addEventListener("nexusai:signin-required", showSignin);
  window.addEventListener("hashchange", () => { if (started) route(); });
  start();
  setInterval(refreshStatus, 15000);
}

// ---------------------------------------------------------------- sign-in (backend ACCESS_PASSWORD)

let started = false;

/** Load the pages only once the API can be used: open API, or a valid token. */
async function start() {
  await refreshStatus();
  if (health?.auth_required) {
    $("#signout").hidden = false;
    const status = hasToken() ? await api("/api/auth/status") : null;
    if (!status?.data?.signed_in) { showSignin(); return; }
  }
  startViews();
}

function startViews() {
  if (started) return;
  started = true;
  initOverview({ onOpenReview: openReview });
  initHistory({ onOpenReview: openReview });
  initReview({
    onOpenSaved: openReview,
    onDashboard: () => { location.hash = "#/overview"; },
    onReviewed: async () => {
      refreshStatus();
      await refreshOverview(); // the dashboard is already up to date when the user opens it
      toast("Dashboard updated with this review.");
    },
  });
  route();
}

function showSignin() {
  $("#signin").hidden = false;
  $("#signin-error").hidden = true;
  setTimeout(() => $("#signin-password").focus(), 50);
}

async function signIn(event) {
  event.preventDefault();
  const button = $("#signin-submit");
  const password = $("#signin-password");
  button.disabled = true;
  const res = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ password: password.value }) });
  button.disabled = false;
  if (!res.ok) {
    const error = $("#signin-error");
    error.textContent = res.status === 401 ? "Wrong password. Please try again." : detailOf(res);
    error.hidden = false;
    password.select();
    return;
  }
  setToken(res.data.token);
  password.value = "";
  $("#signin").hidden = true;
  if (started) route(); // the token had expired while the app was open: just reload the page's data
  else startViews();
  refreshStatus();
}

// Chart.js is loaded with `defer`; module scripts also run after parsing, but wait for it to be safe.
if (window.Chart) init();
else window.addEventListener("load", init, { once: true });
