from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import GA4LinkedVsDeclared

def test_ga4_linked_vs_declared_all_declared():
    rule = GA4LinkedVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.documented",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="ga4:linked_services", observed_at=datetime.now(timezone.utc),
            detail="Linked service found", raw_metadata={"link_type": "GoogleAdsLink"}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.platforms",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:marketing_platforms", observed_at=datetime.now(timezone.utc),
            detail="Declared platforms", raw_metadata={"marketing_platforms": ["google ads", "meta"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"
    assert "is declared" in findings[0].detail


def test_ga4_linked_vs_declared_one_undeclared():
    rule = GA4LinkedVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.documented",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="ga4:linked_services", observed_at=datetime.now(timezone.utc),
            detail="Linked service found", raw_metadata={"link_type": "FirebaseLink"}
        ),
        EvidenceItem(
            obligation_id="S9.sharing.documented",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="ga4:linked_services", observed_at=datetime.now(timezone.utc),
            detail="Linked service found", raw_metadata={"link_type": "GoogleAdsLink"}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.platforms",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:marketing_platforms", observed_at=datetime.now(timezone.utc),
            detail="Declared platforms", raw_metadata={"marketing_platforms": ["google ads"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 2
    
    # One agreement, one contradiction
    outcomes = [f.outcome for f in findings]
    assert "agreement" in outcomes
    assert "contradiction" in outcomes
    
    # Detail check
    for f in findings:
        if f.outcome == "contradiction":
            assert "FirebaseLink" in f.detail
            assert "NOT declared" in f.detail
            assert f.human_review_required is True


def test_ga4_linked_vs_declared_no_scope_data():
    rule = GA4LinkedVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.documented",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="ga4:linked_services", observed_at=datetime.now(timezone.utc),
            detail="Linked service found", raw_metadata={"link_type": "GoogleAdsLink"}
        )
    ]
    evidence_b = []  # No scope sheet data

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
    assert "no scope sheet data" in findings[0].detail


def test_ga4_linked_vs_declared_no_linked_services():
    rule = GA4LinkedVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.documented",
            verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
            source="ga4:some_other_setting", observed_at=datetime.now(timezone.utc),
            detail="Not a linked service", raw_metadata={}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.platforms",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:marketing_platforms", observed_at=datetime.now(timezone.utc),
            detail="Declared platforms", raw_metadata={"marketing_platforms": ["google ads"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 0
