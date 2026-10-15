"""Native invoice PDF generation — independent of Zoho. Used whenever an
invoice is issued, regardless of whether the org also pushes to Zoho."""
import io

from fpdf import FPDF


def build_invoice_pdf(org: dict, client, invoice) -> bytes:
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(232, 98, 42)
    pdf.cell(0, 10, org.get("ORG_NAME", "Your Company"), ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 5, f"GSTIN: {org.get('ORG_GST', '')}", ln=True)
    pdf.ln(4)

    pdf.set_draw_color(220, 220, 220)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(30, 30, 30)
    title = {"debit_note": "Debit Note", "quote": "Quotation"}.get(getattr(invoice, "doc_type", "invoice"), "Invoice")
    pdf.cell(0, 8, f"{title} {invoice.invoice_no}", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 5, f"Period: {invoice.period}", ln=True)
    pdf.cell(0, 5, f"{'Validity' if title == 'Quotation' else 'Due date'}: {invoice.due_date or '-'}", ln=True)
    pdf.ln(3)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(30, 30, 30)
    pdf.cell(0, 6, "Bill to:", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 5, client.name, ln=True)
    pdf.cell(0, 5, f"GSTIN: {client.gst or '-'}  |  State: {client.state or '-'}", ln=True)
    pdf.ln(4)

    pdf.set_fill_color(232, 98, 42)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(90, 7, "Description", 1, 0, "L", True)
    pdf.cell(30, 7, "Qty", 1, 0, "R", True)
    pdf.cell(70, 7, "Amount", 1, 1, "R", True)

    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "", 9)
    for line in invoice.lines:
        pdf.cell(90, 7, str(line.get("label", ""))[:48], 1)
        pdf.cell(30, 7, str(line.get("qty", "")), 1, 0, "R")
        pdf.cell(70, 7, f"Rs. {line.get('amount', 0):,.2f}", 1, 1, "R")

    pdf.ln(3)
    pdf.set_font("Helvetica", "", 9)
    for label, value in [
        ("Subtotal", invoice.subtotal),
        (invoice.gst_type.replace("_", " + "), invoice.gst),
        ("TDS deducted", -invoice.tds),
    ]:
        pdf.cell(120, 6, "")
        pdf.cell(40, 6, label)
        pdf.cell(30, 6, f"Rs. {value:,.2f}", 0, 1, "R")

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(120, 8, "")
    pdf.cell(40, 8, "Total")
    pdf.cell(30, 8, f"Rs. {invoice.total:,.2f}", 0, 1, "R")

    pdf.ln(6)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(0, 4, "This is a system-generated invoice. Payment is tracked against bank "
                          "statement reconciliation; please use the invoice number as your payment reference.")

    return bytes(pdf.output())
