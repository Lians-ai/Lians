"""Versioned memory policy profiles and per-agent assignments."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..policy_profiles import get_agent_policy, list_policy_profiles, set_agent_policy
from ..schemas import AgentPolicyOut, AgentPolicyUpdate, PolicyProfileList
from .deps import AuthContext, get_auth

router = APIRouter(prefix="/v1", tags=["policies"])


@router.get("/policy-profiles", response_model=PolicyProfileList)
async def policy_profiles(auth: AuthContext = Depends(get_auth)):
    auth.require("read")
    profiles = list_policy_profiles()
    return PolicyProfileList(profiles=profiles, total=len(profiles))


@router.get("/agents/{agent_id}/policy", response_model=AgentPolicyOut)
async def agent_policy(
    agent_id: str,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("read")
    return await get_agent_policy(db, auth.namespace, agent_id)


@router.put("/agents/{agent_id}/policy", response_model=AgentPolicyOut)
async def update_agent_policy(
    agent_id: str,
    req: AgentPolicyUpdate,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    auth.require("admin")
    try:
        return await set_agent_policy(db, auth.namespace, agent_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
