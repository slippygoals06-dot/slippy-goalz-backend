"""Rate limiting — prefers shared Postgres RPC, falls back to in-process memory."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from fastapi import HTTPException, Request
from supabase import create_client

from app.config import SUPABASE_URL, SUPABASE_KEY

_supabase = None


def _db():
    global _supabase
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase


def client_ip(request: Request) -> str:
    """Client IP behind a single trusted proxy (Railway appends the real hop)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            return parts[-1]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SlidingWindowRateLimiter:
    """
    Sliding / fixed-window limiter.
    Uses Postgres `rate_limit_hit` when migration 022 is applied (multi-instance safe).
    Falls back to in-memory if the RPC is missing.
    """

    def __init__(self, max_requests: int, window_seconds: int, prefix: str = "rl"):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.prefix = prefix
        self._log: Dict[str, List[float]] = {}
        self._rpc_ok: Optional[bool] = None

    def _memory_limited(self, key: str) -> bool:
        now = time.time()
        timestamps = self._log.get(key, [])
        timestamps = [t for t in timestamps if now - t < self.window_seconds]
        timestamps.append(now)
        self._log[key] = timestamps
        return len(timestamps) > self.max_requests

    def is_limited(self, key: str) -> bool:
        full_key = f"{self.prefix}:{key}"
        if self._rpc_ok is not False:
            try:
                res = _db().rpc(
                    "rate_limit_hit",
                    {
                        "p_key": full_key,
                        "p_window_seconds": int(self.window_seconds),
                        "p_max": int(self.max_requests),
                    },
                ).execute()
                self._rpc_ok = True
                # RPC returns true when OVER the limit
                data = res.data
                if isinstance(data, bool):
                    return data
                if isinstance(data, list) and data:
                    return bool(data[0])
                return bool(data)
            except Exception:
                self._rpc_ok = False
        return self._memory_limited(full_key)

    def check_or_raise(
        self,
        key: str,
        detail: str = "Too many attempts, try again later",
    ) -> None:
        if self.is_limited(key):
            raise HTTPException(status_code=429, detail=detail)
