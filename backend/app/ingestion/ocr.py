"""OCR for scanned PDFs and invoice images, using a locally installed Tesseract.

Tesseract is found via TESSERACT_CMD, then PATH, then the standard Windows install
folders. Images are normalised (grayscale, upscaled, auto-contrast) before OCR; a page that reads
as noise is turned upright with orientation detection and read again. PDF pages are rendered at ~300 dpi with pypdfium2. No network calls.
"""

import io
import os
import re
import shutil
import subprocess
from functools import lru_cache

import pypdfium2 as pdfium
from PIL import Image, ImageOps

MAX_OCR_PAGES = 5
OCR_TIMEOUT_SECONDS = 90
# Measured: 2600 px reads 41/41 real Kaggle line items and 98/98 synthetic fields
# (1800 px: 40/41, misread '37,75' as '37,15'; sharpening cost 2 synthetic fields).
TARGET_WIDTH = 2600
MIN_OSD_CONFIDENCE = 1.5  # below this Tesseract's orientation guess is unreliable
# Mean word confidence: upright scans score ~93-95, a sideways photo ~34 (70 once turned upright).
MIN_UPRIGHT_CONFIDENCE = 60
_STANDARD_PATHS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
)


class OCRError(Exception):
    pass


@lru_cache(maxsize=1)
def find_tesseract() -> str | None:
    for candidate in (os.getenv("TESSERACT_CMD"), shutil.which("tesseract"), *_STANDARD_PATHS):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def ocr_status() -> str:
    return "available" if find_tesseract() else "not_installed"


def _orient(image: Image.Image) -> Image.Image:
    """Turn sideways / upside-down photos upright using Tesseract's orientation detection (OSD).
    Phone photos of receipts are often taken in landscape; without this OCR reads nothing."""
    exe = find_tesseract()
    if exe is None:
        return image
    probe = image.copy()
    probe.thumbnail((1600, 1600))
    buffer = io.BytesIO()
    probe.save(buffer, format="PNG")
    try:
        done = subprocess.run([exe, "stdin", "stdout", "--psm", "0"], input=buffer.getvalue(),
                              capture_output=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        return image
    out = done.stdout.decode("utf-8", errors="replace")
    rotate = re.search(r"Rotate:\s*(\d+)", out)
    confidence = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if done.returncode != 0 or not rotate or not confidence:
        return image  # too little text to tell (OSD fails); leave the page as it is
    degrees = int(rotate.group(1))
    if degrees in (90, 180, 270) and float(confidence.group(1)) >= MIN_OSD_CONFIDENCE:
        return image.rotate(360 - degrees, expand=True)  # Tesseract reports clockwise, PIL rotates anticlockwise
    return image


def _prepare(image: Image.Image) -> bytes:
    """Grayscale page -> upscaled, auto-contrasted PNG bytes for Tesseract."""
    if image.width < TARGET_WIDTH:  # upscale small scans; measured best on real invoices
        ratio = TARGET_WIDTH / image.width
        image = image.resize((TARGET_WIDTH, int(image.height * ratio)), Image.LANCZOS)
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
            [exe, "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
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
    """OCR a page as it is; only if the result looks like noise, detect the orientation and retry.
    Upright scans (the usual case) skip orientation detection, which matters on slow hosting CPUs."""
    image = ImageOps.exif_transpose(image).convert("L")
    text, words = _tesseract(_prepare(image), page)
    if _confidence(words) >= MIN_UPRIGHT_CONFIDENCE:
        return text, words
    turned = _orient(image)
    if turned is image:
        return text, words
    turned_text, turned_words = _tesseract(_prepare(turned), page)
    return (turned_text, turned_words) if _confidence(turned_words) > _confidence(words) else (text, words)


def ocr_image(data: bytes) -> tuple[str, int, list[dict]]:
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


def ocr_pdf(data: bytes) -> tuple[str, int, list[dict]]:
    try:
        pdf = pdfium.PdfDocument(data)
        pages = [pdf[i].render(scale=300 / 72).to_pil() for i in range(min(len(pdf), MAX_OCR_PAGES))]
    except Exception as exc:  # untrusted input
        raise OCRError("The PDF pages could not be rendered for OCR.") from exc
    results = [_read_page(p, i) for i, p in enumerate(pages)]
    return "\n".join(t for t, _ in results), len(pages), [w for _, ws in results for w in ws]
