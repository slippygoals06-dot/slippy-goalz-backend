"""
Railway cron entrypoint: send WhatsApp appointment reminders, then exit.

Start command:
  python -m app.jobs.send_reminders

Cron (UTC): */15 * * * *

Safety: REMINDERS_ENABLED must be true to actually call Meta / flip flags.
Until then the job dry-runs (logs candidates, exits 0, does not mark sent).
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Any, Dict, List, Tuple

from supabase import create_client

from app.appointment_time import KARACHI, parse_appointment_datetime_karachi
from app.config import (
    REMINDERS_ENABLED,
    SUPABASE_KEY,
    SUPABASE_URL,
)
from app.whatsapp import send_whatsapp_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | reminders | %(message)s",
)
logger = logging.getLogger("fixpro_reminders")

TEMPLATE_24H = "appointment_reminder_24h"
TEMPLATE_URGENT = "appointment_reminder_urgent"

# Inclusive hour windows (hours until appointment)
WINDOW_24H = (23.0, 25.0)
WINDOW_URGENT = (5.0, 7.0)


def _hours_until(appointment: datetime, now: datetime) -> float:
    return (appointment - now).total_seconds() / 3600.0


def _in_window(hours: float, window: Tuple[float, float]) -> bool:
    lo, hi = window
    return lo <= hours <= hi


def _fetch_confirmed(supabase) -> List[Dict[str, Any]]:
    res = (
        supabase.table("bookings")
        .select("*")
        .eq("Status", "Confirmed")
        .execute()
    )
    return res.data or []


def _mark_sent(supabase, booking_id: str, column: str) -> None:
    supabase.table("bookings").update({column: True}).eq(
        "Booking ID", booking_id
    ).execute()


def _process_booking(
    supabase,
    booking: Dict[str, Any],
    now: datetime,
    *,
    enabled: bool,
) -> Dict[str, int]:
    stats = {"sent_24h": 0, "sent_urgent": 0, "dry_24h": 0, "dry_urgent": 0, "skipped": 0, "errors": 0}

    booking_id = booking.get("Booking ID") or ""
    phone = booking.get("Phone") or ""
    appointment = parse_appointment_datetime_karachi(
        booking.get("Date"), booking.get("Time")
    )
    if not appointment:
        logger.warning(
            "Unparseable Date/Time for %s: %r %r",
            booking_id,
            booking.get("Date"),
            booking.get("Time"),
        )
        stats["skipped"] += 1
        return stats

    hours = _hours_until(appointment, now)
    if hours < 0:
        stats["skipped"] += 1
        return stats

    name = booking.get("Name") or "customer"

    # ── 24h window ──────────────────────────────────────────────────────────
    if _in_window(hours, WINDOW_24H) and not booking.get("reminder_24h_sent"):
        logger.info(
            "Arena 24h reminder candidate %s (%s) — %.2fh until pitch session",
            booking_id,
            name,
            hours,
        )
        if not enabled:
            logger.info(
                "DRY-RUN: would send %s to %s (REMINDERS_ENABLED is off)",
                TEMPLATE_24H,
                phone,
            )
            stats["dry_24h"] += 1
        else:
            result = send_whatsapp_message(
                phone,
                TEMPLATE_24H,
                body_params=[name, str(booking.get("Date") or ""), str(booking.get("Time") or "")],
            )
            if result.get("ok"):
                _mark_sent(supabase, booking_id, "reminder_24h_sent")
                stats["sent_24h"] += 1
                logger.info("Arena 24h reminder sent for %s", booking_id)
            else:
                stats["errors"] += 1
                logger.error(
                    "Failed arena 24h reminder for %s: %s",
                    booking_id,
                    result.get("error"),
                )

    # ── Urgent window ───────────────────────────────────────────────────────
    if _in_window(hours, WINDOW_URGENT) and not booking.get("reminder_urgent_sent"):
        logger.info(
            "Arena urgent reminder candidate %s (%s) — %.2fh until pitch session",
            booking_id,
            name,
            hours,
        )
        if not enabled:
            logger.info(
                "DRY-RUN: would send %s to %s (REMINDERS_ENABLED is off)",
                TEMPLATE_URGENT,
                phone,
            )
            stats["dry_urgent"] += 1
        else:
            result = send_whatsapp_message(
                phone,
                TEMPLATE_URGENT,
                body_params=[name, str(booking.get("Date") or ""), str(booking.get("Time") or "")],
            )
            if result.get("ok"):
                _mark_sent(supabase, booking_id, "reminder_urgent_sent")
                stats["sent_urgent"] += 1
                logger.info("Arena urgent reminder sent for %s", booking_id)
            else:
                stats["errors"] += 1
                logger.error(
                    "Failed arena urgent reminder for %s: %s",
                    booking_id,
                    result.get("error"),
                )

    return stats


def run() -> int:
    """
    Run one reminder sweep. Returns process exit code (0 = success).
    Never leaves background threads running.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Missing SUPABASE_URL / SUPABASE_KEY")
        return 1

    now = datetime.now(KARACHI)
    logger.info(
        "Reminder job start | now=%s | REMINDERS_ENABLED=%s",
        now.isoformat(),
        REMINDERS_ENABLED,
    )
    if not REMINDERS_ENABLED:
        logger.warning(
            "Live sends disabled. Set REMINDERS_ENABLED=true after Meta "
            "templates %r and %r show Approved.",
            TEMPLATE_24H,
            TEMPLATE_URGENT,
        )

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    try:
        bookings = _fetch_confirmed(supabase)
    except Exception as e:
        logger.error("Failed to fetch Confirmed bookings: %s", e)
        return 1

    logger.info("Loaded %d Confirmed booking(s)", len(bookings))

    totals = {
        "sent_24h": 0,
        "sent_urgent": 0,
        "dry_24h": 0,
        "dry_urgent": 0,
        "skipped": 0,
        "errors": 0,
    }
    for booking in bookings:
        try:
            part = _process_booking(
                supabase, booking, now, enabled=REMINDERS_ENABLED
            )
            for k, v in part.items():
                totals[k] = totals.get(k, 0) + v
        except Exception as e:
            totals["errors"] += 1
            logger.exception(
                "Unexpected error on booking %s: %s",
                booking.get("Booking ID"),
                e,
            )

    logger.info("Reminder job done | %s", totals)
    # Exit 0 even with per-booking send errors so cron isn't stuck; logs capture failures.
    return 0


def main() -> None:
    code = run()
    sys.exit(code)


if __name__ == "__main__":
    main()
