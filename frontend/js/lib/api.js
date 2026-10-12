// The only module that talks to the backend. The page talks only to the NexusAI API (/api/*).

// config.js sets apiBase for a separately hosted backend. Otherwise: served by the API itself ->
// same origin; opened from a dev server (Live Server, Vite) or a file -> the local API.
const configured = (window.NEXUSAI_CONFIG?.apiBase || "").replace(/\/+$/, "");
export const API_BASE = configured
  || (["5500", "5173"].includes(location.port) || location.protocol === "file:" ? "http://127.0.0.1:8080" : "");

// ---------------------------------------------------------------- access token
// Only used when the backend has ACCESS_PASSWORD set. Kept per browser for its lifetime (12 h by default).
const TOKEN_KEY = "nexusai.token";
let token = null;
try { token = localStorage.getItem(TOKEN_KEY); } catch { /* storage blocked: sign in each visit */ }

export function setToken(value) {
  token = value;
  try { value ? localStorage.setItem(TOKEN_KEY, value) : localStorage.removeItem(TOKEN_KEY); } catch { /* not kept */ }
}
export const hasToken = () => Boolean(token);

/** Absolute URL for links that cannot send headers (<img src>), with the token when there is one. */
export function withToken(path) {
  const url = API_BASE + path;
  return token ? `${url}${url.includes("?") ? "&" : "?"}access_token=${encodeURIComponent(token)}` : url;
}

// ---------------------------------------------------------------- requests

export async function api(path, options = {}) {
  let response;
  try {
    const headers = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    response = await fetch(API_BASE + path, { ...options, headers: { ...headers, ...(options.headers || {}) } });
  } catch {
    return { ok: false, status: 0, data: { detail: "Cannot reach the NexusAI API. Start it with: python backend\\run.py" } };
  }
  let data = null;
  try { data = await response.json(); } catch { /* empty or non-JSON body */ }
  if (response.status === 401 && data?.auth_required) {
    setToken(null);
    window.dispatchEvent(new CustomEvent("nexusai:signin-required")); // app.js shows the sign-in screen
  }
  return { ok: response.ok, status: response.status, data };
}

export const detailOf = (res) =>
  (res?.data && typeof res.data.detail === "string" ? res.data.detail : `HTTP ${res?.status ?? "?"}`);
