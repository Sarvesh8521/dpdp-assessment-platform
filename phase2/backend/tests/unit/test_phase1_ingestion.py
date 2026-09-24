import pytest
import uuid
from datetime import datetime, timezone

from app.schemas.api import AssessmentCreate, AssessmentConfig, DeclaredScope
from app.domain.evidence import EvidenceItem, Verdict, Confidence, StructuredEvidence
from app.services.assessment_service import AssessmentService
from app.db.models.assessment import Assessment
from app.db.models.evidence import Evidence
from app.db.models.client import Client
from unittest.mock import AsyncMock

@pytest.fixture
def mock_client():
    return Client(id=uuid.uuid4())

@pytest.fixture
def mock_session(mock_client):
    session = AsyncMock()
    session.flush = AsyncMock()
    
    class MockResult:
        def scalars(self):
            class MockScalars:
                def all(self):
                    return []
            return MockScalars()
        def scalar_one_or_none(self):
            return None
        def scalar(self):
            return None
            
    async def mock_execute(*args, **kwargs):
        return MockResult()
        
    session.execute = mock_execute
    
    async def mock_get(model, id):
        if model == Client:
            return Client(id=id)
        if model == Assessment:
            return Assessment(id=id, client_id=mock_client.id)
        return None
    session.get = mock_get
    return session

@pytest.mark.asyncio
async def test_phase1_ingestion_valid_input(mock_session, mock_client):
    """Test that valid Phase 1 results provided in AssessmentConfig are ingested correctly."""
    # Build a Phase 1 result list
    p1_results = [
        {
            "obligation_id": "S12",
            "verdict": Verdict.OBSERVED,
            "confidence": Confidence.HIGH,
            "source": "phase1_scanner:tags",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "detail": "Found marketing tags via Phase 1 network scan",
            "raw_metadata": {"tags": [{"type": "meta"}]},
            "structured_detail": {
                "source": "phase1_scanner:tags",
                "integration": "phase1_scanner",
                "operation": "scan_network",
                "resource_type": "website",
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "detail": "Found marketing tags via Phase 1 network scan",
                "location": "https://example.com"
            }
        }
    ]
    
    config = AssessmentConfig(
        integrations=["phase1_scanner"],
        scope=DeclaredScope(),
        phase1_results=p1_results
    )
    
    data = AssessmentCreate(
        client_id=mock_client.id,
        config=config
    )
    
    service = AssessmentService(mock_session, mock_client.id)
    user_id = uuid.uuid4()
    
    # Mock _ensure_obligations_seeded to return {"S12": uuid.uuid4()}
    service._ensure_obligations_seeded = AsyncMock(return_value={uuid.uuid4(): "S12"})
    
    # create_assessment should ingest phase1_results and store them as Evidence
    assessment = await service.create_assessment(data, user_id)
    assert assessment is not None
    
    # Check that Evidence objects were appended to mock_session
    # In create_assessment, all_db_evidence is iterated and session.add is called for each
    added_objects = [call_args[0][0] for call_args in mock_session.add.call_args_list]
    ev_rows = [obj for obj in added_objects if isinstance(obj, Evidence) and obj.source == "phase1_scanner:tags"]
    
    assert len(ev_rows) == 1
    ev = ev_rows[0]
    assert ev.verdict == Verdict.OBSERVED
    assert ev.confidence == Confidence.HIGH
    assert ev.detail == "Found marketing tags via Phase 1 network scan"
    assert "tags" in ev.sanitized_metadata
    assert ev.structured_detail["operation"] == "scan_network"


@pytest.mark.asyncio
async def test_phase1_ingestion_unavailable_input(mock_session, mock_client):
    """Test that if Phase 1 data is unavailable, it is exposed as a missing source without fabricating evidence."""
    config = AssessmentConfig(
        integrations=["phase1_scanner"],
        scope=DeclaredScope(),
        phase1_results=None  # Missing Phase 1 data
    )
    
    data = AssessmentCreate(
        client_id=mock_client.id,
        config=config
    )
    
    service = AssessmentService(mock_session, mock_client.id)
    user_id = uuid.uuid4()
    
    service._ensure_obligations_seeded = AsyncMock(return_value={uuid.uuid4(): "S12"})
    
    assessment = await service.create_assessment(data, user_id)
    assert assessment is not None
    
    # Check that Evidence objects for phase1_scanner were NOT appended to mock_session
    if mock_session.add.called:
        added_objects = [call_args[0][0] for call_args in mock_session.add.call_args_list]
        ev_rows = [obj for obj in added_objects if isinstance(obj, Evidence) and obj.source and "phase1_scanner" in obj.source]
        assert len(ev_rows) == 0
