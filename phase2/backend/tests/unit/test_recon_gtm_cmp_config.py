from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import GTMCMPConfigReconciliation

def test_gtm_cmp_config_agreement():
    rule = GTMCMPConfigReconciliation()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="All 2 tags have consent settings configured.",
            raw_metadata={"tags": [
                {"has_consent_settings": True},
                {"has_consent_settings": True}
            ]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured."
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"

def test_gtm_cmp_config_gtm_missing_cmp():
    rule = GTMCMPConfigReconciliation()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="All 2 tags have consent settings configured.",
            raw_metadata={"tags": [
                {"has_consent_settings": True},
                {"has_consent_settings": False}
            ]}
        )
    ]
    evidence_b = [] # No CMP evidence

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
    assert findings[0].human_review_required is True

def test_gtm_cmp_config_cmp_missing_gtm():
    rule = GTMCMPConfigReconciliation()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="0 tags have consent settings configured.",
            raw_metadata={"tags": [
                {"has_consent_settings": False},
                {"has_consent_settings": False}
            ]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured."
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "CMP is configured and active, but GTM has ZERO tags configured" in findings[0].detail
    assert findings[0].human_review_required is True

def test_gtm_cmp_config_no_tags():
    rule = GTMCMPConfigReconciliation()
    evidence_a = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="0 tags.",
            raw_metadata={"tags": []}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured."
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 0 # Cannot evaluate without tags
