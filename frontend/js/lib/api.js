// The only module that talks to the backend. The page talks only to the NexusAI API (/api/*).

// Served by the API itself -> same origin. Opened from a dev server or file -> default API URL.
export const API_BASE = ["5500", "5173"].includes(location.port) || location.protocol === "file:"
  ? "http://127.0.0.1:8080"
  : "";

export async function api(path, options = {}) {
  let response;
  try {
    const headers = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
    response = await fetch(API_BASE + path, { headers, ...options });
  } catch {
    return { ok: false, status: 0, data: { detail: "Cannot reach the NexusAI API. Start it with: python backend\\run.py" } };
  }
  let data = null;
  try { data = await response.json(); } catch { /* empty or non-JSON body */ }
  return { ok: response.ok, status: response.status, data };
}

export const detailOf = (res) =>
  (res?.data && typeof res.data.detail === "string" ? res.data.detail : `HTTP ${res?.status ?? "?"}`);
