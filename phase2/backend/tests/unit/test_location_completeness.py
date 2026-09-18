import pytest
from datetime import datetime, timezone
import uuid

from app.domain.evidence import StructuredEvidence
from app.db.models.finding import Finding

def test_structured_evidence_location_default():
    """Ensure location defaults to 'Unavailable' if not provided."""
    evidence = StructuredEvidence(
        source="test",
        integration="test",
        operation="test_op",
        resource_type="test_res",
        observed_at=datetime.now(timezone.utc),
        detail="test detail"
    )
    assert evidence.location == "Unavailable"


def test_location_pipeline_propagation():
    """Ensure location propagates from StructuredEvidence to Finding."""
    now = datetime.now(timezone.utc)
    
    # 1. Adapter produces StructuredEvidence with explicit location
    adapter_evidence = StructuredEvidence(
        source="gtm",
        integration="gtm",
        operation="get_live_version",
        resource_type="tag",
        resource_id="tag_123",
        resource_name="Test Tag",
        location="Container: pub_456 (My Container) > Tag: tag_123 (Test Tag)",
        observed_at=now,
        observed_state="Consent configured",
        expected_state="Consent configured",
        detail="Tag detail"
    )
    
    assert adapter_evidence.location == "Container: pub_456 (My Container) > Tag: tag_123 (Test Tag)"

    # 2. FindingService stamps it (simulated)
    finding_id = uuid.uuid4()
    evidence_details = [adapter_evidence.model_dump(mode="json")]
    for e in evidence_details:
        e["finding_id"] = str(finding_id)
        
    assert evidence_details[0]["location"] == "Container: pub_456 (My Container) > Tag: tag_123 (Test Tag)"
    
    # 3. Finding entity holds it
    obl_id = uuid.uuid4()
    finding = Finding(
        id=finding_id,
        assessment_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        obligation_id=obl_id,
        verdict="Observed",
        confidence="high",
        severity="medium",
        detail="Finding detail",
        evidence_details=evidence_details
    )
    
    assert finding.evidence_details[0]["location"] == "Container: pub_456 (My Container) > Tag: tag_123 (Test Tag)"
