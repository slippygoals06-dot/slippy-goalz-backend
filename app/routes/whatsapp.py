"""WhatsApp Cloud API routes (JWT-protected)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import verify_token, require_owner
from app.whatsapp import send_whatsapp_message, send_whatsapp_text

router = APIRouter()


class TestWhatsAppRequest(BaseModel):
    to: str = Field(..., description="Recipient phone (e.g. 03001234567 or +923001234567)")
    template_name: str = Field(
        default="hello_world",
        description="Approved template name (default: Meta hello_world)",
    )


class SendWhatsAppTextRequest(BaseModel):
    to: str = Field(..., description="Recipient phone")
    text: str = Field(..., min_length=1, max_length=4096, description="Message body")


def _raise_from_result(result: dict) -> None:
    status = result.get("status_code") or 400
    if status >= 500 or result.get("status_code"):
        raise HTTPException(status_code=502, detail=result.get("error"))
    raise HTTPException(status_code=400, detail=result.get("error"))


@router.post("/test-whatsapp")
def test_whatsapp(body: TestWhatsAppRequest, user=Depends(require_owner)):
    """Send a template message to verify Cloud API credentials end-to-end."""
    result = send_whatsapp_message(body.to, body.template_name)
    if not result.get("ok"):
        _raise_from_result(result)
    return {
        "ok": True,
        "sent_by": user,
        "to": body.to,
        "template_name": body.template_name,
        "meta": result.get("data"),
    }


@router.post("/whatsapp/send")
def whatsapp_send_text(body: SendWhatsAppTextRequest, user=Depends(verify_token)):
    """Send a free-form text reply (inbox composer)."""
    result = send_whatsapp_text(body.to, body.text)
    if not result.get("ok"):
        _raise_from_result(result)
    return {
        "ok": True,
        "sent_by": user,
        "to": body.to,
        "meta": result.get("data"),
    }
