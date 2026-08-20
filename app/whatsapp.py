"""WhatsApp Business Cloud API helper (template + free-form text)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

import httpx

from app.config import WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID
from app.phone import normalize_phone

logger = logging.getLogger("fixpro_whatsapp")

GRAPH_API_VERSION = "v22.0"
DEFAULT_LANGUAGE = "en_US"


def _digits_for_whatsapp(to_number: str) -> Optional[str]:
    """Return country-code digits without '+', or None if invalid."""
    normalized = normalize_phone(to_number)
    if not normalized:
        digits = "".join(c for c in str(to_number) if c.isdigit())
        if len(digits) >= 10:
            return digits
        return None
    return normalized.lstrip("+")


def _post_messages(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        msg = (
            "WhatsApp not configured: set WHATSAPP_ACCESS_TOKEN and "
            "WHATSAPP_PHONE_NUMBER_ID in the environment"
        )
        logger.warning(msg)
        return {"ok": False, "error": msg}

    url = (
        f"https://graph.facebook.com/{GRAPH_API_VERSION}/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            res = client.post(url, headers=headers, json=payload)
    except Exception as e:
        msg = f"WhatsApp API request failed: {e}"
        logger.error(msg)
        return {"ok": False, "error": msg}

    try:
        body = res.json()
    except Exception:
        body = {"raw": res.text}

    if res.is_success:
        return {"ok": True, "data": body}

    err = body.get("error", body) if isinstance(body, dict) else body
    msg = f"WhatsApp API error ({res.status_code}): {err}"
    logger.error(msg)
    return {"ok": False, "error": msg, "status_code": res.status_code, "data": body}


def _template_body_component(body_params: Optional[Sequence[str]]) -> Optional[Dict[str, Any]]:
    if not body_params:
        return None
    params: List[Dict[str, str]] = []
    for p in body_params:
        text = str(p if p is not None else "").strip() or "—"
        params.append({"type": "text", "text": text[:1024]})
    if not params:
        return None
    return {"type": "body", "parameters": params}


def send_whatsapp_message(
    to_number: str,
    template_name: str = "hello_world",
    *,
    language_code: str = DEFAULT_LANGUAGE,
    body_params: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Send a WhatsApp template message via Meta Cloud API.

    Optional body_params map to {{1}}, {{2}}, … in an Approved template body.
    Returns {"ok": True, "data": ...} on success, or
    {"ok": False, "error": "..."} on failure — never raises.
    """
    to_digits = _digits_for_whatsapp(to_number)
    if not to_digits:
        msg = f"Invalid recipient phone number: {to_number!r}"
        logger.warning(msg)
        return {"ok": False, "error": msg}

    if not template_name or not str(template_name).strip():
        msg = "template_name is required"
        logger.warning(msg)
        return {"ok": False, "error": msg}

    template: Dict[str, Any] = {
        "name": template_name.strip(),
        "language": {"code": language_code},
    }
    component = _template_body_component(body_params)
    if component:
        template["components"] = [component]

    payload = {
        "messaging_product": "whatsapp",
        "to": to_digits,
        "type": "template",
        "template": template,
    }
    result = _post_messages(payload)
    if result.get("ok"):
        logger.info("WhatsApp template %r sent to %s", template_name, to_digits)
    return result


def send_whatsapp_text(to_number: str, text: str) -> Dict[str, Any]:
    """
    Send a free-form WhatsApp text message (customer-care window required by Meta).

    Returns {"ok": True, "data": ...} or {"ok": False, "error": "..."} — never raises.
    """
    to_digits = _digits_for_whatsapp(to_number)
    if not to_digits:
        msg = f"Invalid recipient phone number: {to_number!r}"
        logger.warning(msg)
        return {"ok": False, "error": msg}

    body = (text or "").strip()
    if not body:
        return {"ok": False, "error": "Message text is required"}
    if len(body) > 4096:
        return {"ok": False, "error": "Message text exceeds 4096 characters"}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_digits,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }
    result = _post_messages(payload)
    if result.get("ok"):
        logger.info("WhatsApp text sent to %s", to_digits)
    return result
