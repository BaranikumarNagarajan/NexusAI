"""Import real invoice images from four Kaggle datasets (REST API + token, no CLI).

Used by `scripts/3_import_kaggle_samples.py` and by `POST /api/samples/kaggle/refresh`.
Each dataset keeps one small *current set* in backend/samples/kaggle/<key>/ (git-ignored):
a refresh downloads a fresh random set and replaces the previous one, so every refresh shows
different invoices. Only the chosen files are downloaded, never a whole dataset.
`manifest.json` in each folder records the files, their ground truth (when the dataset has
labels) and the dataset's licence and attribution.
"""

import csv
import io
import json
import logging
import os
import queue
import random
import re
import shutil
import threading
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API = "https://www.kaggle.com/api/v1"
OUT = Path(__file__).resolve().parents[2] / "samples" / "kaggle"
MAX_PER_CALL = 12
MAX_IMAGE_SIDE = 1800  # phone photos are ~4000 px; OCR is faster and as accurate at this size


class KaggleError(Exception):
    pass


@dataclass(frozen=True)
class Dataset:
    key: str
    ref: str  # owner/slug
    title: str
    kind: str  # shown in the UI
    description: str
    license: str
    attribution: str
    labelled: bool


DATASETS: dict[str, Dataset] = {d.key: d for d in [
    Dataset("hq", "osamahosamabdellatif/high-quality-invoice-images-for-ocr", "High-Quality Invoice Images for OCR",
            "Scanned · labelled", "Clean scanned invoices with full ground-truth labels.",
            "ODbL (Open Database License)",
            '"High-Quality Invoice Images for OCR" by osamahosamabdellatif on Kaggle (ODbL).', True),
    Dataset("clearocr", "clearocr/clearocr-invoice-ocr-md-json-v1", "clearOCR Invoice Document AI",
            "Scanned · labelled", "Invoice images with model-extracted JSON labels.",
            "CC BY 4.0",
            '"clearOCR Invoice Document AI Dataset" by clearOCR (clearocr.com) on Kaggle, CC BY 4.0, derived from '
            'Kozlowski & Weichbroth (2021), "Samples of electronic invoices", Mendeley Data, DOI 10.17632/tnj49gpmtz.2.',
            True),
    Dataset("synthetic", "nowshinaranini/synthetic-invoice-ocr-dataset", "Realistic Synthetic Invoice Scans",
            "Synthetic scans · varied layouts", "Thousands of generated invoices with different layouts and scan noise.",
            "CC BY 4.0", '"Synthetic Invoice OCR Dataset" by nowshinaranini on Kaggle, CC BY 4.0.', False),
    Dataset("grocery", "surajitsadhukhan/indian-grocery-tax-invoice-image-dataset", "Indian Grocery Tax Invoices",
            "Phone photos · often handwritten",
            "Real shop bills photographed with a phone. Many are handwritten or creased, which OCR cannot read: "
            "type the key values in yourself, or run them as is to see Agent B report the missing information.",
            "CC BY 4.0", '"Indian Grocery Tax Invoice Image Dataset" by surajitsadhukhan on Kaggle, CC BY 4.0.', False),
]}
DEFAULT_DATASET = "hq"


def _token() -> str:
    token = (os.getenv("KAGGLE_API_KEY") or os.getenv("KAGGLE_API_TOKEN") or "").strip()
    if not token:
        raise KaggleError("Set KAGGLE_API_KEY in backend/.env to import real invoices.")
    return token


def dataset(key: str) -> Dataset:
    if key not in DATASETS:
        raise KaggleError(f"Unknown dataset '{key}'. Choose one of: {', '.join(DATASETS)}.")
    return DATASETS[key]


def folder(key: str) -> Path:
    return OUT / dataset(key).key


def load_manifest(key: str = DEFAULT_DATASET) -> dict | None:
    path = folder(key) / "manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class _Kaggle:
    def __init__(self, ds: Dataset) -> None:
        self.ds = ds
        self.http = httpx.Client(headers={"Authorization": f"Bearer {_token()}"}, timeout=120, follow_redirects=True)

    def get(self, url: str, **params: Any) -> httpx.Response:
        try:
            response = self.http.get(url, params=params or None)
        except httpx.HTTPError as exc:
            raise KaggleError(f"Could not reach Kaggle ({type(exc).__name__}).") from exc
        if response.status_code in (401, 403):
            raise KaggleError("Kaggle rejected the token (check KAGGLE_API_KEY).")
        if response.status_code != 200:
            raise KaggleError(f"Kaggle returned HTTP {response.status_code} for {self.ds.title}.")
        return response

    def file(self, path: str) -> bytes:
        return self.get(f"{API}/datasets/download/{self.ds.ref}/{urllib.parse.quote(path, safe='')}").content

    def list_files(self) -> list[str]:
        names, token = [], None
        while True:
            params = {"pageSize": 1000, **({"pageToken": token} if token else {})}
            data = self.get(f"{API}/datasets/list/{self.ds.ref}", **params).json()
            names += [f["name"] for f in data.get("datasetFiles", [])]
            token = data.get("nextPageToken")
            if not token or len(names) > 50_000:
                return names

    def cached(self, name: str, fetch: Callable[[], Any]) -> Any:
        """Indexes / label files are fetched once per dataset and kept next to the images."""
        path = folder(self.ds.key) / name
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        value = fetch()
        path.write_text(json.dumps(value), encoding="utf-8")
        return value

    def close(self) -> None:
        self.http.close()


# --- per-dataset candidates and labels ----------------------------------------------------------
# Each returns [{"path": <file in the dataset>, ...entry fields...}] for every usable invoice.

def _hq_candidates(k: _Kaggle) -> list[dict]:
    def fetch() -> list[dict]:
        csv.field_size_limit(10**8)
        text = k.file("batch_1/batch_1/batch1_1.csv").decode("utf-8", errors="replace")
        out = []
        for row in csv.DictReader(io.StringIO(text)):
            truth = json.loads(row["Json Data"])
            out.append({"path": f"batch_1/batch_1/batch1_1/{row['File Name']}",
                        "invoice_number": truth["invoice"].get("invoice_number"),
                        "seller": truth["invoice"].get("seller_name"), "client": truth["invoice"].get("client_name"),
                        "total": truth.get("subtotal", {}).get("total"), "items": len(truth.get("items", [])),
                        "ground_truth": truth})
        return out
    return k.cached("index.json", fetch)


def _clearocr_candidates(k: _Kaggle) -> list[dict]:
    def fetch() -> list[dict]:
        rows = [json.loads(line) for line in k.file("train/metadata.jsonl").decode("utf-8").splitlines() if line.strip()]
        return [{"path": f"train/{r['file_name']}", "label_path": f"train/{r['invoice_json_path']}"} for r in rows]
    return k.cached("index.json", fetch)


def _clearocr_label(k: _Kaggle, entry: dict) -> dict:
    try:
        truth = json.loads(k.file(entry.pop("label_path")))
    except (KaggleError, ValueError):
        return entry
    header, summary = truth.get("header", {}), truth.get("summary", {})
    return {**entry, "invoice_number": header.get("invoice_no"), "seller": header.get("seller"),
            "client": header.get("client"), "total": summary.get("total_gross_worth"),
            "items": len(truth.get("items", [])), "ground_truth": truth}


def _image_candidates(k: _Kaggle) -> list[dict]:
    names = k.cached("index.json", lambda: [n for n in k.list_files() if n.lower().endswith((".jpg", ".jpeg", ".png"))])
    out = []
    for name in names:
        number = re.search(r"(INV[-A-Z]*-\d+)", name)  # e.g. 0001_INV-273789.png, INV-BD-303348.jpg
        out.append({"path": name, "invoice_number": number.group(1) if number else None})
    return out


_CANDIDATES = {"hq": _hq_candidates, "clearocr": _clearocr_candidates,
               "synthetic": _image_candidates, "grocery": _image_candidates}
_LABEL = {"clearocr": _clearocr_label}


def _save_image(data: bytes, target: Path) -> Path | None:
    """Validate the download is an image; shrink very large photos; save as JPEG/PNG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError):
        return None
    if max(image.size) <= MAX_IMAGE_SIDE and target.suffix.lower() in {".jpg", ".jpeg", ".png"}:
        target.write_bytes(data)
        return target
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    target = target.with_suffix(".jpg")
    image.save(target, "JPEG", quality=88)
    return target


def refresh(key: str = DEFAULT_DATASET, count: int = 6) -> dict:
    """Replace the dataset's current set with `count` random invoices not shown last time."""
    ds = dataset(key)
    count = max(1, min(count, MAX_PER_CALL))
    out = folder(key)
    out.mkdir(parents=True, exist_ok=True)
    previous = load_manifest(key) or {"files": []}
    shown = {f.get("path") for f in previous["files"]}

    k = _Kaggle(ds)
    try:
        candidates = _CANDIDATES[key](k)
        pool = [c for c in candidates if c["path"] not in shown] or candidates
        added = []
        for entry in random.sample(pool, len(pool)):  # random order; stop once we have enough
            if len(added) >= count:
                break
            name = re.sub(r"[^\w.-]", "_", entry["path"].rsplit("/", 1)[-1])
            saved = _save_image(k.file(entry["path"]), out / f"new-{name}")
            if saved is None:
                continue
            entry = _LABEL.get(key, lambda _k, e: e)(k, dict(entry))
            added.append({**entry, "file": saved.name})
    finally:
        k.close()
    if not added:
        raise KaggleError(f"No usable images were downloaded from {ds.title}.")

    # Swap the new set in: remove the previous images, drop the "new-" prefix.
    for old in previous["files"]:
        (out / old["file"]).unlink(missing_ok=True)
        _cache_path(key, old["file"]).unlink(missing_ok=True)
    for entry in added:
        final = entry["file"].removeprefix("new-")
        shutil.move(out / entry["file"], out / final)
        entry["file"] = final

    manifest = {"dataset": key, "source": f"https://www.kaggle.com/datasets/{ds.ref}", "title": ds.title,
                "license": ds.license, "attribution": ds.attribution, "labelled": ds.labelled,
                "imported_at": datetime.now(UTC).isoformat(timespec="seconds"), "files": added}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"dataset": key, "title": ds.title, "added": len(added), "files": [a["file"] for a in added],
            "license": ds.license, "attribution": ds.attribution}


# --- OCR results, read ahead ---------------------------------------------------------------------
# OCR is the slow part (seconds locally, 10-30 s on a small hosting CPU). After a refresh every new
# invoice is read in the background and the result saved next to the image, so clicking a card is
# usually instant. A per-file lock makes a click wait for a reading already in progress, not repeat it.

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


def _cache_path(key: str, filename: str) -> Path:
    return folder(key) / f"{filename}.extract.json"


def cached_extraction(key: str, filename: str) -> dict:
    """The extraction (same shape as POST /api/documents/extract) for one downloaded invoice."""
    from app.ingestion.pdf_extract import extract_document  # imported lazily: OCR stack is heavy

    path = _cache_path(key, filename)
    with _locks_guard:
        lock = _locks.setdefault(path, threading.Lock())
    with lock:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        result = {"filename": filename, **extract_document((folder(key) / filename).read_bytes(), "invoice").to_dict()}
        path.write_text(json.dumps(result), encoding="utf-8")
        return result


_queue: queue.Queue[tuple[str, str]] = queue.Queue()
_worker: threading.Thread | None = None


def read_ahead(key: str, filenames: list[str]) -> None:
    """Queue a fresh set for background OCR. One worker reads one invoice at a time, so several
    refreshes in a row never run OCR in parallel (a small hosting CPU would stall the web app)."""
    global _worker
    for name in filenames:
        _queue.put((key, name))
    with _locks_guard:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_read_ahead_worker, name="read-ahead", daemon=True)
            _worker.start()


def _read_ahead_worker() -> None:
    while True:
        key, name = _queue.get()
        if not (folder(key) / name).is_file():
            continue  # replaced by a newer refresh before its turn came
        try:
            cached_extraction(key, name)
        except Exception as exc:  # a bad image must not stop the others
            logger.warning("Read-ahead OCR failed for %s/%s: %s", key, name, type(exc).__name__)
