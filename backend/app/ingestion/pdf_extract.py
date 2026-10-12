"""Extract invoice / purchase-order fields from PDFs and invoice images.

Pure function: document bytes in, :class:`Extraction` out. No network, no LLM.

* Text-based PDFs are read directly (pdfplumber). Scanned PDFs and images
  (PNG / JPEG / TIFF) are read with **OCR** (local Tesseract, see ``ocr.py``).
* Header fields are found by **label** ("Invoice No:", "Purchase Order", "VAT", ...),
  with several common wordings per field, so different supplier layouts work.
* Line items come from the **table** whose header row names the columns
  (SKU / Item code, Qty / Quantity, Unit price / Price, Amount / Line total, ...);
  for OCR text, from the rows under a header line that names those columns.
* Every field carries a confidence: ``high`` (explicit label, value validated),
  ``medium`` (inferred, e.g. currency from a symbol, or anything read by OCR), or ``missing``.
* Sanity warnings (line items not adding up to the subtotal, ...) are reported but
  never "fixed": verification remains Agent B's job, and a person reviews the
  extracted values before anything is submitted.
"""

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import pdfplumber

MAX_PAGES = 10
PDF_MAGIC = b"%PDF-"

INVOICE_FIELDS = ("invoice_number", "supplier_name", "currency", "po_reference", "tax_amount", "total_amount")
PO_FIELDS = ("po_number", "supplier_name", "currency", "total_amount")

_SYMBOL_CURRENCY = {"£": "GBP", "€": "EUR", "$": "USD", "¥": "JPY"}
# Numbers in any common format: 1234.56 | 1,234.56 | 1.234,56 | 1 234,56 | 3,00
_NUM = r"-?\d{1,3}(?:[ \u00a0.,]\d{3})+(?:[.,]\d{1,2})?(?![\d])|-?\d+(?:[.,]\d+)?"
_AMOUNT = r"(?P<cur>[A-Z]{3}|[£€$¥])?\s*(?P<amt>" + _NUM + r")"
_DOC_ID = r"(?P<val>[A-Za-z0-9][A-Za-z0-9._/#-]{0,63})"

# Label patterns, most specific first. Matched against single lines of text.
_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "invoice_number": [
        (r"invoice\s*(?:no\.?|number|num\.?|#)\s*[:#]?\s*" + _DOC_ID, "Invoice number"),
        # Other common layouts: Indian GST invoices ("Inv No :"), statements, bills and receipts.
        (r"\b(?:inv\.?\s*no\.?|invoice\s*ref\.?|statement\s*ref\.?|bill\s*no\.?|receipt\s*no\.?)\s*[:#]?\s*" + _DOC_ID,
         "Invoice number"),
    ],
    "po_number": [
        (r"(?:p\.?o\.?|purchase\s*order)\s*(?:no\.?|number|num\.?|#)\s*[:#]?\s*" + _DOC_ID, "PO number"),
    ],
    "po_reference": [
        (r"(?:p\.?o\.?\s*(?:reference|ref\.?|no\.?|number|#)|purchase\s*order(?:\s*(?:no\.?|number|ref\.?))?|your\s*order)"
         r"\s*[:#]?\s*" + _DOC_ID, "PO reference"),
    ],
    "supplier_name": [
        (r"(?:supplier|vendor|seller|from|issued\s*by)\s*:\s*(?P<val>.{2,200}?)\s*$", "Supplier"),
    ],
    "currency": [
        (r"currency(?:\s*code)?\s*:?\s*(?P<val>[A-Za-z]{3})\b", "Currency"),
    ],
    "tax_amount": [
        (r"(?:vat|tax|gst|sales\s*tax)(?:\s*amount)?(?:\s*\(\s*[0-9.]+\s*%\s*\))?\s*:?\s*" + _AMOUNT + r"\s*$", "Tax"),
    ],
    "total_amount": [
        (r"(?:total\s*due|amount\s*due|balance\s*due|grand\s*total|invoice\s*total|order\s*total|total\s*amount)"
         r"\s*:?\s*" + _AMOUNT + r"\s*$", "Total"),
        (r"^(?<!sub)total\s*:?\s*" + _AMOUNT + r"\s*$", "Total"),
        # GST invoices / statements; OCR of ruled tables often leaves ':' or '|' after the amount.
        (r"(?:net\s*payable|total\s*payable|amount\s*payable|total\s*charges)\s*:?\s*" + _AMOUNT + r"[\s:;|'.]*$", "Total"),
    ],
    "subtotal": [
        (r"(?:sub[\s-]*total|net\s*(?:amount|total))\s*:?\s*" + _AMOUNT + r"\s*$", "Subtotal"),
    ],
}

_COLUMN_ALIASES = {
    "sku": ("sku", "item code", "code", "product code", "part no", "part number", "item no", "article"),
    "description": ("description", "item", "details", "product", "item description"),
    "quantity": ("qty", "quantity", "units", "qty."),
    "unit_price": ("unit price", "price", "rate", "unit cost", "price per unit", "unit"),
    "line_total": ("amount", "line total", "total", "net", "line amount", "extended"),
}


class ExtractionError(Exception):
    """The file is not usable (wrong type, too many pages, unreadable, OCR unavailable...)."""


IMAGE_MAGIC = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpeg", b"II*\x00": "tiff", b"MM\x00*": "tiff"}


def detect_kind(data: bytes) -> str | None:
    if data.startswith(PDF_MAGIC):
        return "pdf"
    return next((kind for magic, kind in IMAGE_MAGIC.items() if data.startswith(magic)), None)


@dataclass
class FieldValue:
    value: str | None
    confidence: str  # high | medium | missing
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "confidence": self.confidence, "source": self.source}


@dataclass
class Extraction:
    document_type: str  # invoice | purchase_order | unknown
    pages: int
    fields: dict[str, FieldValue]
    line_items: list[dict[str, str]]
    line_items_confidence: str
    warnings: list[str] = field(default_factory=list)
    method: str = "text"  # text | ocr

    def data(self) -> dict[str, Any]:
        """Request fragment in Agent B's schema (only fields that were found)."""
        data: dict[str, Any] = {k: f.value for k, f in self.fields.items() if f.value is not None}
        if self.line_items:
            data["line_items"] = self.line_items
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_type": self.document_type,
            "pages": self.pages,
            "fields": {k: f.to_dict() for k, f in self.fields.items()},
            "line_items": self.line_items,
            "line_items_confidence": self.line_items_confidence,
            "warnings": self.warnings,
            "method": self.method,
            "data": self.data(),
        }


# --- public API -----------------------------------------------------------------------------


def extract_document(data: bytes, expected_type: str | None = None) -> Extraction:
    """Extract fields from a PDF (text layer, else OCR) or an image (OCR)."""
    from app.ingestion.ocr import OCRError, ocr_image, ocr_pdf  # local import keeps OCR optional

    kind = detect_kind(data)
    if kind is None:
        raise ExtractionError("Unsupported file type. Upload a PDF, PNG, JPEG or TIFF.")
    try:
        if kind != "pdf":
            text, pages, words = ocr_image(data)
            return _analyse(text, "", [], pages, "ocr", expected_type, words)
        text, title, tables, pages, words = _read_text_pdf(data)
        if text.strip():
            return _analyse(text, title, tables, pages, "text", expected_type, words)
        text, pages, words = ocr_pdf(data)  # scanned PDF: no text layer
        return _analyse(text, "", [], pages, "ocr", expected_type, words)
    except OCRError as exc:
        raise ExtractionError(str(exc)) from exc


def _read_text_pdf(data: bytes) -> tuple[str, str, list, int, list[dict]]:
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ExtractionError(f"The PDF has {len(pdf.pages)} pages; at most {MAX_PAGES} are supported.")
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            tables = [t for page in pdf.pages for t in page.extract_tables()]
            words = [
                {"text": w["text"], "left": w["x0"], "top": w["top"] + n * 100_000, "height": w["bottom"] - w["top"],
                 "line": (n, round(w["top"]))}
                for n, page in enumerate(pdf.pages) for w in page.extract_words()
            ]
            return text, _title_text(pdf.pages[0]), tables, len(pdf.pages), words
    except ExtractionError:
        raise
    except Exception as exc:  # untrusted input: any parser failure (PdfminerException, PDFSyntaxError, ...) is a bad file
        raise ExtractionError("The PDF could not be read; it may be damaged or not a real PDF.") from exc


def _analyse(text: str, title: str, tables: list, pages: int, method: str, expected_type: str | None,
             words: list[dict] | None = None) -> Extraction:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ExtractionError("No text could be read from this document.")
    document_type = _document_type(title or " ".join(lines[:6]).upper(), text)
    warnings: list[str] = []
    if method == "ocr":
        warnings.append("Read with OCR (scanned document): please check every value carefully.")
    if expected_type and document_type not in (expected_type, "unknown"):
        names = {"invoice": "an invoice", "purchase_order": "a purchase order"}
        warnings.append(f"This looks like {names[document_type]}, not {names.get(expected_type, expected_type)}.")
    if document_type == "unknown":
        document_type = expected_type or "unknown"
        warnings.append("Could not tell from the title whether this is an invoice or a purchase order.")

    wanted = PO_FIELDS if document_type == "purchase_order" else INVOICE_FIELDS
    fields = {name: _find_field(name, lines) for name in wanted}
    subtotal = _find_field("subtotal", lines)
    _summary_row(fields, lines, subtotal)
    if fields["supplier_name"].value is None and words:
        fields["supplier_name"] = _supplier_from_layout(words)
    _infer_currency(fields, lines)

    include_total = document_type != "purchase_order"
    if method == "ocr":
        items, items_confidence, item_warnings = _line_items_from_text(lines, include_total)
        for value in fields.values():  # OCR can misread characters: never claim high confidence
            if value.confidence == "high":
                value.confidence, value.source = "medium", f"OCR - {value.source}"
    else:
        items, items_confidence, item_warnings = _line_items(tables, include_line_total=include_total)
    warnings += item_warnings
    warnings += _sanity_checks(document_type, fields, subtotal, items)
    return Extraction(document_type, pages, fields, items, items_confidence, warnings, method)


_SUPPLIER_LABELS = {"seller", "supplier", "vendor", "from", "sold by", "issued by"}
_OTHER_PARTY_LABELS = {"client", "buyer", "customer", "bill", "billed", "ship", "invoice to", "sold to", "to"}


def _supplier_from_layout(words: list[dict]) -> FieldValue:
    """Two-column headers ('Seller:' next to 'Client:') put the supplier name on the line *below*
    the label, beside the other party's name. Use word positions to read only the seller's column."""
    for i, word in enumerate(words):
        label = word["text"].strip(":").lower()
        if label not in _SUPPLIER_LABELS or not (word["text"].endswith(":") or
                                                  (i + 1 < len(words) and words[i + 1]["text"] == ":")):
            continue
        same_line = [w for w in words if w["line"] == word["line"] and w["left"] > word["left"]]
        right = next((w["left"] for w in same_line if w["text"].strip(":").lower() in _OTHER_PARTY_LABELS), None)
        below = [w for w in words
                 if word["top"] + word["height"] * 0.6 < w["top"] < word["top"] + word["height"] * 5
                 and w["left"] >= word["left"] - 20 and (right is None or w["left"] < right - 10)]
        if not below:
            continue
        first_line = min(below, key=lambda w: w["top"])["line"]
        name = " ".join(w["text"] for w in sorted((w for w in below if w["line"] == first_line), key=lambda w: w["left"]))
        if len(name) >= 2:
            return FieldValue(name, "medium", f"Name under the '{word['text'].strip(':')}' label (column layout)")
    return FieldValue(None, "missing")


def _summary_row(fields: dict[str, FieldValue], lines: list[str], subtotal: FieldValue) -> None:
    """Summary tables put several totals on one row, e.g. 'Total $ 5 640,17 $ 564,02 $ 6 204,19'
    (net, tax, gross). Fill total / tax / subtotal from such a row when no label matched."""
    for line in lines:
        if not re.match(r"^(grand\s+)?total\b", line, re.IGNORECASE):
            continue
        amounts = [parse_amount(m.group("amt")) for m in re.finditer(_AMOUNT, line)]
        amounts = [a for a in amounts if a is not None]
        if len(amounts) < 2:
            continue
        source = f"summary row: '{line}'"
        if "total_amount" in fields and fields["total_amount"].value is None:
            fields["total_amount"] = FieldValue(amounts[-1], "medium", source)
        if len(amounts) == 3 and "tax_amount" in fields and fields["tax_amount"].value is None:
            fields["tax_amount"] = FieldValue(amounts[1], "medium", source)
        if subtotal.value is None:
            subtotal.value, subtotal.confidence, subtotal.source = amounts[0], "medium", source
        return


# --- helpers -----------------------------------------------------------------------------------


def _title_text(page) -> str:
    """Text set in the largest font(s) on page 1, where document titles live."""
    words = page.extract_words(extra_attrs=["size"])
    if not words:
        return ""
    biggest = max(w["size"] for w in words)
    return " ".join(w["text"] for w in words if w["size"] >= biggest - 2).upper()


def _document_type(title: str, text: str) -> str:
    for source in (title, text[:400].upper()):
        if re.search(r"PURCHASE\s+ORDER", source) and "INVOICE" not in source:
            return "purchase_order"
        if "INVOICE" in source and not re.search(r"PURCHASE\s+ORDER", source):
            return "invoice"
    return "unknown"


def parse_amount(raw: str) -> str | None:
    """Normalise a number written in any common locale to a plain decimal string.

    '1 534,14' -> '1534.14', '1.234,56' -> '1234.56', '1,234.56' -> '1234.56', '3,00' -> '3.00'.
    """
    value = re.sub(r"[£€$¥\s\u00a0]|[A-Z]{3}", "", raw.strip())
    if "," in value and "." in value:
        decimal = "," if value.rfind(",") > value.rfind(".") else "."
        value = value.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif "," in value:
        value = value.replace(",", ".") if re.search(r"^-?\d+,\d{1,2}$", value) else value.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3}){2,}", value):  # 1.234.567
        value = value.replace(".", "")
    try:
        Decimal(value)
    except InvalidOperation:
        return None
    return value


_clean_amount = parse_amount


def _find_field(name: str, lines: list[str]) -> FieldValue:
    for pattern, label in _PATTERNS[name]:
        regex = re.compile(pattern, re.IGNORECASE)
        for line in lines:
            match = regex.search(line)
            if not match:
                continue
            if "amt" in match.groupdict():
                value = _clean_amount(match.group("amt"))
                if value is None:
                    continue
                return FieldValue(value, "high", f"{label} label: '{line}'")
            value = match.group("val").strip(" :#")
            if name == "supplier_name" and (match.group("val").rstrip().endswith(":")
                                            or value.lower() in _OTHER_PARTY_LABELS):
                continue  # "Seller: Client:" - the value is the next column's label, not a name
            if name == "currency":
                value = value.upper()
                if not re.fullmatch(r"[A-Z]{3}", value):
                    continue
            return FieldValue(value, "high", f"{label} label: '{line}'")
    return FieldValue(None, "missing")


def _infer_currency(fields: dict[str, FieldValue], lines: list[str]) -> None:
    """No 'Currency:' label: infer it from codes/symbols next to amounts (medium confidence)."""
    if fields["currency"].value is not None:
        return
    amount_lines = [l for l in lines if re.search(r"(total|amount|due|vat|tax|subtotal)", l, re.IGNORECASE)]
    for line in amount_lines:
        code = re.search(r"\b([A-Z]{3})\s*-?[0-9][0-9,]*(?:\.[0-9]+)?\s*$", line)
        if code and code.group(1) not in {"VAT", "GST", "TAX"}:
            fields["currency"] = FieldValue(code.group(1), "medium", f"code next to amount in '{line}'")
            return
        for symbol, currency in _SYMBOL_CURRENCY.items():
            if symbol in line:
                note = " ($ assumed to be USD)" if symbol == "$" else ""
                fields["currency"] = FieldValue(currency, "medium", f"symbol '{symbol}' in '{line}'{note}")
                return


def _match_columns(header: list[str | None]) -> dict[str, int]:
    names = [re.sub(r"\s+", " ", (h or "")).strip().lower().rstrip(".:") for h in header]
    columns: dict[str, int] = {}
    # Exact alias matches first, so "unit price" isn't taken by "price"/"unit" elsewhere.
    for key, aliases in _COLUMN_ALIASES.items():
        for index, name in enumerate(names):
            if name in aliases and index not in columns.values():
                columns.setdefault(key, index)
    return columns


def _line_items(tables: list[list[list[str | None]]], include_line_total: bool) -> tuple[list[dict], str, list[str]]:
    for table in tables:
        if not table:
            continue
        columns = _match_columns(table[0])
        if "sku" not in columns or "quantity" not in columns:
            continue
        items, problems = [], []
        for row in table[1:]:
            cell = lambda key: (row[columns[key]] or "").strip() if key in columns and columns[key] < len(row) else ""
            sku = cell("sku")
            if not sku or re.match(r"(sub)?total|vat|tax", sku, re.IGNORECASE):
                continue
            item: dict[str, str] = {"sku": sku}
            if cell("description"):
                item["description"] = re.sub(r"\s+", " ", cell("description"))
            for key in ("quantity", "unit_price") + (("line_total",) if include_line_total else ()):
                raw = re.sub(r"[A-Z]{3}|[£€$¥]", "", cell(key)).strip()
                if not raw:
                    continue
                value = _clean_amount(raw)
                if value is None:
                    problems.append(f"Line {sku}: could not read {key.replace('_', ' ')} '{cell(key)}'.")
                else:
                    item[key] = value
            items.append(item)
        if items:
            return items, ("medium" if problems else "high"), problems
    return [], "missing", ["No line-item table with SKU and quantity columns was found."]


_TOTALS_ROW = re.compile(r"(summary|sub[\s-]*total|total|net\s*amount|amount\s*due|order\s*total)\b", re.IGNORECASE)
_STANDALONE_NUM = re.compile(r"(?<![\w.,/-])(?:[£€$¥]\s?)?(" + _NUM + r")(?![\w%/-])(?!\s*%)")
# Header keywords -> column, longest phrases first so "net price" wins over "net".
_HEADER_COLUMNS = [
    (r"unit[\s_]*price|net[\s_]*price|price\s*per\s*unit|unit[\s_]*cost|price|rate", "unit_price"),
    (r"net\s*worth|net\s*amount|line\s*total|amount|extended", "line_total"),
    (r"gross\s*worth|gross\s*amount|gross", "gross"),
    (r"qty|quantity", "quantity"),
    (r"vat|tax", "tax"),
]


def _header_numeric_columns(header: str) -> list[str]:
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for pattern, column in _HEADER_COLUMNS:
        for m in re.finditer(r"\b(?:" + pattern + r")\b", header, re.IGNORECASE):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            # A tax column given as a percentage ("VAT [%]") holds no amount: skip it.
            if column == "tax" and re.match(r"\s*\[?%", header[m.end():]):
                taken.append((m.start(), m.end()))
                continue
            found.append((m.start(), column))
            taken.append((m.start(), m.end()))
            break
    return [c for _, c in sorted(found)]


def _line_items_from_text(lines: list[str], include_line_total: bool) -> tuple[list[dict], str, list[str]]:
    """OCR text has no table structure. Find the header line (Qty + Price/Amount), map its numeric
    columns in order, then read each row's trailing numbers into those columns. Lines without
    numbers continue the previous row's description (wrapped text)."""
    header_index = next(
        (i for i, line in enumerate(lines)
         if re.search(r"\b(qty|quantity)\b", line, re.I) and re.search(r"(price|rate|cost|amount|worth)\b", line, re.I)),
        None,
    )
    if header_index is None:
        return [], "missing", ["No line-item header (Qty / Price columns) was found in the OCR text."]
    header = lines[header_index]
    columns = _header_numeric_columns(header)
    has_sku = bool(re.search(r"\b(sku|item\s*code|code|part\s*(no|number))\b", header, re.I))
    items: list[dict] = []
    pending: list[str] = []  # text-only lines: wrapped description of the previous row, or of the next
    for line in lines[header_index + 1:]:
        if _TOTALS_ROW.match(line):
            break
        body = line
        index = re.match(r"^(\d{1,3})[.)]\s+", body)
        if index:
            body = body[index.end():]
        numbers = [(m.start(), parse_amount(m.group(1))) for m in _STANDALONE_NUM.finditer(body)]
        numbers = [(pos, n) for pos, n in numbers if n is not None]
        if len(numbers) < 2:
            if body.strip():
                pending.append(body.strip())
            continue
        values = numbers[-len(columns):] if len(numbers) >= len(columns) else numbers
        text = body[:values[0][0]].strip(" |-")
        if text and pending and items:  # this row has its own text: pending lines continued the last row
            items[-1]["description"] = (items[-1].get("description", "") + " " + " ".join(pending)).strip()
            pending = []
        if not text and pending:  # description printed on its own line(s) next to the numbers
            text, pending = " ".join(pending), []
        mapped = dict(zip(columns[-len(values):] if len(values) < len(columns) else columns, [n for _, n in values]))
        tokens = text.split()
        if has_sku and tokens:
            sku, description = tokens[0], " ".join(tokens[1:])
        else:
            sku, description = f"ITEM-{index.group(1) if index else len(items) + 1}", text
        description = re.sub(r"\s+(each|pcs|pc|units?|ea|kg|hrs?|hours?)$", "", description, flags=re.I)
        item: dict[str, str] = {"sku": sku}
        if description:
            item["description"] = description
        if "quantity" in mapped:
            item["quantity"] = mapped["quantity"]
        if "unit_price" in mapped:
            item["unit_price"] = mapped["unit_price"]
        if include_line_total and "line_total" in mapped:
            item["line_total"] = mapped["line_total"]
        if "quantity" in item:
            items.append(item)
    if pending and items:
        items[-1]["description"] = (items[-1].get("description", "") + " " + " ".join(pending)).strip()
    if not items:
        return [], "missing", ["Line-item rows could not be read from the OCR text."]
    for item in items:  # descriptions can carry trailing unit words after wrapping
        if "description" in item:
            item["description"] = re.sub(r"\s+", " ", item["description"]).strip()[:200]
    return items, "medium", []


def _sanity_checks(doc_type: str, fields: dict[str, FieldValue], subtotal: FieldValue, items: list[dict]) -> list[str]:
    warnings = []
    totals = [Decimal(i["line_total"]) for i in items if "line_total" in i]
    if subtotal.value and totals and len(totals) == len(items):
        if sum(totals) != Decimal(subtotal.value):
            warnings.append(f"Line totals add up to {sum(totals)}, but the document's subtotal is {subtotal.value}.")
    total = fields["total_amount"].value if "total_amount" in fields else None
    tax_value = fields["tax_amount"].value if "tax_amount" in fields else None
    if doc_type == "invoice" and subtotal.value is not None and total is not None:
        tax = Decimal(tax_value) if tax_value is not None else Decimal(0)
        if Decimal(subtotal.value) + tax != Decimal(total):
            warnings.append("Subtotal plus tax does not equal the stated total.")
    for name, value in fields.items():
        if value.confidence == "missing":
            warnings.append(f"'{name.replace('_', ' ')}' was not found in the document.")
    return warnings
