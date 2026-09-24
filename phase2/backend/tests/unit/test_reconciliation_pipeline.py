import uuid
from datetime import datetime, timezone
import pytest
from app.db.models.reconciliation_result import ReconciliationResult, ReconciliationOutcome
from app.db.models.evidence import Evidence, Verdict, Confidence
from app.domain.evidence import EvidenceItem, StructuredEvidence
from app.services.reconciliation.engine import ReconciliationFinding

def test_reconciliation_pipeline_integration():
    """Test that the assessment service correctly links evidence IDs in ReconciliationResult."""
    test_client_id = uuid.uuid4()
    # 1. Create historical Phase 1 evidence in the DB
    old_assessment_id = uuid.uuid4()
    p1_evidence = Evidence(
        id=uuid.uuid4(),
        client_id=test_client_id,
        assessment_id=old_assessment_id,
        obligation_id=uuid.uuid4(),
        source="phase1_scanner:network_traffic",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        observed_at=datetime.now(timezone.utc),
        detail="Phase 1 test evidence",
        sanitized_metadata={"tags": [{"vendor": "Google", "type": "Google Analytics"}]}
    )

    obl_uuid = p1_evidence.obligation_id
    historical_evidence_ids = {"phase1_scanner:network_traffic": p1_evidence.id}
    
    # Create a new evidence that would have just been collected
    new_ev_id = uuid.uuid4()
    new_evidence = Evidence(
        id=new_ev_id,
        client_id=test_client_id,
        assessment_id=uuid.uuid4(),
        obligation_id=obl_uuid,
        source="gtm:tag_analysis",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        observed_at=datetime.now(timezone.utc),
        detail="GTM test evidence")
    all_db_evidence = [(new_evidence, "S6.consentGating")]
    
    evidence_source_map = dict(historical_evidence_ids)
    for ev, _ in all_db_evidence:
        if ev.id:
            evidence_source_map[ev.source] = ev.id
            
    rf = ReconciliationFinding(
        rule_id="test_rule",
        obligation_id="S6.consentGating",
        source_a="gtm",
        source_b="phase1_scanner",
        outcome="contradiction",
        confidence=Confidence.HIGH,
        detail="Test contradiction",
        human_review_required=True,
        evidence_a_ids=["gtm:tag_analysis"],
        evidence_b_ids=["phase1_scanner:network_traffic"])
    
    evidence_a_db_id = None
    for src in (rf.evidence_a_ids or []):
        if src in evidence_source_map:
            evidence_a_db_id = evidence_source_map[src]
            break
            
    evidence_b_db_id = None
    for src in (rf.evidence_b_ids or []):
        if src in evidence_source_map:
            evidence_b_db_id = evidence_source_map[src]
            break
            
    assert evidence_a_db_id == new_ev_id
    assert evidence_b_db_id == p1_evidence.id
    
    rr = ReconciliationResult(
        assessment_id=uuid.uuid4(),
        rule_id=rf.rule_id,
        obligation_id=obl_uuid,
        source_a=rf.source_a,
        source_b=rf.source_b,
        outcome=ReconciliationOutcome(rf.outcome),
        confidence=rf.confidence,
        detail=rf.detail,
        human_review_required=rf.human_review_required,
        evidence_a_id=evidence_a_db_id,
        evidence_b_id=evidence_b_db_id,
        reconciliation_metadata={
            "evidence_a_detail": rf.evidence_a_detail,
            "evidence_b_detail": rf.evidence_b_detail,
            "evidence_a_ids": rf.evidence_a_ids,
            "evidence_b_ids": rf.evidence_b_ids,
        })
    
    assert rr.evidence_a_id == new_ev_id
    assert rr.evidence_b_id == p1_evidence.id
    assert rr.human_review_required is True
    assert rr.reconciliation_metadata["evidence_a_ids"] == ["gtm:tag_analysis"]
