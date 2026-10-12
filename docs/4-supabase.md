# 4 · Supabase: saving every review

[← 3 · Agent B](3-agent-b-verification.md) · [README](../README.md) · Next: [5 · AI reasoning](5-ai-reasoning.md)

Agent B saves every final review to the `verification_reviews` table. The API then adds Agent A's
outcome (action, risk, reasoning) to the same row, so the dashboard and history can show it.

Code: [`backend/app/persistence/reviews.py`](../backend/app/persistence/reviews.py) ·
SQL: [`backend/supabase/migrations/`](../backend/supabase/migrations/)

## One-time setup

1. **Apply both migrations** in the Supabase dashboard: **SQL Editor → New query**, paste the
   file, **Run**. Both run in a transaction and are safe to re-run.
   - `001_create_verification_reviews.sql` — the table, constraints, indexes, RLS
   - `002_add_coordinator_reasoning.sql` — Agent A's columns (action, risk, reasoning)
2. **Add credentials** to `backend/.env` from **Project Settings → API Keys** (a *secret* key):
   ```dotenv
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_SECRET_KEY=sb_secret_...
   ```
   The legacy `service_role` JWT is also accepted. Publishable / `anon` keys are refused at
   startup. The key is never logged and never reaches the browser.
3. **Verify** (real write, read, idempotency check and cleanup):
   ```powershell
   cd backend
   python scripts\1_check_supabase.py
   ```

## What the table holds

- **One row per final Agent B task**, `a2a_task_id` unique: status, summary, invoice metadata,
  and `findings` / `validation_errors` / `assumptions` as jsonb. Line items aren't stored.
- **Agent A's outcome** (migration 002): `coordinator_task_id`, `recommended_action`,
  `risk_level`, `reasoning_source` and `reasoning` (jsonb).
- **Constraints:** the four statuses stay distinct, `REJECTED` goes with `INVALID_REQUEST`,
  currency is a 3-letter code, `invoice_total` ≥ 0, jsonb columns have the right shape.
- **Access:** Row Level Security on with **no policies**; privileges revoked from `anon` and
  `authenticated`. Only the backend's secret key can read or write.

## How saving behaves

- **Idempotent:** an upsert on the A2A task ID, so a retry never creates a duplicate.
- **What's saved:** `COMPLETED` and `REJECTED` tasks (`FAILED` tasks have no result).
- **A failed save never hides the result:** the verification is still returned, the failure is
  logged at `ERROR` and reported in `metadata.persistence` (`saved` / `failed` / `disabled`).
- **Before migration 002 nothing breaks:** reviews still save; the UI shows which file to apply.

| Agent B startup log | Meaning |
|---|---|
| `WARNING ... persistence DISABLED` | Supabase values blank; verification works, nothing saved |
| `INFO ... persistence enabled for <host>` | Connected; every final review is saved |
| `ERROR ... enabled ... but NOT READY: ...` | Wrong key, URL or table, or no network |
| `ERROR ... Invalid Supabase configuration` | Server exits; fix `.env` |

## Read a saved review

Reviews are stored under **Agent B's** task ID (the `Agent B :` line of the client output).

```powershell
python scripts\5_get_verification_review.py --task-id <AGENT_B_TASK_ID>
python scripts\5_get_verification_review.py --task-id <AGENT_B_TASK_ID> --json
```
