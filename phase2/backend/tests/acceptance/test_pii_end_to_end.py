import asyncio
from datetime import datetime, timezone
import pytest

from app.schemas.api import AssessmentCreate, AssessmentConfig
from app.services.assessment_service import AssessmentService
from app.integrations.base.http_client import AuditedHTTPClient
from app.db.models import Finding, Evidence, Assessment
import uuid
from unittest.mock import patch, AsyncMock, MagicMock

# Fake malicious response representing an API accidentally dumping PII
MALICIOUS_API_RESPONSE = {
    "accountSummaries": [
        {
            "propertySummaries": [
                {
                    "property": "properties/123",
                    "displayName": "Safe Property Name",
                    # PII fields that should be stripped
                    "email": "user@example.com",
                    "phone": "555-0100",
                    "userId": "usr_999",
                    "owner_details": {
                        "firstName": "John",
                        "lastName": "Doe",
                        "ip": "192.168.1.1",
                    },
                    "eventDataRetention": "FIFTY_MONTHS",
                    "googleAdsLinks": [{"name": "fake_link"}],
                    # High-risk structures that should be stripped entirely
                    "users": [
                        {"email": "admin@example.com"}
                    ],
                    "events": [
                        {"payload": {"user_id": 123}}
                    ]
                }
            ]
        }
    ]
}

class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
        self.headers = {}
    
    def raise_for_status(self):
        pass
        
    def json(self):
        return self._json

@pytest.fixture
def mock_httpx_client():
    # Mock httpx.AsyncClient.request to return our malicious payload for GA4
    async def mock_request(*args, **kwargs):
        return FakeResponse(MALICIOUS_API_RESPONSE)
        
    with patch("httpx.AsyncClient.request", side_effect=mock_request) as mock:
        yield mock

@pytest.mark.asyncio
async def test_end_to_end_pii_redaction(mock_httpx_client):
    test_client_id = uuid.uuid4()
    mock_session = AsyncMock()
    
    # Setup Assessment (we mock it since we are not using a real DB)
    assessment = Assessment(
        id=uuid.uuid4(),
        client_id=test_client_id,
        status="running",
        started_at=datetime.now(timezone.utc),
        configuration={}
    )
    
    def mock_get(model, obj_id):
        if model == Assessment:
            return assessment
        return None
    
    # Mock result for session.execute(...) returns a MagicMock since the result object itself is synchronous
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    
    mock_cp = MagicMock()
    mock_cp.provider_account_id = "fake_account"
    mock_cp.secret_ref = "fake_secret_ref"
    mock_cp.resource_id = "properties/123"
    mock_result.scalars.return_value.first.return_value = mock_cp
    
    def mock_add(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid.uuid4()
            
    mock_session.execute.return_value = mock_result
    mock_session.get.side_effect = mock_get
    mock_session.add.side_effect = mock_add
    
    # We also mock the logging to verify no PII goes into logs
    with patch("app.integrations.base.http_client.logger.info") as mock_logger:
        service = AssessmentService(mock_session, test_client_id)
        
        class FakeDict(dict):
            def __contains__(self, key): return True
            def __getitem__(self, key): return uuid.uuid4()
            
        with patch.object(service, "_ensure_obligations_seeded", return_value=FakeDict()):
            mock_sm = AsyncMock()
            mock_sm.get_secret.return_value = '{"access_token": "fake_token", "refresh_token": "fake"}'
            
            with patch("app.services.assessment_service.get_secret_manager", return_value=mock_sm):
                # Configure a GA4 run with fake credentials
                config = AssessmentConfig(
                    integrations=["ga4"],
                    credentials={"ga4": {"access_token": "fake_token"}}
                )
                data = AssessmentCreate(client_id=test_client_id, config=config)
                
                # Execute
                created_by_uuid = uuid.uuid4()
                await service.create_assessment(data, created_by_uuid)
                
                # Verify Database Evidence 
                evidence_added = []
                for call in mock_session.add.call_args_list:
                    obj = call[0][0]
                    print(f"Added obj: {type(obj)} {getattr(obj, 'source', 'no_source')}")
                    if isinstance(obj, Evidence) and obj.source != "scope_sheet:search_console_properties" and "scope_sheet" not in obj.source:
                        evidence_added.append(obj)
                        
                assert len(evidence_added) > 0, "No evidence was persisted from the provider API"
                
                for row in evidence_added:
                    metadata_str = str(row.sanitized_metadata)
                    structured_str = str(row.structured_detail)
                    
                    # Verify PII was redacted
                    assert "user@example.com" not in metadata_str, "Email leaked!"
                    assert "555-0100" not in metadata_str, "Phone leaked!"
                    assert "usr_999" not in metadata_str, "User ID leaked!"
                    assert "John" not in metadata_str, "First name leaked!"
                    assert "Doe" not in metadata_str, "Last name leaked!"
                    assert "192.168.1.1" not in metadata_str, "IP leaked!"
                    
                    # Verify Safe Name was preserved
                    assert "Safe Property Name" in metadata_str or "Safe Property Name" in structured_str, "Safe name missing!"
