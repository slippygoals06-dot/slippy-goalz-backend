"""Permanent customer identity: one row per normalized phone."""
from __future__ import annotations

from typing import Any, Dict, Optional

from postgrest.exceptions import APIError
from supabase import create_client

from app.config import SUPABASE_KEY, SUPABASE_URL
from app.phone import normalize_phone

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def find_or_create_customer(
    phone: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Resolve a customer by canonical phone (+92…).
    Race-safe: UNIQUE(phone) + re-select on conflict.
    Updates missing profile name/email; never touches booking snapshots.
    """
    canonical = normalize_phone(phone)
    if not canonical:
        return None

    name_clean = (name or "").strip() or None
    email_clean = (email or "").strip() or None

    existing = (
        supabase.table("customers")
        .select("*")
        .eq("phone", canonical)
        .limit(1)
        .execute()
    )
    if existing.data:
        row = existing.data[0]
        patch: Dict[str, Any] = {}
        if name_clean and not (row.get("name") or "").strip():
            patch["name"] = name_clean
        if email_clean and not (row.get("email") or "").strip():
            patch["email"] = email_clean
        if patch:
            try:
                updated = (
                    supabase.table("customers")
                    .update(patch)
                    .eq("id", row["id"])
                    .execute()
                )
                if updated.data:
                    return updated.data[0]
            except Exception:
                pass
        return row

    payload: Dict[str, Any] = {"phone": canonical}
    if name_clean:
        payload["name"] = name_clean
    if email_clean:
        payload["email"] = email_clean

    try:
        inserted = supabase.table("customers").insert(payload).execute()
        if inserted.data:
            return inserted.data[0]
    except APIError as err:
        # Concurrent insert hit UNIQUE(phone) — fetch the winner.
        msg = str(getattr(err, "message", "") or err).lower()
        code = str(getattr(err, "code", "") or "")
        if code == "23505" or "duplicate" in msg or "unique" in msg:
            again = (
                supabase.table("customers")
                .select("*")
                .eq("phone", canonical)
                .limit(1)
                .execute()
            )
            if again.data:
                return again.data[0]
        raise
    except Exception as err:
        # Some supabase clients surface uniqueness as generic Exception.
        msg = str(err).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            again = (
                supabase.table("customers")
                .select("*")
                .eq("phone", canonical)
                .limit(1)
                .execute()
            )
            if again.data:
                return again.data[0]
        raise

    # Insert returned empty — last-chance select
    again = (
        supabase.table("customers")
        .select("*")
        .eq("phone", canonical)
        .limit(1)
        .execute()
    )
    return again.data[0] if again.data else None
