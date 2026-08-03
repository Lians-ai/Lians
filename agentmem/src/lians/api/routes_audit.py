from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import AuditReconstructionCapacityExceeded, reconstruct
from ..config import get_settings
from ..db import get_db
from ..schemas import AuditReconstructResult
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/reconstruct", response_model=AuditReconstructResult)
async def audit_reconstruct(
    agent_id: str,
    as_of: datetime,
    query: Optional[str] = Query(default=None),
    k: int = Query(default=20, ge=1, le=100),
    memory_limit: int = Query(default=1000, ge=1, le=10_000),
    event_limit: int = Query(default=5000, ge=1, le=10_000),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    try:
        return await reconstruct(
            db,
            auth.namespace,
            agent_id,
            as_of,
            query,
            k,
            barrier_override=auth.barrier_group,
            memory_limit=memory_limit,
            event_limit=event_limit,
            max_response_bytes=get_settings().content_export_page_bytes_limit,
        )
    except AuditReconstructionCapacityExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": exc.code,
                "message": exc.public_message,
                "estimated_bytes": exc.estimated_bytes,
                "byte_limit": exc.byte_limit,
                "memory_estimated_bytes": exc.memory_estimated_bytes,
                "event_estimated_bytes": exc.event_estimated_bytes,
            },
        ) from exc
