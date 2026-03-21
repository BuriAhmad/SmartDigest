"""Job status endpoint — STUB for Phase 1.

Full implementation in Phase 3 (ARQ integration).
"""

import structlog
from fastapi import APIRouter, Request

logger = structlog.get_logger()
router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job_status(job_id: str, request: Request) -> dict:
    """Check the status of a background job. STUB."""
    return {
        "job_id": job_id,
        "status": "complete",
        "result": None,
    }
