import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.findings import override_finding_verdict, FindingOverrideRequest
from app.auth.dependencies import AuthContext
from app.db.models.finding import Finding, FindingStatus, FindingSeverity
from app.db.models.evidence import Confidence, Verdict


@pytest.fixture
def mock_auth():
    client_id = uuid.uuid4()
    return AuthContext(
        user_id=uuid.uuid4(),
        identity="reviewer@example.com",
        client_roles={str(client_id): "admin"}
    ), client_id


@pytest.mark.asyncio
async def test_human_override_success(mock_auth):
    """Verify successful finding override."""
    auth_ctx, client_id = mock_auth
    mock_session = AsyncMock(spec=AsyncSession)
    
    finding = Finding(
        id=uuid.uuid4(),
        client_id=client_id,
        verdict=Verdict.CANNOT_DETERMINE,
        status=FindingStatus.OPEN
    )
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = finding
    mock_session.execute.return_value = mock_result
    
    req = FindingOverrideRequest(
        new_verdict=Verdict.NOT_OBSERVED,
        reason="Reviewed externally."
    )
    
    resp = await override_finding_verdict(
        finding_id=finding.id,
        data=req,
        auth=auth_ctx,
        session=mock_session
    )
    
    assert resp["status"] == "success"
    assert resp["new_verdict"] == Verdict.NOT_OBSERVED.value
    assert finding.verdict == Verdict.NOT_OBSERVED
    assert finding.status == FindingStatus.OVERRIDDEN
    
    # Assert HumanOverride was added
    mock_session.add.assert_called_once()
    override = mock_session.add.call_args[0][0]
    assert override.finding_id == finding.id
    assert override.reason == "Reviewed externally."
    assert override.previous_verdict == Verdict.CANNOT_DETERMINE.value
    assert override.new_verdict == Verdict.NOT_OBSERVED.value


@pytest.mark.asyncio
async def test_human_override_missing_reason(mock_auth):
    """Verify empty reason fails validation."""
    auth_ctx, _ = mock_auth
    mock_session = AsyncMock()
    
    req = FindingOverrideRequest(
        new_verdict=Verdict.NOT_OBSERVED,
        reason="   "
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await override_finding_verdict(
            finding_id=uuid.uuid4(),
            data=req,
            auth=auth_ctx,
            session=mock_session
        )
        
    assert exc_info.value.status_code == 400
    assert "reason is required" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_human_override_same_verdict(mock_auth):
    """Verify overriding to the same verdict fails."""
    auth_ctx, client_id = mock_auth
    mock_session = AsyncMock(spec=AsyncSession)
    
    finding = Finding(
        id=uuid.uuid4(),
        client_id=client_id,
        verdict=Verdict.OBSERVED,
        status=FindingStatus.OPEN
    )
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = finding
    mock_session.execute.return_value = mock_result
    
    req = FindingOverrideRequest(
        new_verdict=Verdict.OBSERVED,
        reason="Nothing to change."
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await override_finding_verdict(
            finding_id=finding.id,
            data=req,
            auth=auth_ctx,
            session=mock_session
        )
        
    assert exc_info.value.status_code == 400
    assert "same as the current verdict" in str(exc_info.value.detail).lower()


@pytest.mark.asyncio
async def test_human_override_tenant_isolation(mock_auth):
    """Verify users cannot override findings from tenants they do not have access to."""
    auth_ctx, _ = mock_auth
    mock_session = AsyncMock(spec=AsyncSession)
    
    # Simulate DB returning None for the finding because it is filtered by client_id.in_()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    
    req = FindingOverrideRequest(
        new_verdict=Verdict.NOT_OBSERVED,
        reason="Overriding another tenant."
    )
    
    with pytest.raises(HTTPException) as exc_info:
        await override_finding_verdict(
            finding_id=uuid.uuid4(),
            data=req,
            auth=auth_ctx,
            session=mock_session
        )
        
    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value.detail).lower()
