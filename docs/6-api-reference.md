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
| `GET /api/health` | API, both agents, Supabase, OCR and AI status | `200` (`ok` / `degraded`) |
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

## How the API protects the system

- **No direct access:** the browser never reaches the agents or Supabase; secrets stay in `backend/.env`.
- **CORS:** only origins in `API_CORS_ORIGINS`, only `GET` / `POST`.
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
