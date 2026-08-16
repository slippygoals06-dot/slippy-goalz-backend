"""Safe client-facing errors — never leak SQL, keys, or stack traces."""
from __future__ import annotations

from fastapi import HTTPException

INTERNAL_ERROR = "Something went wrong. Please try again."


def http_500(exc: BaseException | None = None) -> HTTPException:
    if exc is not None:
        print(f"internal error: {type(exc).__name__}: {exc}")
    return HTTPException(status_code=500, detail=INTERNAL_ERROR)
