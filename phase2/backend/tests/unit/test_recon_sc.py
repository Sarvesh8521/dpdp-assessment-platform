from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import SCPropertiesVsDeclared

def test_sc_properties_vs_declared_agreement():
    rule = SCPropertiesVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="search_console:verified_site:https://example.com", observed_at=datetime.now(timezone.utc),
            detail="Verified property.",
            raw_metadata={"property_url": "https://example.com", "permission": "siteOwner"}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:search_console_properties", observed_at=datetime.now(timezone.utc),
            detail="Declared properties.",
            raw_metadata={"search_console_properties": ["example.com"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"
    assert "example.com" in findings[0].evidence_a_detail
    assert "example.com" in findings[0].evidence_b_detail

def test_sc_properties_vs_declared_contradiction():
    rule = SCPropertiesVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="search_console:verified_site:https://admin.otherdomain.com", observed_at=datetime.now(timezone.utc),
            detail="Verified property.",
            raw_metadata={"property_url": "https://admin.otherdomain.com", "permission": "siteOwner"}
        ),
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="search_console:verified_site:https://example.com", observed_at=datetime.now(timezone.utc),
            detail="Verified property.",
            raw_metadata={"property_url": "https://example.com", "permission": "siteOwner"}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:search_console_properties", observed_at=datetime.now(timezone.utc),
            detail="Declared properties.",
            raw_metadata={"search_console_properties": ["example.com"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "https://admin.otherdomain.com" in findings[0].detail
    assert "https://example.com" not in findings[0].detail.split("NOT declared")[1]  # It WAS declared
    assert findings[0].human_review_required is True

def test_sc_properties_vs_declared_no_declared():
    rule = SCPropertiesVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="search_console:verified_site:https://example.com", observed_at=datetime.now(timezone.utc),
            detail="Verified property.",
            raw_metadata={"property_url": "https://example.com", "permission": "siteOwner"}
        )
    ]
    evidence_b = [] # No scope sheet data

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
    assert "https://example.com" in findings[0].detail

def test_sc_properties_vs_declared_missing_sc():
    rule = SCPropertiesVsDeclared()
    evidence_a = [] # No sc data
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.properties",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:search_console_properties", observed_at=datetime.now(timezone.utc),
            detail="Declared properties.",
            raw_metadata={"search_console_properties": ["example.com"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
