"""Generate audit-ready CSV and PDF case reports without altering case data."""

from __future__ import annotations

import csv
import re
from io import BytesIO, StringIO

from stage5_gateway.schemas import CaseDetailResponse


def safe_filename(transaction_id: str, extension: str) -> str:
    """Return a browser-safe report filename derived from the transaction ID."""
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", transaction_id).strip("._") or "case"
    return f"sentinel-case-{stem}.{extension}"


def build_case_csv(case: CaseDetailResponse) -> bytes:
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Field", "Value"])
    writer.writerows(
        [
            ["Transaction ID", case.transaction_id],
            ["System Risk Score", f"{case.fraud_score:.2f}"],
            ["Decision Directive", case.action],
            ["Primary Reason", case.primary_reason],
            ["Investigation Priority", case.investigation_priority],
            ["Reviewer Status", case.review_status],
            ["Reviewer Notes", case.investigator_note or ""],
            ["SAR Narrative", case.sar_narrative or ""],
            ["Last Updated", case.updated_at.isoformat() if case.updated_at else ""],
        ]
    )
    return output.getvalue().encode("utf-8-sig")


def build_case_pdf(case: CaseDetailResponse) -> bytes:
    """Create a compact, printable PDF report using the lightweight fpdf2 package."""
    try:
        from fpdf import FPDF
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("PDF export requires the fpdf2 package.") from exc

    def compatible(value: object) -> str:
        return str(value).encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(text="Sentinel Case Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)

    fields = [
        ("Transaction ID", case.transaction_id),
        ("System Risk Score", f"{case.fraud_score:.2f} / 100"),
        ("Decision Directive", case.action),
        ("Primary Reason", case.primary_reason),
        ("Investigation Priority", case.investigation_priority),
        ("Reviewer Status", case.review_status),
        ("Last Updated", case.updated_at.isoformat() if case.updated_at else "Not reviewed"),
    ]
    for label, value in fields:
        pdf.set_font("Helvetica", "B", 10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=0, text=compatible(label))
        pdf.set_font("Helvetica", size=10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=0, text=compatible(value))
        pdf.ln(1)

    for heading, value in [
        ("Human Reviewer Notes", case.investigator_note or "No reviewer notes recorded."),
        ("SAR Narrative", case.sar_narrative or "No SAR narrative was generated for this case."),
    ]:
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_x(pdf.l_margin)
        pdf.cell(text=heading, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(w=0, text=compatible(value))

    return bytes(pdf.output())
