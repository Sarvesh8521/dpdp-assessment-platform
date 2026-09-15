"""Audit Logs API endpoints."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth.dependencies import AuthContext, get_current_user
from app.db.session import get_db_session
from app.db.models.audit_log import AuditLog

router = APIRouter(prefix="/audit-logs", tags=["audit_logs"])

@router.get("")
async def list_audit_logs(
    client_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
    limit: int = 50,
    offset: int = 0
) -> Any:
    """List audit logs (append-only API log)."""
    auth.require_client_access(client_id)
    
    query = select(AuditLog).where(
        AuditLog.client_id == client_id
    ).order_by(AuditLog.timestamp.desc()).limit(limit).offset(offset)
    
    result = await session.execute(query)
    logs = result.scalars().all()
    
    return logs
