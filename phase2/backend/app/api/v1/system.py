from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthContext, get_current_user
from app.db.session import get_db_session
from app.services.retention_service import RetentionService

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/retention/apply", response_model=dict[str, Any])
async def apply_retention(
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Apply the data retention policy.
    
    Purges all expired assessments and their related artifacts
    (evidence, findings, reports) based on the configured retention period.
    Generates audit logs for the deletion.
    
    Requires system admin access (in a real app, we'd check roles here).
    """
    # Note: In a production app, we would add `@require_admin` here.
    
    service = RetentionService(session)
    stats = await service.apply_retention_policy()
    
    return {
        "status": "success",
        "deleted": stats
    }
