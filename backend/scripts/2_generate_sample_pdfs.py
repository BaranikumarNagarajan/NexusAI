"""Generate synthetic invoice and purchase-order PDFs from the sample scenarios.

All companies, addresses and numbers are fictional. Two invoice layouts with different
wording are used on purpose, so the extractor is exercised on more than one format:

  * "classic" (Acme Office Supplies): "Invoice No:", "Currency: GBP", "VAT (20%)", "Total Due"
  * "modern"  (Globex Industrial Parts): "Invoice Number", "Purchase Order", "$" amounts with
    no currency line, "Item Code / Quantity / Price / Line Total" columns

Usage (from backend/):
    python scripts/2_generate_sample_pdfs.py          # writes samples/pdfs/*.pdf
"""

import json
import sys
from decimal import Decimal
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from fpdf.fonts import FontFace

BACKEND = Path(__file__).resolve().parent.parent
SAMPLES = BACKEND / "samples"
OUT = SAMPLES / "pdfs"
SCENARIOS = ["matching", "supplier_mismatch", "currency_mismatch", "quantity_total_mismatch", "missing_information"]

BUYER = ["NexusAI Demo Buyer Ltd", "1 Example Way", "Sampletown ST1 2AB"]  # fictional
ADDRESSES = {
    "Acme Office Supplies Ltd": ["12 Paper Lane", "Inkford IN4 5PQ", "VAT No. GB000000001"],
    "Globex Industrial Parts": ["400 Fastener Blvd", "Springfield, ZZ 00000", "Tax ID 00-0000001"],
    "Initech Stationery plc": ["9 Stapler Court", "Penton PN1 1ZZ", "VAT No. GB000000002"],
}
DATES = {"matching": "2026-09-14", "supplier_mismatch": "2026-09-16", "currency_mismatch": "2026-09-18",
         "quantity_total_mismatch": "2026-09-21", "missing_information": "2026-09-23"}
INK, MUTED, ACCENT, RULE = (23, 26, 38), (95, 102, 122), (79, 70, 229), (226, 229, 238)


def money(value: str | None, currency: str | None, style: str) -> str:
    if value is None:
        return ""
    amount = f"{Decimal(value):,.2f}"
    if style == "modern" and currency == "USD":
        return f"${amount}"
    return amount


def header(pdf: FPDF, company: str, title: str, accent=ACCENT) -> None:
    pdf.set_fill_color(*accent)
    pdf.rect(0, 0, 210, 4, style="F")
    pdf.set_xy(15, 14)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(110, 8, company)
    pdf.set_font("Helvetica", "B", 24)
    pdf.set_text_color(*accent)
    pdf.cell(70, 8, title, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 9)
    for line in ADDRESSES.get(company, []):
        pdf.cell(110, 4.6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(5)


def meta(pdf: FPDF, rows: list[tuple[str, str]], right: list[str], right_title: str) -> None:
    y = pdf.get_y()
    pdf.set_font("Helvetica", "", 10)
    for label, value in rows:
        pdf.set_text_color(*MUTED)
        pdf.set_x(15)
        pdf.cell(38, 6, label)
        pdf.set_text_color(*INK)
        pdf.cell(60, 6, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    end = pdf.get_y()
    pdf.set_xy(120, y)
    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(75, 6, right_title, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*INK)
    for line in right:
        pdf.cell(75, 5.5, line, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.set_y(max(end, pdf.get_y()) + 6)


def items_table(pdf: FPDF, headings: list[str], rows: list[list[str]], widths: tuple) -> None:
    pdf.set_draw_color(*RULE)
    pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(*INK)
    pdf.set_font("Helvetica", "", 9.5)
    heading = FontFace(emphasis="BOLD", color=INK, fill_color=(240, 242, 247))
    with pdf.table(col_widths=widths, text_align=("LEFT", "LEFT", "RIGHT", "RIGHT", "RIGHT"),
                   headings_style=heading, cell_fill_mode="NONE", line_height=7, padding=1.6) as table:
        head = table.row()
        for h in headings:
            head.cell(h)
        for values in rows:
            row = table.row()
            for v in values:
                row.cell(v)
    pdf.ln(4)


def totals(pdf: FPDF, rows: list[tuple[str, str]], bold_last=True) -> None:
    for i, (label, value) in enumerate(rows):
        last = bold_last and i == len(rows) - 1
        pdf.set_x(115)
        pdf.set_font("Helvetica", "B" if last else "", 11 if last else 10)
        pdf.set_text_color(*(INK if last else MUTED))
        pdf.cell(45, 7, label + ":")
        pdf.set_text_color(*INK)
        pdf.cell(35, 7, value, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def footer(pdf: FPDF, text: str) -> None:
    pdf.set_y(-28)
    pdf.set_draw_color(*RULE)
    pdf.line(15, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(3)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(180, 4, text + "\nSYNTHETIC DOCUMENT FOR THE NEXUSAI DEMO - NOT A REAL INVOICE OR ORDER.")


def invoice_pdf(scenario: str, inv: dict) -> FPDF:
    supplier, cur = inv["supplier_name"], inv.get("currency")
    style = "modern" if supplier.startswith("Globex") else "classic"
    pdf = FPDF(format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(False)
    pdf.add_page()
    lines = inv.get("line_items", [])
    subtotal = sum((Decimal(l["line_total"]) for l in lines), Decimal(0))

    if style == "classic":
        header(pdf, supplier, "INVOICE")
        rows = [("Invoice No:", inv["invoice_number"]), ("Invoice Date:", DATES[scenario])]
        if inv.get("po_reference"):
            rows.append(("PO Reference:", inv["po_reference"]))
        rows += [("Currency:", cur), ("Payment Terms:", "30 days")]
        meta(pdf, rows, BUYER, "BILL TO")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, f"From: {supplier}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
        items_table(pdf, ["SKU", "Description", "Qty", "Unit Price", "Amount"],
                    [[l["sku"], l.get("description", ""), l["quantity"], money(l["unit_price"], cur, style),
                      money(l["line_total"], cur, style)] for l in lines], (26, 74, 18, 30, 32))
        totals(pdf, [("Subtotal", money(str(subtotal), cur, style)),
                     ("VAT (20%)", money(inv.get("tax_amount"), cur, style)),
                     ("Total Due", f"{cur} {money(inv['total_amount'], cur, style)}")])
        footer(pdf, f"Please pay {supplier} within 30 days quoting {inv['invoice_number']}.")
    else:
        header(pdf, supplier, "TAX INVOICE", accent=(15, 118, 110))
        rows = [("Invoice Number", inv["invoice_number"]), ("Issue Date", DATES[scenario])]
        if inv.get("po_reference"):
            rows.append(("Purchase Order", inv["po_reference"]))
        meta(pdf, rows, BUYER, "INVOICE TO")
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, f"Supplier: {supplier}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
        items_table(pdf, ["Item Code", "Item", "Quantity", "Price", "Line Total"],
                    [[l["sku"], l.get("description", "Industrial component"), l["quantity"],
                      money(l["unit_price"], cur, style), money(l["line_total"], cur, style)] for l in lines],
                    (28, 70, 22, 28, 32))
        totals(pdf, [("Net Amount", money(str(subtotal), cur, style)),
                     ("Tax Amount", money(inv.get("tax_amount"), cur, style)),
                     ("Amount Due", money(inv["total_amount"], cur, style))])
        footer(pdf, f"Remit to {supplier}. Reference {inv['invoice_number']} on all payments.")
    return pdf


def po_pdf(scenario: str, po: dict, inv: dict) -> FPDF:
    cur = po.get("currency")
    descriptions = {l["sku"]: l.get("description", "") for l in inv.get("line_items", [])}
    pdf = FPDF(format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(False)
    pdf.add_page()
    header(pdf, BUYER[0], "PURCHASE ORDER", accent=(30, 64, 175))
    rows = [("PO Number:", po["po_number"]), ("Order Date:", DATES[scenario])]
    if cur:
        rows.append(("Currency:", cur))
    supplier = po.get("supplier_name")
    meta(pdf, rows, [supplier or "", *ADDRESSES.get(supplier or "", [])[:2]], "SUPPLIER")
    if supplier:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, f"Vendor: {supplier}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)
    lines = po.get("line_items", [])
    items_table(pdf, ["SKU", "Description", "Qty", "Unit Price", "Amount"],
                [[l["sku"], descriptions.get(l["sku"], "") or "As quoted", l["quantity"], money(l["unit_price"], cur, "po"),
                  money(str(Decimal(l["quantity"]) * Decimal(l["unit_price"])), cur, "po")] for l in lines],
                (26, 74, 18, 30, 32))
    if po.get("total_amount"):
        totals(pdf, [("Order Total", f"{cur + ' ' if cur else ''}{money(po['total_amount'], cur, 'po')}")])
    footer(pdf, "Deliver to NexusAI Demo Buyer Ltd. Quote the PO number on all invoices.")
    return pdf


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for scenario in SCENARIOS:
        request = json.loads((SAMPLES / "json" / f"{scenario}.json").read_text(encoding="utf-8"))
        invoice_pdf(scenario, request["invoice"]).output(str(OUT / f"{scenario}_invoice.pdf"))
        po_pdf(scenario, request["purchase_order"], request["invoice"]).output(str(OUT / f"{scenario}_po.pdf"))
        print(f"wrote samples/pdfs/{scenario}_invoice.pdf and {scenario}_po.pdf")


if __name__ == "__main__":
    sys.exit(main())
