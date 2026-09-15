"""Tenant isolation tests.

PHASE2_SPEC Section 1 Rule 6 & Section 38:
Prove:
  - Client A cannot access Client B assessment
  - Client A cannot access Client B evidence
  - Client A cannot access Client B finding
  - Client A cannot access Client B integration (Connection)
  - Client A cannot access Client B report
  - Client A cannot access Client B audit log
Enforced at the data/query layer, not only in frontend logic.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import HTTPException

from app.auth.dependencies import AuthContext
from app.db.models.assessment import Assessment
from app.db.models.audit_log import AuditLog
from app.db.models.connection import Connection
from app.db.models.evidence import Evidence
from app.db.models.finding import Finding
from app.db.repositories.base import TenantIsolatedRepository
from app.services.report_generator import ReportGeneratorService


@pytest.fixture
def client_a_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def client_b_id() -> uuid.UUID:
    return uuid.uuid4()


def test_auth_context_blocks_cross_client_access(client_a_id: uuid.UUID, client_b_id: uuid.UUID):
    """Prove AuthContext raises 403 when Client A user attempts to access Client B."""
    auth_a = AuthContext(
        user_id=uuid.uuid4(),
        identity="user@client-a.com",
        client_roles={str(client_a_id): "admin"},
    )

    # Accessing own client succeeds
    auth_a.require_client_access(client_a_id)

    # Accessing another client raises 403 Forbidden
    with pytest.raises(HTTPException) as exc_info:
        auth_a.require_client_access(client_b_id)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_repository_tenant_isolation_assessment(client_a_id: uuid.UUID, client_b_id: uuid.UUID):
    """Prove Client A repository filters strictly by Client A ID and cannot retrieve Client B assessment."""
    session = AsyncMock()
    repo_a = TenantIsolatedRepository(Assessment, session, client_a_id)

    query = repo_a._base_query()
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert client_a_id.hex in compiled_query
    assert client_b_id.hex not in compiled_query


@pytest.mark.asyncio
async def test_repository_tenant_isolation_evidence(client_a_id: uuid.UUID, client_b_id: uuid.UUID):
    """Prove Client A repository cannot query Client B evidence."""
    session = AsyncMock()
    repo_a = TenantIsolatedRepository(Evidence, session, client_a_id)

    query = repo_a._base_query()
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert client_a_id.hex in compiled_query
    assert client_b_id.hex not in compiled_query


@pytest.mark.asyncio
async def test_repository_tenant_isolation_finding(client_a_id: uuid.UUID, client_b_id: uuid.UUID):
    """Prove Client A repository cannot query Client B findings."""
    session = AsyncMock()
    repo_a = TenantIsolatedRepository(Finding, session, client_a_id)

    query = repo_a._base_query()
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert client_a_id.hex in compiled_query
    assert client_b_id.hex not in compiled_query


@pytest.mark.asyncio
async def test_repository_tenant_isolation_connection(client_a_id: uuid.UUID, client_b_id: uuid.UUID):
    """Prove Client A repository cannot query Client B connections (integrations)."""
    session = AsyncMock()
    repo_a = TenantIsolatedRepository(Connection, session, client_a_id)

    query = repo_a._base_query()
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert client_a_id.hex in compiled_query
    assert client_b_id.hex not in compiled_query


@pytest.mark.asyncio
async def test_repository_tenant_isolation_audit_log(client_a_id: uuid.UUID, client_b_id: uuid.UUID):
    """Prove Client A repository cannot query Client B audit logs."""
    session = AsyncMock()
    repo_a = TenantIsolatedRepository(AuditLog, session, client_a_id)

    query = repo_a._base_query()
    compiled_query = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert client_a_id.hex in compiled_query
    assert client_b_id.hex not in compiled_query


@pytest.mark.asyncio
async def test_report_service_blocks_cross_client_report_generation(
    client_a_id: uuid.UUID, client_b_id: uuid.UUID
):
    """Prove ReportGeneratorService rejects generating or viewing reports across clients."""
    session = AsyncMock()
    assessment_id = uuid.uuid4()

    # Assessment belongs to Client B
    mock_assessment = MagicMock()
    mock_assessment.id = assessment_id
    mock_assessment.client_id = client_b_id
    session.get.return_value = mock_assessment

    # Service runs in Client A context
    report_service = ReportGeneratorService(session, client_a_id)

    with pytest.raises(ValueError) as exc_info:
        await report_service.generate_report(assessment_id)
    assert "Assessment not found or inaccessible" in str(exc_info.value)
