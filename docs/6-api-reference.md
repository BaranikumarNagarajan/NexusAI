# 6 · NexusAI API (FastAPI gateway)

[← 5 · AI reasoning](5-ai-reasoning.md) · [README](../README.md) · Next: [7 · Web app](7-web-app.md)

The browser talks only to this API; the API talks to Agent A over A2A and reads Supabase. The
agents and the Supabase key are never exposed to the browser.

Code: [`backend/app/api/`](../backend/app/api/) — `main.py` (app, CORS, limits, serves the web
app), `routes.py` (endpoints), `schemas.py` (response models), `gateway.py` (A2A calls to Agent A).

Interactive docs: **<http://127.0.0.1:8080/docs>** (every endpoint can be tried there).

## Endpoints

| Method & path | Purpose | Responses |
|---|---|---|
| `GET /api/health` | API, both agents, Supabase, OCR and AI status; `auth_required` | `200` (`ok` / `degraded`) — always public |
| `POST /api/auth/login` | `{password}` → `{token, expires_in}` | `200` · `401` wrong · `404` no password set · `429` too many tries |
| `GET /api/auth/status` | Is a password needed, and is this token valid? | `200` — always public |
| `GET /api/agents` | Both Agent Cards, discovered live | `200` |
| `POST /api/reviews` | Submit `{invoice, purchase_order}` → Agent A → Agent B, both over A2A | `200` · `422` `INVALID_REQUEST` · `502` agent failed · `503` Agent A down |
| `GET /api/reviews?limit=&offset=&status=` | Saved reviews, newest first, with `total` | `200`, `502`, `503` |
| `GET /api/reviews/{task_id}` | One saved review by **Agent B task ID** | `200`, `404`, `502`, `503` |
| `GET /api/stats` | Saved reviews per result | `200`, `502`, `503` |
| `GET /api/analytics?days=14` | Dashboard aggregates (per day, top issues, supplier health, value, risk, actions) | `200`, `502`, `503` |
| `POST /api/documents/extract` | Upload a PDF or image (`file`, optional `expected_type`) → fields with confidence | `200` · `413` > 5 MB · `415` wrong type · `422` unreadable |
| `GET /api/samples` · `GET /api/samples/{name}` | The six JSON scenarios | `200`, `404` |
| `GET /api/samples/pdfs/list` · `GET /api/samples/pdfs/{file}` | The demo PDF pairs | `200`, `404` |
| `GET /api/samples/kaggle/datasets` | The four Kaggle datasets | `200` |
| `GET /api/samples/kaggle/list?dataset=hq` | The current set of one dataset | `200`, `404` |
| `POST /api/samples/kaggle/refresh?dataset=hq&count=6` | Load a fresh random set (token stays on the server) | `200`, `400`, `502` |
| `GET /api/samples/kaggle/{dataset}/{file}` | One downloaded invoice image | `200`, `404` |

A `POST /api/reviews` response contains `recommended_action`, `verification_result`, `summary`,
`action_items`, `task_id` (Agent A), `verifier_task_id` (Agent B), `persistence`,
`verification` (Agent B's findings), `reasoning` ([doc 5](5-ai-reasoning.md)) and `disclaimer`.

## Access password and rate limits

For a public deployment set **`ACCESS_PASSWORD`** on the backend. The web app then shows a sign-in
screen, and every `/api/*` call except `health` and `auth/*` needs a token (`401` otherwise).

- **Token:** `POST /api/auth/login` returns a signed token (HMAC-SHA256) valid for
  `ACCESS_TOKEN_HOURS` (12 h). The web app sends it as `Authorization: Bearer …`; image links use
  `?access_token=…`, which is masked as `***` in the server log. Changing the password signs
  everyone out. In `/docs`, use **Authorize** with the token.
- **Rate limits** (per visitor IP; `*_TOTAL` = per day across all visitors). Over the limit →
  `429` with `Retry-After`:

| Rule | Default | Protects |
|---|---|---|
| `RATE_LIMIT_REVIEWS` / `RATE_LIMIT_REVIEWS_TOTAL` | `6/minute` / `300/day` | AI model quota (Ollama) and Supabase writes |
| `RATE_LIMIT_KAGGLE` / `RATE_LIMIT_KAGGLE_TOTAL` | `4/minute` / `100/day` | Kaggle downloads |
| `RATE_LIMIT_OCR` | `20/minute` | CPU (Tesseract) |
| `RATE_LIMIT_LOGIN` | `5/minute` | Password guessing |

- Behind a hosting proxy (Render, Railway, Fly.io) set `TRUST_PROXY_HEADERS=true` so limits use
  the visitor's real IP. Counters live in memory and reset on restart.
- Without `ACCESS_PASSWORD` (local development) the API is open and logs a warning.

## How the API protects the system

- **No direct access:** the browser never reaches the agents or Supabase; secrets stay in `backend/.env`.
- **Access password + rate limits:** see above.
- **CORS:** only origins in `API_CORS_ORIGINS`, only `GET` / `POST`; error replies carry CORS headers too.
- **Size limits:** JSON bodies over `MAX_REQUEST_BYTES` → `413`; uploads over 5 MB → `413`.
- **No internal details leak:** storage errors return a generic `502`; details go to the log.
- **Whitelisted inputs:** sample names, dataset keys, file names and task IDs are validated.
- **Exact money:** `invoice_total` comes back as a decimal string at currency precision (`"880.00"`).

## Quick check from PowerShell

```powershell
Invoke-RestMethod http://127.0.0.1:8080/api/health
$body = Get-Content backend\samples\json\matching.json -Raw
Invoke-RestMethod http://127.0.0.1:8080/api/reviews -Method Post -ContentType application/json -Body $body
```
