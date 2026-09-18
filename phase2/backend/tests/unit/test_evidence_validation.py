import uuid
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from app.db.models.assessment import Assessment, AssessmentStatus
from app.db.models.client import Client
from app.db.models.evidence import Evidence, Verdict, Confidence
from app.db.models.finding import Finding
from app.db.models.obligation import Obligation, ObligationSeverity
from app.services.finding_service import FindingService


@pytest.fixture
def mock_session():
    session = AsyncMock()
    return session


def _create_mock_assessment():
    return Assessment(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        status=AssessmentStatus.COMPLETED,
    )


def _create_mock_obligation():
    return Obligation(
        id=uuid.uuid4(),
        code="S1.test",
        title="Test Obligation",
        severity=ObligationSeverity.MEDIUM,
    )


def _create_mock_evidence(assessment, obligation, verdict, structured_detail):
    return Evidence(
        id=uuid.uuid4(),
        assessment_id=assessment.id,
        client_id=assessment.client_id,
        obligation_id=obligation.id,
        verdict=verdict,
        confidence=Confidence.HIGH,
        source="test:adapter",
        observed_at=datetime.now(timezone.utc),
        detail="Test evidence",
        structured_detail=structured_detail,
        sanitized_metadata={}
    )


@pytest.mark.asyncio
async def test_empty_evidence_downgrades_to_cannot_determine(mock_session):
    """
    Test that if an Evidence item has no structured_detail, the resulting finding
    is downgraded to CANNOT_DETERMINE, even if the raw verdict was OBSERVED.
    """
    assessment = _create_mock_assessment()
    obligation = _create_mock_obligation()
    
    # OBSERVED verdict but NO structured_detail
    ev = _create_mock_evidence(assessment, obligation, Verdict.OBSERVED, structured_detail=None)

    class MockResult:
        def __init__(self, items):
            self.items = items
        def scalars(self):
            class ScalarResult:
                def all(self_inner):
                    return self.items
            return ScalarResult()
        def scalar_one_or_none(self):
            return self.items[0] if self.items else None

    def mock_execute(stmt, *args, **kwargs):
        s = str(stmt).lower()
        if "reconciliation" in s:
            return MockResult([])
        if "evidence" in s:
            return MockResult([ev])
        if "obligations" in s:
            return MockResult([obligation])
        return MockResult([])

    mock_session.execute.side_effect = mock_execute
    
    service = FindingService(mock_session, assessment.client_id)
    findings = await service.generate_findings_for_assessment(assessment.id)
    
    assert len(findings) == 1
    finding = findings[0]
    
    # Should be downgraded
    assert finding.verdict == Verdict.CANNOT_DETERMINE
    assert "No valid structured evidence provided" in finding.reason


@pytest.mark.asyncio
async def test_valid_evidence_retains_verdict(mock_session):
    """
    Test that if an Evidence item HAS structured_detail, the resulting finding
    keeps its OBSERVED verdict.
    """
    assessment = _create_mock_assessment()
    obligation = _create_mock_obligation()
    
    sd = {
        "source": "test", "integration": "test", "operation": "test_op",
        "resource_type": "account", "resource_id": "123", "resource_name": "Test",
        "location": "test_loc", "observed_at": "now", "observed_state": "Bad",
        "expected_state": "Good", "detail": "detail", "reason": "reason",
        "metadata": {}
    }
    
    # OBSERVED verdict WITH structured_detail
    ev = _create_mock_evidence(assessment, obligation, Verdict.OBSERVED, structured_detail=sd)

    class MockResult:
        def __init__(self, items):
            self.items = items
        def scalars(self):
            class ScalarResult:
                def all(self_inner):
                    return self.items
            return ScalarResult()
        def scalar_one_or_none(self):
            return self.items[0] if self.items else None

    def mock_execute(stmt, *args, **kwargs):
        s = str(stmt).lower()
        if "reconciliation" in s:
            return MockResult([])
        if "evidence" in s:
            return MockResult([ev])
        if "obligations" in s:
            return MockResult([obligation])
        return MockResult([])

    mock_session.execute.side_effect = mock_execute
    
    service = FindingService(mock_session, assessment.client_id)
    findings = await service.generate_findings_for_assessment(assessment.id)
    
    assert len(findings) == 1
    finding = findings[0]
    
    # Should NOT be downgraded
    assert finding.verdict == Verdict.OBSERVED
