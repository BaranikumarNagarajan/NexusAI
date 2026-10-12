"""Live Supabase check: confirms credentials, the migration and a real write/read.

Writes one synthetic review (task id prefixed "supabase-check-"), reads it back,
saves it again to confirm idempotency, then deletes it.

Usage (from backend/, after filling SUPABASE_URL / SUPABASE_SECRET_KEY in .env
and applying supabase/migrations/001_create_verification_reviews.sql):
    python scripts/1_check_supabase.py
"""

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.invoice_verifier.verification import verify_payload  # noqa: E402
from app.config import ConfigurationError, load_supabase_settings  # noqa: E402
from app.persistence.reviews import (  # noqa: E402
    TABLE,
    PersistenceError,
    build_review_row,
    create_review_repository,
)

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "json" / "matching.json"


def step(text: str) -> None:
    print(f"[ OK ] {text}")


def main() -> None:
    try:
        settings = load_supabase_settings()
    except ConfigurationError as exc:
        sys.exit(f"[FAIL] Invalid Supabase configuration: {exc}")
    if settings is None:
        sys.exit("[FAIL] SUPABASE_URL and SUPABASE_SECRET_KEY are not set in backend/.env")
    step(f"Configuration loaded (key type: {settings.key_type})")

    repo = create_review_repository(settings)
    task_id = f"supabase-check-{uuid.uuid4()}"
    payload = json.loads(SAMPLE.read_text(encoding="utf-8"))
    row = build_review_row(task_id=task_id, context_id="supabase-check", result=verify_payload(payload), payload=payload)
    try:
        repo.check_ready()
        step("Connected; table verification_reviews is reachable")
        saved = repo.save(row)
        step(f"Wrote review {saved['id']} for task {task_id}")
        fetched = repo.get_by_task_id(task_id)
        if fetched is None or fetched["id"] != saved["id"]:
            sys.exit("[FAIL] The review was written but could not be read back")
        step(f"Read it back: status {fetched['verification_status']}, total {fetched['invoice_total']}")
        again = repo.save(row)
        if again["id"] != saved["id"]:
            sys.exit("[FAIL] Saving the same task created a second record (idempotency broken)")
        step("Saved the same task again: still one record (idempotent)")
    except PersistenceError as exc:
        sys.exit(f"[FAIL] {exc}")
    finally:
        try:
            repo._client.table(TABLE).delete().eq("a2a_task_id", task_id).execute()
        except Exception as exc:  # cleanup is best-effort; report but don't mask the real outcome
            print(f"[WARN] Could not delete the synthetic review {task_id}: {type(exc).__name__}")

    if repo.get_by_task_id(task_id) is None:
        step("Deleted the synthetic review")
    print("\nSupabase persistence is working.")


if __name__ == "__main__":
    main()
