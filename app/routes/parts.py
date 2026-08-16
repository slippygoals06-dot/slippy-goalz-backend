"""Parts batch receive / install with Avalanche checkpoint hashing."""
from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from typing import Literal, Optional

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from supabase import create_client
from web3 import Web3

from app.auth import verify_token
from app.blockchain import add_checkpoint, get_latest_hash
from app.config import SUPABASE_KEY, SUPABASE_URL
from app.errors import http_500

router = APIRouter()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

ZERO_HASH = "0x" + ("00" * 32)
EventType = Literal["received", "installed"]


# ── Models (Pydantic + Supabase tables — matches existing route style) ─────────


class PartsBatch(BaseModel):
    id: Optional[str] = None
    supplier_name: str
    batch_number: str
    part_type: str
    received_date: Optional[date] = None
    created_at: Optional[str] = None


class PartEvent(BaseModel):
    id: Optional[str] = None
    batch_id: str
    event_type: EventType
    repair_id: Optional[str] = None
    location: Optional[str] = None
    previous_hash: str
    data_hash: str
    tx_hash: str
    created_at: Optional[str] = None


class ReceivePartsBody(BaseModel):
    supplier_name: str = Field(..., min_length=1)
    batch_number: str = Field(..., min_length=1)
    part_type: str = Field(..., min_length=1)


class InstallPartsBody(BaseModel):
    batch_number: str = Field(..., min_length=1)
    repair_id: str = Field(..., min_length=1)
    location: str = Field(..., min_length=1)


def _keccak_json(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return Web3.to_hex(Web3.keccak(text=raw))


def _get_batch_by_number(batch_number: str) -> dict:
    res = (
        supabase.table("parts_batches")
        .select("*")
        .eq("batch_number", batch_number)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_number}")
    return res.data[0]


def _require_booking(repair_id: str) -> None:
    """Soft-FK: repair jobs live in bookings (no dedicated repairs table)."""
    res = (
        supabase.table("bookings")
        .select('"Booking ID"')
        .eq("Booking ID", repair_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        raise HTTPException(
            status_code=404,
            detail=f"Repair/booking not found: {repair_id}",
        )


@router.post("/receive")
def receive_parts(body: ReceivePartsBody, user=Depends(verify_token)):
    supplier_name = body.supplier_name.strip()
    batch_number = body.batch_number.strip()
    part_type = body.part_type.strip()
    if not supplier_name or not batch_number or not part_type:
        raise HTTPException(
            status_code=400,
            detail="supplier_name, batch_number, and part_type are required",
        )

    received_date = date.today().isoformat()
    previous_hash = ZERO_HASH

    try:
        existing = (
            supabase.table("parts_batches")
            .select("id")
            .eq("batch_number", batch_number)
            .limit(1)
            .execute()
        )
        if existing.data:
            raise HTTPException(
                status_code=409,
                detail=f"Batch already exists: {batch_number}",
            )

        batch_res = (
            supabase.table("parts_batches")
            .insert(
                {
                    "supplier_name": supplier_name,
                    "batch_number": batch_number,
                    "part_type": part_type,
                    "received_date": received_date,
                }
            )
            .execute()
        )
        if not batch_res.data:
            raise HTTPException(status_code=500, detail="Failed to create parts batch")
        batch = batch_res.data[0]

        data_hash = _keccak_json(
            {
                "event_type": "received",
                "supplier_name": supplier_name,
                "batch_number": batch_number,
                "part_type": part_type,
                "received_date": received_date,
                "batch_id": batch["id"],
            }
        )

        tx_hash = add_checkpoint(batch_number, data_hash, previous_hash)

        event_res = (
            supabase.table("part_events")
            .insert(
                {
                    "batch_id": batch["id"],
                    "event_type": "received",
                    "repair_id": None,
                    "location": None,
                    "previous_hash": previous_hash,
                    "data_hash": data_hash,
                    "tx_hash": tx_hash,
                }
            )
            .execute()
        )
        if not event_res.data:
            raise HTTPException(status_code=500, detail="Failed to create part event")

        return {"batch": batch, "event": event_res.data[0]}
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.post("/install")
def install_parts(body: InstallPartsBody, user=Depends(verify_token)):
    batch_number = body.batch_number.strip()
    repair_id = body.repair_id.strip()
    location = body.location.strip()
    if not batch_number or not repair_id or not location:
        raise HTTPException(
            status_code=400,
            detail="batch_number, repair_id, and location are required",
        )

    try:
        batch = _get_batch_by_number(batch_number)

        received = (
            supabase.table("part_events")
            .select("id")
            .eq("batch_id", batch["id"])
            .eq("event_type", "received")
            .limit(1)
            .execute()
        )
        if not received.data:
            raise HTTPException(
                status_code=400,
                detail="Batch has no received event; receive parts first",
            )

        _require_booking(repair_id)

        previous_hash = get_latest_hash(batch_number)
        if previous_hash.lower() == ZERO_HASH.lower():
            raise HTTPException(
                status_code=400,
                detail="No on-chain checkpoint for this batch; receive parts first",
            )

        data_hash = _keccak_json(
            {
                "event_type": "installed",
                "batch_number": batch_number,
                "batch_id": batch["id"],
                "repair_id": repair_id,
                "location": location,
            }
        )

        tx_hash = add_checkpoint(batch_number, data_hash, previous_hash)

        event_res = (
            supabase.table("part_events")
            .insert(
                {
                    "batch_id": batch["id"],
                    "event_type": "installed",
                    "repair_id": repair_id,
                    "location": location,
                    "previous_hash": previous_hash,
                    "data_hash": data_hash,
                    "tx_hash": tx_hash,
                }
            )
            .execute()
        )
        if not event_res.data:
            raise HTTPException(status_code=500, detail="Failed to create part event")

        return {"event": event_res.data[0], "batch": batch}
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)


@router.get("/{batch_number}/qrcode")
def get_batch_qrcode(batch_number: str):
    _get_batch_by_number(batch_number)
    try:
        url = f"https://irepair-dashboard.vercel.app/verify/{batch_number}"
        img = qrcode.make(url)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return StreamingResponse(buf, media_type="image/png")
    except Exception as e:
        raise http_500(e)


@router.get("/{batch_number}/verify")
def verify_batch(batch_number: str):
    batch = _get_batch_by_number(batch_number)

    try:
        events_res = (
            supabase.table("part_events")
            .select("event_type, location, repair_id, data_hash, created_at")
            .eq("batch_id", batch["id"])
            .order("created_at", desc=False)
            .execute()
        )
        events = events_res.data or []
    except HTTPException:
        raise
    except Exception as e:
        raise http_500(e)

    try:
        on_chain_hash = get_latest_hash(batch_number)
    except Exception as e:
        raise http_500(e)

    latest_hash = events[-1]["data_hash"] if events else None
    verified = (
        latest_hash is not None
        and on_chain_hash.lower() == str(latest_hash).lower()
    )

    return {
        "batch": {
            "supplier_name": batch["supplier_name"],
            "batch_number": batch["batch_number"],
            "part_type": batch["part_type"],
            "received_date": batch["received_date"],
        },
        "events": [
            {
                "event_type": e["event_type"],
                "location": e.get("location"),
                "repair_id": None,
                "timestamp": e.get("created_at"),
            }
            for e in events
        ],
        "verified": verified,
    }
