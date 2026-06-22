from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..schemas import EraseRequest, EraseResult, ErasureCertificate
from ..memory_service import erase_subject as _erase_subject, get_erasure_certificate
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


@router.get("/erase/{subject_id}/certificate", response_model=ErasureCertificate)
async def erasure_certificate(
    subject_id: str,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Retrieve a cryptographic proof-of-erasure certificate for a data subject.

    The certificate proves:
    - N memories had their encrypted content permanently destroyed (DEK shredded).
    - The SHA-256 content_hashes are preserved — the erasure is auditable but
      the content is cryptographically unrecoverable.
    - The audit chain remains intact after the erasure (SEC 17a-4 compliant).
    - A unique `certificate_id` is generated for filing with a supervisory
      authority (e.g., GDPR Art. 17 response, CCPA deletion acknowledgement).

    This is the "erasure that proves itself" story: compliance officers buy
    proofs, not promises.

    Returns 404 if no erasure has been recorded for this subject.
    """
    auth.require("admin")
    cert = await get_erasure_certificate(db, auth.namespace, subject_id)
    if not cert:
        raise HTTPException(status_code=404, detail=f"No erasure record found for subject '{subject_id}'")
    return ErasureCertificate(**cert)
