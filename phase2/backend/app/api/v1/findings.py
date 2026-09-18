import uuid
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthContext, get_current_user
from app.db.session import get_db_session
from app.db.models.finding import Finding, FindingStatus
from app.db.models.evidence import Verdict
from app.db.models.human_override import HumanOverride

router = APIRouter(prefix="/findings", tags=["Findings"])


class FindingOverrideRequest(BaseModel):
    new_verdict: Verdict
    reason: str


@router.post("/{finding_id}/override", status_code=status.HTTP_200_OK)
async def override_finding_verdict(
    finding_id: uuid.UUID,
    data: FindingOverrideRequest,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Submit a human review override for a finding."""
    if not data.reason or not data.reason.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A reason is required to override a finding verdict"
        )
        
    result = await session.execute(
        select(Finding).where(
            Finding.id == finding_id,
            Finding.client_id.in_([uuid.UUID(c) for c in auth.get_accessible_client_ids()])
        )
    )
    finding = result.scalar_one_or_none()
    
    if not finding:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Finding not found"
        )
        
    if finding.verdict == data.new_verdict:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New verdict is the same as the current verdict"
        )
        
    # Create the override record
    override = HumanOverride(
        finding_id=finding.id,
        previous_verdict=finding.verdict.value,
        new_verdict=data.new_verdict.value,
        reason=data.reason.strip(),
        user_id=auth.user_id
    )
    session.add(override)
    
    # Update the finding itself
    finding.verdict = data.new_verdict
    finding.status = FindingStatus.OVERRIDDEN
    # We leave human_review_required as True or False based on what it was,
    # because the status OVERRIDDEN tells us it was handled.
    
    await session.commit()
    
    return {
        "status": "success",
        "finding_id": str(finding.id),
        "new_verdict": finding.verdict.value,
        "finding_status": finding.status.value
    }
