from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from io import BytesIO
from supabase import create_client
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from app.config import SUPABASE_URL, SUPABASE_KEY
from app.auth import verify_token
from app.errors import http_500
from app.audit import log_audit_event

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

BUSINESS_NAME = "Slippy Goalz Arena"
BUSINESS_TAGLINE = "Phone Repair Services"


class CompleteBookingBody(BaseModel):
    amount: float


class InvoiceStatusUpdate(BaseModel):
    status: str  # paid | unpaid


def _next_invoice_number() -> str:
    """Generate INV-YYYYMMDD-XXXX using today's date + short unique suffix."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    suffix = datetime.now(timezone.utc).strftime("%H%M%S")
    return f"INV-{today}-{suffix}"


def _fetch_booking(booking_id: str) -> dict:
    res = supabase.table("bookings").select("*").eq("Booking ID", booking_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Booking not found")
    return res.data[0]


def _fetch_invoice(invoice_id: str) -> dict:
    res = supabase.table("invoices").select("*").eq("id", invoice_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return res.data[0]


def _enrich_invoice(inv: dict) -> dict:
    """Attach booking customer/service/device for dashboard display."""
    booking = None
    try:
        booking = _fetch_booking(inv["booking_id"])
    except HTTPException:
        pass
    return {
        **inv,
        "customer_name": booking.get("Name") if booking else None,
        "phone": booking.get("Phone") if booking else None,
        "service": booking.get("Service") if booking else None,
        "device": booking.get("Device") if booking else None,
        "booking_date": booking.get("Date") if booking else None,
    }


@router.get("/")
def list_invoices(user=Depends(verify_token)):
    try:
        res = (
            supabase.table("invoices")
            .select("*")
            .order("created_at", desc=True)
            .execute()
        )
        invoices = res.data or []
        return [_enrich_invoice(inv) for inv in invoices]
    except Exception as e:
        raise http_500(e)


@router.post("/from-booking/{booking_id}")
def complete_and_invoice(booking_id: str, body: CompleteBookingBody, user=Depends(verify_token)):
    """
    Mark booking Completed and create an invoice.
    Amount can override the booking's saved amount.
    """
    try:
        booking = _fetch_booking(booking_id)

        if booking.get("Status") == "Completed":
            existing = (
                supabase.table("invoices")
                .select("*")
                .eq("booking_id", booking_id)
                .limit(1)
                .execute()
            )
            if existing.data:
                raise HTTPException(
                    status_code=400,
                    detail="Booking already completed with an invoice",
                )

        amount = float(body.amount)
        if amount < 0:
            raise HTTPException(status_code=400, detail="amount must be >= 0")

        # Persist amount + Completed status on booking
        supabase.table("bookings").update({
            "Status": "Completed",
            "amount": amount,
        }).eq("Booking ID", booking_id).execute()

        invoice_number = _next_invoice_number()
        inv_res = supabase.table("invoices").insert({
            "booking_id": booking_id,
            "amount": amount,
            "status": "unpaid",
            "invoice_number": invoice_number,
        }).execute()

        if not inv_res.data:
            raise HTTPException(status_code=500, detail="Failed to create invoice")

        invoice = inv_res.data[0]
        log_audit_event(
            actor=user,
            action="completed_invoiced",
            booking_id=booking_id,
            invoice_id=invoice.get("id"),
            details={
                "name": booking.get("Name"),
                "amount": amount,
                "invoice_number": invoice.get("invoice_number"),
                "from_status": booking.get("Status"),
                "to_status": "Completed",
            },
        )
        return _enrich_invoice(invoice)
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.get("/{invoice_id}")
def get_invoice(invoice_id: str, user=Depends(verify_token)):
    try:
        return _enrich_invoice(_fetch_invoice(invoice_id))
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.put("/{invoice_id}/status")
def update_invoice_status(invoice_id: str, body: InvoiceStatusUpdate, user=Depends(verify_token)):
    status = (body.status or "").lower().strip()
    if status not in ("paid", "unpaid"):
        raise HTTPException(status_code=400, detail="status must be 'paid' or 'unpaid'")
    try:
        before = _fetch_invoice(invoice_id)
        res = (
            supabase.table("invoices")
            .update({"status": status})
            .eq("id", invoice_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Invoice not found")

        log_audit_event(
            actor=user,
            action="invoice_status_changed",
            booking_id=before.get("booking_id"),
            invoice_id=invoice_id,
            details={
                "from": before.get("status"),
                "to": status,
                "invoice_number": before.get("invoice_number"),
                "amount": before.get("amount"),
            },
        )
        return _enrich_invoice(res.data[0])
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


def _build_invoice_pdf(invoice: dict, booking: Optional[dict]) -> BytesIO:
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "InvTitle",
        parent=styles["Heading1"],
        fontSize=22,
        textColor=HexColor("#1e1b4b"),
        spaceAfter=2,
        alignment=TA_LEFT,
    )
    tagline_style = ParagraphStyle(
        "Tagline",
        parent=styles["Normal"],
        fontSize=10,
        textColor=HexColor("#6b7280"),
        spaceAfter=12,
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=9,
        textColor=HexColor("#6b7280"),
        spaceBefore=4,
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=11,
        textColor=black,
        spaceAfter=2,
    )
    right_meta = ParagraphStyle(
        "RightMeta",
        parent=styles["Normal"],
        fontSize=10,
        textColor=HexColor("#374151"),
        alignment=TA_RIGHT,
    )
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=9,
        textColor=HexColor("#9ca3af"),
        alignment=TA_CENTER,
        spaceBefore=24,
    )

    customer = (booking or {}).get("Name") or "—"
    phone = (booking or {}).get("Phone") or "—"
    email = (booking or {}).get("Email") or "—"
    device = (booking or {}).get("Device") or "—"
    service = (booking or {}).get("Service") or "—"
    amount = invoice.get("amount") or 0
    inv_no = invoice.get("invoice_number") or "—"
    status = (invoice.get("status") or "unpaid").upper()
    created = invoice.get("created_at") or ""
    try:
        created_fmt = datetime.fromisoformat(created.replace("Z", "+00:00")).strftime("%d %b %Y")
    except Exception:
        created_fmt = created[:10] if created else "—"

    story = []
    story.append(Paragraph(BUSINESS_NAME, title_style))
    story.append(Paragraph(BUSINESS_TAGLINE, tagline_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=HexColor("#6366f1"), spaceAfter=14))

    header_data = [
        [
            Paragraph("<b>INVOICE</b>", value_style),
            Paragraph(f"Invoice #: <b>{inv_no}</b><br/>Date: {created_fmt}<br/>Status: {status}", right_meta),
        ]
    ]
    header_table = Table(header_data, colWidths=[95 * mm, 75 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 16))

    story.append(Paragraph("BILL TO", label_style))
    story.append(Paragraph(f"<b>{customer}</b>", value_style))
    story.append(Paragraph(f"Phone: {phone}", value_style))
    if email and email != "—":
        story.append(Paragraph(f"Email: {email}", value_style))
    story.append(Spacer(1, 14))

    table_data = [
        [
            Paragraph("<b>Description</b>", ParagraphStyle("th", parent=styles["Normal"], fontSize=10, textColor=white)),
            Paragraph("<b>Details</b>", ParagraphStyle("th2", parent=styles["Normal"], fontSize=10, textColor=white)),
            Paragraph("<b>Amount</b>", ParagraphStyle("th3", parent=styles["Normal"], fontSize=10, textColor=white, alignment=TA_RIGHT)),
        ],
        [
            Paragraph(service, value_style),
            Paragraph(f"Device: {device}", value_style),
            Paragraph(f"Rs {float(amount):,.0f}", ParagraphStyle("amt", parent=styles["Normal"], fontSize=11, alignment=TA_RIGHT)),
        ],
    ]
    items = Table(table_data, colWidths=[70 * mm, 70 * mm, 30 * mm])
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#4f46e5")),
        ("BACKGROUND", (0, 1), (-1, 1), HexColor("#f5f3ff")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#e5e7eb")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, HexColor("#4338ca")),
    ]))
    story.append(items)
    story.append(Spacer(1, 16))

    total_data = [
        [
            "",
            Paragraph("<b>Total</b>", ParagraphStyle("tot", parent=styles["Normal"], fontSize=12, alignment=TA_RIGHT)),
            Paragraph(f"<b>Rs {float(amount):,.0f}</b>", ParagraphStyle("totv", parent=styles["Normal"], fontSize=14, alignment=TA_RIGHT, textColor=HexColor("#1e1b4b"))),
        ]
    ]
    total_table = Table(total_data, colWidths=[90 * mm, 40 * mm, 40 * mm])
    total_table.setStyle(TableStyle([
        ("BACKGROUND", (1, 0), (-1, 0), HexColor("#eef2ff")),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("BOX", (1, 0), (-1, 0), 0.5, HexColor("#c7d2fe")),
    ]))
    story.append(total_table)

    story.append(Paragraph(
        f"Thank you for choosing {BUSINESS_NAME}. For questions about this invoice, contact the shop.",
        footer_style,
    ))

    doc.build(story)
    buffer.seek(0)
    return buffer


@router.get("/{invoice_id}/pdf")
def download_invoice_pdf(invoice_id: str, user=Depends(verify_token)):
    try:
        invoice = _fetch_invoice(invoice_id)
        booking = None
        try:
            booking = _fetch_booking(invoice["booking_id"])
        except HTTPException:
            pass

        pdf = _build_invoice_pdf(invoice, booking)
        filename = f"{invoice.get('invoice_number', 'invoice')}.pdf"
        return StreamingResponse(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)
