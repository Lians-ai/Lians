from fastapi import APIRouter, Depends, Query
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import AuditReconstructResult
from ..audit import reconstruct
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1/audit", tags=["audit"])


@router.get("/reconstruct", response_model=AuditReconstructResult)
async def audit_reconstruct(
    agent_id: str,
    as_of: datetime,
    query: Optional[str] = Query(default=None),
    k: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await reconstruct(db, auth.namespace, agent_id, as_of, query, k)
