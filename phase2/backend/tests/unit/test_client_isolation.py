import pytest
import uuid
from fastapi import status
from app.db.models.assessment import Assessment
from app.db.models.client_user import UserRole
from app.auth.dependencies import AuthContext

@pytest.fixture
def other_client_id():
    return uuid.uuid4()

@pytest.fixture
def my_client_id():
    return uuid.uuid4()

@pytest.fixture
def mock_auth_context(my_client_id):
    return AuthContext(
        user_id=uuid.uuid4(),
        identity="user@example.com",
        client_roles={str(my_client_id): UserRole.ASSESSOR},
        is_platform_admin=False
    )

@pytest.mark.asyncio
async def test_cannot_access_other_client_assessment(mocker, mock_auth_context, other_client_id):
    from app.api.v1.assessments import get_assessment
    
    mock_session = mocker.AsyncMock()
    # Mocking the session to return None because apply_client_isolation filters it out
    mock_result = mocker.MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_result
    
    try:
        await get_assessment(
            assessment_id=uuid.uuid4(),
            auth=mock_auth_context,
            session=mock_session
        )
    except Exception as e:
        assert e.status_code == status.HTTP_404_NOT_FOUND

@pytest.mark.asyncio
async def test_cannot_list_other_client_resources(mocker, mock_auth_context, other_client_id):
    from app.api.v1.integrations import list_resources
    
    mock_session = mocker.AsyncMock()
    
    try:
        await list_resources(
            provider="cmp",
            client_id=other_client_id,
            auth=mock_auth_context,
            session=mock_session
        )
    except Exception as e:
        assert e.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.asyncio
async def test_cannot_get_other_client_integration_status(mocker, mock_auth_context, other_client_id):
    from app.api.v1.integrations import integration_status
    
    mock_session = mocker.AsyncMock()
    
    try:
        await integration_status(
            client_id=other_client_id,
            auth=mock_auth_context,
            session=mock_session
        )
    except Exception as e:
        assert e.status_code == status.HTTP_403_FORBIDDEN

@pytest.mark.asyncio
async def test_cannot_get_other_client_audit_logs(mocker, mock_auth_context, other_client_id):
    from app.api.v1.audit_logs import list_audit_logs
    
    mock_session = mocker.AsyncMock()
    
    try:
        await list_audit_logs(
            client_id=other_client_id,
            auth=mock_auth_context,
            session=mock_session
        )
    except Exception as e:
        assert e.status_code == status.HTTP_403_FORBIDDEN

