"""Retrieve a saved verification review from Supabase by its A2A task ID.

Usage (from the project root, virtual environment activated):
    python scripts/5_get_verification_review.py --task-id <TASK_ID>
    python scripts/5_get_verification_review.py --task-id <TASK_ID> --json
"""

import argparse
import json
import sys
from pathlib import Path

# Allow running as a plain script from the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ConfigurationError, load_supabase_settings  # noqa: E402
from app.persistence.reviews import PersistenceError, create_review_repository  # noqa: E402


def print_review(review: dict) -> None:
    print(f"Task ID        : {review['a2a_task_id']}")
    print(f"Review ID      : {review['id']}")
    print(f"Created at     : {review['created_at']}")
    print(f"Task state     : {review['task_state']}")
    print(f"Status         : {review['verification_status']}")
    print(f"Invoice        : {review.get('invoice_number') or '-'}   PO: {review.get('po_number') or '-'}")
    print(f"Supplier       : {review.get('supplier_name') or '-'}")
    total = review.get("invoice_total")
    currency = review.get("currency") or ""
    print(f"Invoice total  : {f'{total} {currency}'.strip() if total is not None else '-'}")
    print(f"Summary        : {review['summary']}")
    findings = review.get("findings") or []
    if findings:
        print("Findings:")
        for finding in findings:
            print(f"  {finding['outcome']:<20} {finding['check']:<26} {finding['explanation']}")
    for issue in review.get("validation_errors") or []:
        print(f"  INVALID              {issue['field']:<26} {issue['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show a saved NexusAI verification review.")
    parser.add_argument("--task-id", required=True, help="Agent B task ID printed by 4_send_verification_task.py")
    parser.add_argument("--json", action="store_true", help="print the raw stored row as JSON")
    args = parser.parse_args()

    task_id = args.task_id.strip()
    if not task_id:
        sys.exit("--task-id must not be empty")

    try:
        settings = load_supabase_settings()
    except ConfigurationError as exc:
        sys.exit(f"Invalid Supabase configuration: {exc}")
    if settings is None:
        sys.exit("Supabase is not configured: set SUPABASE_URL and SUPABASE_SECRET_KEY in .env (see README).")

    try:
        review = create_review_repository(settings).get_by_task_id(task_id)
    except PersistenceError as exc:
        sys.exit(f"Could not retrieve the review: {exc}")

    if review is None:
        sys.exit(f"No saved review found for task {task_id}.")
    if args.json:
        print(json.dumps(review, indent=2))
    else:
        print_review(review)


if __name__ == "__main__":
    main()
