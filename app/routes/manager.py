"""Owner-only API for deterministic Manager findings."""
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_owner
from app.manager.briefing import generate_briefing
from app.manager.run import normalize_business_id, run_detectors

router = APIRouter()


class ManagerBriefingRequest(BaseModel):
    """Owner request to refresh or retrieve a deterministic briefing."""

    business_id: str
    refresh: bool = False


@router.get("/findings")
def get_manager_findings(
    business_id: str = Query(..., min_length=1),
    _owner: str = Depends(require_owner),
) -> dict[str, Any]:
    """Refresh and return findings for the authenticated owner's business."""
    try:
        normalized_id = normalize_business_id(business_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Business not found") from exc
    return run_detectors(normalized_id)


@router.post("/briefing")
def post_manager_briefing(
    request: ManagerBriefingRequest,
    _owner: str = Depends(require_owner),
) -> dict[str, Any]:
    """Refresh findings and return a validated, cached owner briefing."""
    try:
        normalized_id = normalize_business_id(request.business_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Business not found") from exc

    result = run_detectors(normalized_id)
    from app.routes.chat import GROQ_MODEL, groq_client

    briefing = generate_briefing(
        result["snapshot"],
        result["findings"],
        groq_client,
        GROQ_MODEL,
        refresh=request.refresh,
    )
    return {
        **briefing,
        "business_state": result["business_state"],
        "snapshot": result["snapshot"],
        "findings": result["findings"],
    }
