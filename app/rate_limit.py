"""In-process sliding-window rate limiter (same pattern as chat.py)."""
from __future__ import annotations

import time
from typing import Dict, List

from fastapi import HTTPException, Request


def client_ip(request: Request) -> str:
    """Best-effort client IP (honours X-Forwarded-For behind Railway proxy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


class SlidingWindowRateLimiter:
    """Simple in-memory sliding window keyed by an arbitrary string (IP, session, …)."""

    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._log: Dict[str, List[float]] = {}

    def is_limited(self, key: str) -> bool:
        now = time.time()
        timestamps = self._log.get(key, [])
        timestamps = [t for t in timestamps if now - t < self.window_seconds]
        timestamps.append(now)
        self._log[key] = timestamps
        return len(timestamps) > self.max_requests

    def check_or_raise(
        self,
        key: str,
        detail: str = "Too many attempts, try again later",
    ) -> None:
        if self.is_limited(key):
            raise HTTPException(status_code=429, detail=detail)
