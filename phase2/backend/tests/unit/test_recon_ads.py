from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import AdsMatchVsConsent

def test_ads_match_vs_consent_matching():
    rule = AdsMatchVsConsent()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.audienceUpload",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="google_ads:customer_match", observed_at=datetime.now(timezone.utc),
            detail="Customer Match lists configured.", raw_metadata={"list_count": 1}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured.",
            raw_metadata={"purpose_names": ["Targeting", "Performance", "Strictly Necessary"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"
    assert "matching consent purpose (advertising/marketing) was found" in findings[0].detail


def test_ads_match_vs_consent_mismatching():
    rule = AdsMatchVsConsent()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.audienceUpload",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="google_ads:customer_match", observed_at=datetime.now(timezone.utc),
            detail="Customer Match lists configured.", raw_metadata={"list_count": 1}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 1 consent purpose configured.",
            raw_metadata={"purpose_names": ["Strictly Necessary"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "NO matching advertising/marketing purpose" in findings[0].detail


def test_ads_match_vs_consent_missing_cmp():
    rule = AdsMatchVsConsent()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.audienceUpload",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="google_ads:customer_match", observed_at=datetime.now(timezone.utc),
            detail="Customer Match lists configured.", raw_metadata={"list_count": 1}
        )
    ]
    evidence_b = []  # No CMP evidence provided

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
    assert "no CMP purpose evidence was provided" in findings[0].detail


def test_ads_match_vs_consent_no_customer_match():
    rule = AdsMatchVsConsent()
    evidence_a = [
        EvidenceItem(
            obligation_id="S9.sharing.audienceUpload",
            verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
            source="google_ads:customer_match", observed_at=datetime.now(timezone.utc),
            detail="No Customer Match lists.", raw_metadata={}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured.",
            raw_metadata={"purpose_names": ["Targeting"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 0
