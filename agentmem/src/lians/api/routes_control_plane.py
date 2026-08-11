"""Enterprise control-plane summary routes."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..control_plane import control_plane_overview
from ..db import get_db
from ..schemas import ControlPlaneOverview
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1/control-plane", tags=["control-plane"])


@router.get("/overview", response_model=ControlPlaneOverview)
async def overview(
    verify_audit: bool = Query(default=False),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    auth.require_unbarriered()
    return await control_plane_overview(
        db,
        auth.namespace,
        verify_audit=verify_audit,
    )
