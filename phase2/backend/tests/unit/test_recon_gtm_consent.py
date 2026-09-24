from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import GTMConsentVsObserved

def test_gtm_consent_vs_observed_agreement():
    rule = GTMConsentVsObserved()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentMode",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="Consent configured."
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentMode",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="phase1_scanner:consent", observed_at=datetime.now(timezone.utc),
            detail="Consent signals observed."
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"

def test_gtm_consent_vs_observed_contradiction_gtm_only():
    rule = GTMConsentVsObserved()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentMode",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="Consent configured."
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentMode",
            verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
            source="phase1_scanner:consent", observed_at=datetime.now(timezone.utc),
            detail="No consent signals observed."
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert findings[0].human_review_required is True

def test_gtm_consent_vs_observed_missing_gtm():
    rule = GTMConsentVsObserved()
    evidence_a = []
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentMode",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="phase1_scanner:consent", observed_at=datetime.now(timezone.utc),
            detail="Consent signals observed."
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"

def test_gtm_consent_vs_observed_missing_phase1():
    rule = GTMConsentVsObserved()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentMode",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="Consent configured."
        )
    ]
    evidence_b = []

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
