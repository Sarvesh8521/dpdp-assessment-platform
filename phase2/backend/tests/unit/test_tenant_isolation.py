import pytest
import uuid
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.assessments import get_assessment
from app.auth.dependencies import AuthContext
from app.db.models.assessment import Assessment


@pytest.mark.asyncio
async def test_get_assessment_blocks_cross_tenant():
    """Verify that get_assessment returns 404 if the assessment does not belong to the user's tenants."""
    mock_session = AsyncMock(spec=AsyncSession)
    
    # Mock AuthContext with access to client_A only
    client_a = uuid.uuid4()
    client_b = uuid.uuid4()
    
    auth_context = AuthContext(
        user_id=uuid.uuid4(),
        identity="test@example.com",
        client_roles={str(client_a): "admin"}
    )
    
    # Simulate an assessment that belongs to client_b
    assessment_id = uuid.uuid4()
    
    # We mock the execute to return nothing, simulating the .where() clause filtering it out
    # because the query enforces client_id in [client_a] while the assessment is client_b.
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    
    with pytest.raises(HTTPException) as exc_info:
        await get_assessment(
            assessment_id=assessment_id,
            auth=auth_context,
            session=mock_session
        )
        
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Assessment not found"
    
    # Verify the query was executed (which proves our mock setup was hit)
    mock_session.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_assessment_allows_same_tenant():
    """Verify that get_assessment allows access if the assessment belongs to the user's tenants."""
    mock_session = AsyncMock(spec=AsyncSession)
    
    client_a = uuid.uuid4()
    auth_context = AuthContext(
        user_id=uuid.uuid4(),
        identity="test@example.com",
        client_roles={str(client_a): "admin"}
    )
    
    assessment_id = uuid.uuid4()
    mock_assessment = Assessment(id=assessment_id, client_id=client_a)
    
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_assessment
    mock_session.execute.return_value = mock_result
    
    result = await get_assessment(
        assessment_id=assessment_id,
        auth=auth_context,
        session=mock_session
    )
    
    assert result == mock_assessment
    mock_session.execute.assert_called_once()
