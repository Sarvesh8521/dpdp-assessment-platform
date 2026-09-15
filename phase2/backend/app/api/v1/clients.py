"""Clients API endpoints."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.auth.dependencies import AuthContext, get_current_user
from app.db.session import get_db_session
from app.db.models.client import Client
from app.schemas.api import ClientResponse

router = APIRouter(prefix="/clients", tags=["clients"])


@router.get("", response_model=list[ClientResponse])
async def list_clients(
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """List accessible clients for the current user."""
    accessible_ids = auth.get_accessible_client_ids()
    
    if not accessible_ids and not auth.is_platform_admin:
        return []
        
    query = select(Client)
    if not auth.is_platform_admin:
        query = query.where(Client.id.in_([uuid.UUID(uid) for uid in accessible_ids]))
        
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{client_id}", response_model=ClientResponse)
async def get_client(
    client_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Get details for a specific client."""
    auth.require_client_access(client_id)
    
    client = await session.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
        
    return client
