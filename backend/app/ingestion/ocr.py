"""OCR for scanned PDFs and invoice images, using a locally installed Tesseract.

Tesseract is found via TESSERACT_CMD, then PATH, then the standard Windows install
folders. Images are normalised (grayscale, upscaled, auto-contrast) before OCR; a page that reads
as noise is turned the right way up (a quick read at each quarter turn) and read again. PDF pages
are rendered at ~300 dpi with pypdfium2. No network calls.
"""

import io
import os
import shutil
import subprocess
import threading
from functools import lru_cache

import pypdfium2 as pdfium
from PIL import Image, ImageOps

MAX_OCR_PAGES = 5
OCR_TIMEOUT_SECONDS = 90
# Measured: 2600 px reads 41/41 real Kaggle line items and 98/98 synthetic fields
# (1800 px: 40/41, misread '37,75' as '37,15'; sharpening cost 2 synthetic fields).
TARGET_WIDTH = 2600
# Mean word confidence: upright scans score ~93-95, a sideways photo ~23-34 (57-70 once turned upright).
MIN_UPRIGHT_CONFIDENCE = 60
PROBE_SIZE = 1200  # small copy used to find the right way up (a quick read, not the real one)
MIN_ANGLE_GAIN = 10  # a turned copy must read clearly better than the page as it is
_STANDARD_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


class OCRError(Exception):
    pass


# OCR runs at low CPU priority (Linux / Docker), so web requests and health checks stay responsive
# on a small hosting CPU while invoices are read.
_LOW_PRIORITY = ["nice", "-n", "10"] if os.name == "posix" and shutil.which("nice") else []
# One OCR job at a time per process (background read-ahead, uploads): OCR is the memory-hungry step,
# and two at once can push a 512 MB hosting plan over its limit.
_ONE_AT_A_TIME = threading.Lock()


@lru_cache(maxsize=1)
def find_tesseract() -> str | None:
    for candidate in (os.getenv("TESSERACT_CMD"), shutil.which("tesseract"), *_STANDARD_PATHS):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def ocr_status() -> str:
    return "available" if find_tesseract() else "not_installed"


def _best_angle(image: Image.Image) -> int:
    """Find the right way up for a sideways or upside-down photo: read a small copy at each quarter
    turn and keep the angle whose words Tesseract is most sure of. (Measured on real phone photos:
    more reliable than Tesseract's own orientation detection, which misses dense tables.)"""
    probe = image.copy()
    probe.thumbnail((PROBE_SIZE, PROBE_SIZE))
    scores: dict[int, float] = {}
    for angle in (0, 90, 180, 270):
        turned = ImageOps.autocontrast(probe.rotate(angle, expand=True))
        buffer = io.BytesIO()
        turned.save(buffer, format="PNG")
        scores[angle] = _confidence(_tesseract(buffer.getvalue())[1])
    best = max(scores, key=lambda angle: scores[angle])
    return best if scores[best] >= scores[0] + MIN_ANGLE_GAIN else 0


def _prepare(image: Image.Image) -> bytes:
    """Grayscale page -> upscaled, auto-contrasted PNG bytes for Tesseract."""
    if image.width < TARGET_WIDTH:  # upscale small scans; measured best on real invoices
        ratio = TARGET_WIDTH / image.width
        image = image.resize((TARGET_WIDTH, int(image.height * ratio)), Image.Resampling.LANCZOS)
    image = ImageOps.autocontrast(image)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _tesseract(png: bytes, page: int = 0) -> tuple[str, list[dict]]:
    """OCR one page; returns its text (one line per OCR line) and the words with positions."""
    exe = find_tesseract()
    if exe is None:
        raise OCRError("OCR is not available: install Tesseract or set TESSERACT_CMD in backend/.env.")
    try:
        done = subprocess.run(
            [*_LOW_PRIORITY, exe, "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
            input=png, capture_output=True, timeout=OCR_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OCRError("OCR took too long on this page.") from exc
    if done.returncode != 0:
        raise OCRError("Tesseract could not read this document.")
    words: list[dict] = []
    lines: dict[tuple, list[str]] = {}
    for row in done.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        key = (int(cols[2]), int(cols[3]), int(cols[4]))  # block, paragraph, line
        lines.setdefault(key, []).append(cols[11])
        words.append({"text": cols[11], "left": int(cols[6]), "top": int(cols[7]) + page * 100_000,
                      "height": int(cols[9]), "line": (page, *key), "conf": float(cols[10])})
    return "\n".join(" ".join(ws) for ws in lines.values()), words


def _confidence(words: list[dict]) -> float:
    return sum(w["conf"] for w in words) / len(words) if words else 0.0


def _read_page(image: Image.Image, page: int) -> tuple[str, list[dict]]:
    """OCR a page as it is; only if the result looks like noise, find the right way up and retry.
    Upright scans (the usual case) skip orientation detection, which matters on slow hosting CPUs."""
    image = ImageOps.exif_transpose(image).convert("L")
    text, words = _tesseract(_prepare(image), page)
    if _confidence(words) >= MIN_UPRIGHT_CONFIDENCE:
        return text, words
    angle = _best_angle(image)
    if angle == 0:
        return text, words
    turned_text, turned_words = _tesseract(_prepare(image.rotate(angle, expand=True)), page)
    return (turned_text, turned_words) if _confidence(turned_words) > _confidence(words) else (text, words)


def ocr_image(data: bytes) -> tuple[str, int, list[dict]]:
    with _ONE_AT_A_TIME:
        return _ocr_image(data)


def ocr_pdf(data: bytes) -> tuple[str, int, list[dict]]:
    with _ONE_AT_A_TIME:
        return _ocr_pdf(data)


def _ocr_image(data: bytes) -> tuple[str, int, list[dict]]:
    try:
        image = Image.open(io.BytesIO(data))
        frames = []
        for index in range(min(getattr(image, "n_frames", 1), MAX_OCR_PAGES)):  # multi-page TIFF
            image.seek(index)
            frames.append(image.copy())
    except OCRError:
        raise
    except Exception as exc:  # untrusted input
        raise OCRError("The image could not be opened.") from exc
    results = [_read_page(f, i) for i, f in enumerate(frames)]
    return "\n".join(t for t, _ in results), len(frames), [w for _, ws in results for w in ws]


def _ocr_pdf(data: bytes) -> tuple[str, int, list[dict]]:
    try:
        pdf = pdfium.PdfDocument(data)
        # scale is pixels per PDF point (72 per inch); pypdfium2 accepts a float, its type hint says int.
        pages = [pdf[i].render(scale=300 / 72).to_pil()  # pyright: ignore[reportArgumentType]
                 for i in range(min(len(pdf), MAX_OCR_PAGES))]
    except Exception as exc:  # untrusted input
        raise OCRError("The PDF pages could not be rendered for OCR.") from exc
    results = [_read_page(p, i) for i, p in enumerate(pages)]
    return "\n".join(t for t, _ in results), len(pages), [w for _, ws in results for w in ws]
