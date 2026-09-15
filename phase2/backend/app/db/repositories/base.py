"""Repository base with tenant isolation.

PHASE2_SPEC Section 1 Rule 6:
  'Per-client isolation. No query can ever return data across clients.
   Enforce at the data layer, not in application logic.'
"""

from __future__ import annotations

import uuid
from typing import Generic, Sequence, Type, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.base import Base

logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=Base)


class TenantIsolatedRepository(Generic[ModelT]):
    """Base repository enforcing per-client tenant isolation.

    Every query method automatically filters by client_id.
    No method can return data across clients.
    """

    def __init__(
        self,
        model: Type[ModelT],
        session: AsyncSession,
        client_id: uuid.UUID,
    ) -> None:
        self._model = model
        self._session = session
        self._client_id = client_id

    def _base_query(self) -> Select:
        """Create a base query with tenant isolation enforced."""
        return select(self._model).where(
            self._model.client_id == self._client_id  # type: ignore
        )

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelT | None:
        """Get entity by ID, scoped to current client."""
        result = await self._session.execute(
            self._base_query().where(
                self._model.id == entity_id  # type: ignore
            )
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
        order_by: str | None = None,
        order_desc: bool = False,
    ) -> Sequence[ModelT]:
        """List all entities for this client with pagination."""
        query = self._base_query()

        if order_by and hasattr(self._model, order_by):
            col = getattr(self._model, order_by)
            query = query.order_by(col.desc() if order_desc else col.asc())
        elif hasattr(self._model, "created_at"):
            query = query.order_by(self._model.created_at.desc())  # type: ignore

        query = query.offset(offset).limit(limit)
        result = await self._session.execute(query)
        return result.scalars().all()

    async def count(self) -> int:
        """Count entities for this client."""
        query = select(func.count()).select_from(self._model).where(
            self._model.client_id == self._client_id  # type: ignore
        )
        result = await self._session.execute(query)
        return result.scalar_one()

    async def create(self, entity: ModelT) -> ModelT:
        """Create a new entity, enforcing client_id."""
        # Enforce client_id regardless of what was passed
        entity.client_id = self._client_id  # type: ignore
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def update(self, entity: ModelT) -> ModelT:
        """Update an entity, verifying tenant isolation."""
        if entity.client_id != self._client_id:  # type: ignore
            raise PermissionError("Cannot modify entity belonging to another client")
        self._session.add(entity)
        await self._session.flush()
        return entity

    async def delete(self, entity_id: uuid.UUID) -> bool:
        """Delete an entity by ID, scoped to current client."""
        entity = await self.get_by_id(entity_id)
        if entity is None:
            return False
        await self._session.delete(entity)
        await self._session.flush()
        return True


class GlobalRepository(Generic[ModelT]):
    """Repository for non-tenant-scoped entities (e.g., obligations, users)."""

    def __init__(self, model: Type[ModelT], session: AsyncSession) -> None:
        self._model = model
        self._session = session

    async def get_by_id(self, entity_id: uuid.UUID) -> ModelT | None:
        result = await self._session.execute(
            select(self._model).where(self._model.id == entity_id)  # type: ignore
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> Sequence[ModelT]:
        query = select(self._model).offset(offset).limit(limit)
        if hasattr(self._model, "created_at"):
            query = query.order_by(self._model.created_at.desc())  # type: ignore
        result = await self._session.execute(query)
        return result.scalars().all()

    async def count(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(self._model)
        )
        return result.scalar_one()

    async def create(self, entity: ModelT) -> ModelT:
        self._session.add(entity)
        await self._session.flush()
        return entity
