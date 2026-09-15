from datetime import datetime, timezone

from app.domain.evidence import (
    EvidenceItem,
    Verdict,
    Confidence,
    apply_confidence_rules
)

def create_evidence(verdict, confidence, source, obligation_id="O1"):
    return EvidenceItem(
        obligation_id=obligation_id,
        verdict=verdict,
        confidence=confidence,
        source=source,
        observed_at=datetime.now(timezone.utc),
        detail="Test detail"
    )

def test_single_source():
    items = [create_evidence(Verdict.OBSERVED, Confidence.MEDIUM, "gtm:tags")]
    verdict, confidence, human_review = apply_confidence_rules(items, "O1")
    assert verdict == Verdict.OBSERVED
    assert confidence == Confidence.MEDIUM
    assert not human_review

def test_two_agreeing_independent_sources_upgrade():
    items = [
        create_evidence(Verdict.OBSERVED, Confidence.MEDIUM, "gtm:tags"),
        create_evidence(Verdict.OBSERVED, Confidence.LOW, "ga4:settings")
    ]
    verdict, confidence, human_review = apply_confidence_rules(items, "O1")
    
    # Agreement across two distinct unique source roots ("gtm" and "ga4") -> HIGH
    assert verdict == Verdict.OBSERVED
    assert confidence == Confidence.HIGH
    assert not human_review

def test_two_agreeing_same_source_no_upgrade():
    items = [
        create_evidence(Verdict.OBSERVED, Confidence.MEDIUM, "gtm:tags"),
        create_evidence(Verdict.OBSERVED, Confidence.LOW, "gtm:variables")
    ]
    verdict, confidence, human_review = apply_confidence_rules(items, "O1")
    
    # Same root source ("gtm"), so use highest confidence, but no upgrade
    assert verdict == Verdict.OBSERVED
    assert confidence == Confidence.MEDIUM
    assert not human_review

def test_conflicting_sources_human_review():
    items = [
        create_evidence(Verdict.OBSERVED, Confidence.HIGH, "gtm:tags"),
        create_evidence(Verdict.NOT_OBSERVED, Confidence.HIGH, "ga4:settings")
    ]
    verdict, confidence, human_review = apply_confidence_rules(items, "O1")
    
    # Conflict forces human review
    assert verdict == Verdict.CANNOT_DETERMINE
    assert confidence == Confidence.LOW
    assert human_review

def test_low_confidence_never_not_observed():
    # If explicitly passed LOW and NOT_OBSERVED, the pydantic model validator catches it
    item = create_evidence(Verdict.NOT_OBSERVED, Confidence.LOW, "gtm:tags")
    assert item.verdict == Verdict.CANNOT_DETERMINE
    assert item.confidence == Confidence.LOW
