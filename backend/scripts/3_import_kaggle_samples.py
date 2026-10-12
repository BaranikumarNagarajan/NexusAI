"""Load real invoice images from one of the four Kaggle datasets NexusAI supports.

Reads KAGGLE_API_KEY (Kaggle API token) from backend/.env and talks to Kaggle's REST API with
httpx - no Kaggle CLI. Only the chosen files are downloaded (never a whole dataset), into
backend/samples/kaggle/<dataset>/ (git-ignored). Each run replaces that dataset's current set with
a fresh random one. The web app does the same with the "Refresh" button.

Usage (from backend/):
    python scripts/3_import_kaggle_samples.py                        # 6 random invoices from "hq"
    python scripts/3_import_kaggle_samples.py --dataset grocery --count 12
    python scripts/3_import_kaggle_samples.py --evaluate             # also measure OCR accuracy vs labels (hq)
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.ingestion.kaggle_import import DATASETS, KaggleError, folder, load_manifest, refresh  # noqa: E402


def evaluate() -> None:
    """OCR accuracy on the current "hq" set, the dataset with complete ground-truth labels."""
    from app.ingestion.pdf_extract import extract_document, parse_amount

    def norm(value):
        amount = parse_amount(value) if value else None
        return Decimal(amount) if amount else None

    score: dict[str, list[int]] = {}
    for entry in (load_manifest("hq") or {}).get("files", []):
        data = extract_document((folder("hq") / entry["file"]).read_bytes(), "invoice").data()
        truth = entry["ground_truth"]
        checks = {
            "invoice_number": data.get("invoice_number") == truth["invoice"]["invoice_number"],
            "supplier": (data.get("supplier_name") or "").lower() == (truth["invoice"]["seller_name"] or "").lower(),
            "total": norm(data.get("total_amount")) == norm(truth["subtotal"]["total"]),
            "tax": norm(data.get("tax_amount")) == norm(truth["subtotal"]["tax"]),
            "line_items": len(data.get("line_items", [])) == len(truth["items"]),
        }
        for key, ok in checks.items():
            score.setdefault(key, [0, 0])
            score[key][0] += ok
            score[key][1] += 1
    print("\nOCR accuracy against the dataset's labels:")
    for key, (ok, total) in score.items():
        print(f"  {key:15} {ok}/{total}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load real invoice images from Kaggle.")
    parser.add_argument("--dataset", choices=list(DATASETS), default="hq", help="which dataset to load from")
    parser.add_argument("--count", type=int, default=6, help="number of invoices in the new set (1-12)")
    parser.add_argument("--evaluate", action="store_true", help="run OCR on the hq set and compare with labels")
    args = parser.parse_args()
    load_dotenv(BACKEND / ".env")
    try:
        result = refresh(args.dataset, args.count)
    except KaggleError as exc:
        sys.exit(str(exc))
    print(f"Loaded {result['added']} invoices from {result['title']} - licence: {result['license']}")
    for name in result["files"]:
        print(f"  {name}")
    if args.evaluate:
        evaluate()


if __name__ == "__main__":
    main()
