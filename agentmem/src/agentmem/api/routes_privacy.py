from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import EraseRequest, EraseResult
from ..memory_service import erase_subject as _erase_subject
from .deps import get_auth, AuthContext

router = APIRouter(prefix="/v1", tags=["privacy"])


@router.post("/erase", response_model=EraseResult)
async def erase_subject(
    req: EraseRequest,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    count = await _erase_subject(db, auth.namespace, req.subject_id, req.request_ref)
    return EraseResult(
        subject_id=req.subject_id,
        memories_erased=count,
        request_ref=req.request_ref,
    )
