# 9 · Testing and verification

[← 8 · Deployment](8-deployment.md) · [README](../README.md)

The automated test suite (150 tests) was run during development and then removed from the
repository at the owner's request, to keep the portfolio tree lean. Every later change was
verified with temporary tests and live runs, recorded here.

## Automated tests (run before removal): 150 passed

- All checks, rounding and validation edge cases of Agent B.
- Mocked Supabase persistence: mapping, configuration and key validation, duplicate tasks,
  insert failures, retrieval of existing and missing records.
- Agent B over real HTTP; **both agents as real servers**, with Agent A delegating every
  scenario to Agent B.
- Agent B unreachable → Agent A `FAILED`; an Agent Card missing the skill → `FAILED`;
  non-JSON requests rejected by Agent A without contacting Agent B.

## Live checks

| Area | Result |
|---|---|
| A2A, three processes | All 6 scenarios give the expected result through Agent B directly and through Agent A; every Agent B task ID matched its log |
| Supabase | Live write, read-back, idempotent re-save and cleanup (`1_check_supabase.py`); reviews read back by task ID |
| API | Every endpoint, `422` / `502` / `503`, CORS, the `413` limits; no secret in any log |
| PDF extraction | All 10 sample PDFs extract exactly; wrong document type, damaged files, oversize and path traversal rejected |
| OCR | 195/196 synthetic fields as images; 24/24 invoice numbers and totals on labelled Kaggle scans; sideways phone photos turned upright |
| AI reasoning (Ollama Cloud) | All scenarios written by `gpt-oss:120b` in ~4–6 s; fact check verified 17–24 figures per review; a real self-correction round passed; built-in writer used when the model is unavailable |
| Web app (headless Edge) | Full review through the UI, report tabs, history drawer, Kaggle refresh, How it works speeds; no console errors |

## How to re-check by hand

```powershell
cd D:\NexusAI
.\.venv\Scripts\Activate.ps1
python backend\run.py                                              # everything at http://127.0.0.1:8080
cd backend
python scripts\1_check_supabase.py                                 # database round trip
python scripts\4_send_verification_task.py --agent coordinator --brief   # 6 scenarios over A2A
```

## Known limitations

- Tasks are kept in memory by both agents (`InMemoryTaskStore`) and are lost on restart; reviews
  persist in Supabase.
- No streaming, push notifications or authentication between agents (local development).
- Agent A discovers Agent B on every request; no retry if Agent B is down.
- Supplier matching is normalised string equality; tax is one invoice-level amount; discounts
  aren't modelled.
- Migrations are applied by hand in the SQL Editor.
- OCR on creased phone photos is partial — the colour-marked review step is there for a person
  to correct values before verification.
