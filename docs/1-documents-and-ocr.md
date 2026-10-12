# 1 · Documents in: PDF extraction, OCR and Kaggle datasets

[← Back to README](../README.md) · Next: [2 · Agent A and A2A](2-agent-a-and-a2a.md)

Every review starts with two documents: an **invoice** (what the supplier says we owe) and a
**purchase order** (what we agreed to buy). The API turns each one into structured fields that a
person checks before the agents run.

Code: [`backend/app/ingestion/`](../backend/app/ingestion/)

| File | Job |
|---|---|
| `pdf_extract.py` | Document bytes → fields with confidence, line items and warnings |
| `ocr.py` | Tesseract OCR for scanned PDFs and images (orientation, upscaling, contrast) |
| `kaggle_import.py` | Loads random sets of real invoice images from four Kaggle datasets |

## Synthetic demo documents

`backend/samples/pdfs/` holds 10 generated PDFs (an invoice and a PO for five scenarios), built
from the JSON scenarios in `backend/samples/json/` by `scripts/2_generate_sample_pdfs.py`. All
companies and numbers are fictional, and every page says so. Two invoice layouts are used on
purpose:

| Layout | Supplier | Labels and format |
|---|---|---|
| Classic | Acme Office Supplies | `Invoice No:`, `Currency: GBP`, `VAT (20%)`, `Total Due: GBP 435.00`, columns `SKU / Qty / Unit Price / Amount` |
| Modern | Globex Industrial Parts | `Invoice Number`, `Purchase Order`, `$` amounts with no currency line, `Net Amount / Tax Amount / Amount Due`, columns `Item Code / Quantity / Price / Line Total` |
| Purchase order | NexusAI Demo Buyer | `PO Number:`, `Vendor:`, `Order Total` |

All 10 sample PDFs extract **exactly** to the JSON they were generated from.

## How extraction works

- **Document type:** from the title (the largest text on page 1). A PO in the invoice slot gives a warning.
- **Header fields:** found by **label**, with several wordings per field (`Invoice No`, `Inv No`,
  `Statement Ref`, `Total Due`, `Net Payable`, …). Amounts stay exact decimal strings.
- **Line items:** from the table whose header names the columns (`SKU` / `Item Code`,
  `Qty` / `Quantity`, `Price` / `Unit Price`, `Amount` / `Line Total`).
- **Confidence:** `high` = read from a label; `medium` = inferred (currency from a `$` sign, or
  anything read by OCR); `missing` = not found. Nothing is guessed silently.
- **Sanity warnings:** for example, line totals that don't add up. They are shown, not "fixed":
  verification is Agent B's job.
- **Limits:** PDF or image signature required, max 5 MB, 10 pages for text PDFs, 5 pages for OCR.

In the web app every value is colour-marked: **green** read from a label, **amber** inferred or
OCR (please check), **red** not found, **purple** edited by you.

## OCR for scans and photos (Tesseract)

- **When:** scanned PDFs (no text layer) and PNG / JPEG / TIFF images. Text PDFs are read directly.
- **Pipeline:** grayscale → upscale to 2600 px → auto-contrast → OCR with word positions. If the
  page reads as noise (mean word confidence below 60 — typical of a sideways phone photo), it is
  turned upright with Tesseract's orientation detection and read again; the better reading wins.
- **Setup:** Tesseract on `PATH`, or `TESSERACT_CMD` in `backend/.env`. In Docker it is
  installed with `apt-get install tesseract-ocr`.
- **Accuracy:** 195/196 fields on the synthetic set as images; **24/24** invoice numbers and
  totals on the labelled Kaggle scans.

| What real invoices needed | How it's handled |
|---|---|
| European numbers (`1 534,14`, `3,00`) | Locale-aware `parse_amount` |
| `Seller:` and `Client:` side by side | Word positions, so the seller's column is read alone |
| Summary row `Total $ 5 640,17 $ 564,02 $ 6 204,19` | Net, tax and gross taken from the row |
| Line items with no SKU, wrapped descriptions | Columns mapped from the header; rows get `ITEM-n` |
| Small scans misreading `37,75` as `37,15` | Upscaling to 2600 px (41/41 lines) |
| Sideways phone photos | Orientation detection (OSD) before OCR |

## Real invoices from Kaggle (four datasets)

The **Real invoices · Kaggle** tab loads invoices from four open datasets. Pick one with the
dataset chips; **↻ Refresh** moves to the next dataset and loads a fresh random set.

| Key | Dataset | What it tests | Licence |
|---|---|---|---|
| `hq` | High-Quality Invoice Images for OCR (osamahosamabdellatif) | clean scans, full ground-truth labels | ODbL |
| `clearocr` | clearOCR Invoice Document AI (from Kozlowski & Weichbroth, Mendeley Data) | scans with model-extracted JSON labels | CC BY 4.0 |
| `synthetic` | Realistic Synthetic Invoice Scans (nowshinaranini) | thousands of varied layouts and scan noise | CC BY 4.0 |
| `grocery` | Indian Grocery Tax Invoices (surajitsadhukhan) | real phone photos, the hardest OCR case | CC BY 4.0 |

- Only the chosen files are downloaded (3, 6 or 12 per set). Phone photos are shrunk to 1800 px.
- `KAGGLE_API_KEY` stays on the server; no Kaggle CLI is needed.
- Files go to `backend/samples/kaggle/<dataset>/` (git-ignored) with a `manifest.json`
  (labels, licence, attribution). Each refresh replaces that dataset's previous set.
- Command line: `python scripts\3_import_kaggle_samples.py --dataset grocery --count 6`
  (`--evaluate` measures OCR accuracy against the `hq` labels).

**Real invoices have no purchase order.** Press **Create demo PO from invoice** (or keep
*Auto-create a demo PO* ticked): the PO is copied from the invoice and marked purple. Change a
quantity or price and watch the agents catch it.
