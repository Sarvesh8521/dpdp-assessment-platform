from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import GTMvsPhase1Tags

def test_gtm_vs_phase1_tags_agreement():
    rule = GTMvsPhase1Tags()
    evidence_a = [
        EvidenceItem(
            obligation_id="S12.governance.tagInventory",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="Tags found.",
            raw_metadata={"tags": [{"vendor": "Google Analytics"}, {"vendor": "Facebook Pixel"}]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S12.governance.tagInventory",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="phase1_scanner:tags", observed_at=datetime.now(timezone.utc),
            detail="Tags found.",
            raw_metadata={"tags": [{"vendor": "Google Analytics"}, {"vendor": "Facebook Pixel"}]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"

def test_gtm_vs_phase1_tags_contradiction():
    rule = GTMvsPhase1Tags()
    evidence_a = [
        EvidenceItem(
            obligation_id="S12.governance.tagInventory",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="Tags found.",
            raw_metadata={"tags": [{"vendor": "Google Analytics"}]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S12.governance.tagInventory",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="phase1_scanner:tags", observed_at=datetime.now(timezone.utc),
            detail="Tags found.",
            raw_metadata={"tags": [{"vendor": "Google Analytics"}, {"vendor": "Hotjar"}]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "Hotjar" in findings[0].detail
    assert findings[0].human_review_required is True

def test_gtm_vs_phase1_tags_missing_gtm():
    rule = GTMvsPhase1Tags()
    evidence_a = []
    evidence_b = [
        EvidenceItem(
            obligation_id="S12.governance.tagInventory",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="phase1_scanner:tags", observed_at=datetime.now(timezone.utc),
            detail="Tags found.",
            raw_metadata={"tags": [{"vendor": "Google Analytics"}]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"

def test_gtm_vs_phase1_tags_missing_phase1():
    rule = GTMvsPhase1Tags()
    evidence_a = [
        EvidenceItem(
            obligation_id="S12.governance.tagInventory",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="Tags found.",
            raw_metadata={"tags": [{"vendor": "Google Analytics"}]}
        )
    ]
    evidence_b = []

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
