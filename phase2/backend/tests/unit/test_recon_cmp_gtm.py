from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import CMPPurposesVsGTMCategories

def test_cmp_vs_gtm_matching():
    rule = CMPPurposesVsGTMCategories()
    evidence_a = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured.",
            raw_metadata={"purpose_names": ["Targeting", "Performance", "Strictly Necessary"]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="All 2 tags have consent settings configured.",
            raw_metadata={"tags": [
                {
                    "has_consent_settings": True,
                    "consent_settings": {
                        "consent_type": {"value": "ad_storage"}
                    }
                },
                {
                    "has_consent_settings": True,
                    "consent_settings": {
                        "consent_type": {"value": "analytics_storage"}
                    }
                }
            ]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 2
    for f in findings:
        assert f.outcome == "agreement"
        assert "is governed by a matching CMP purpose" in f.detail

def test_cmp_vs_gtm_mismatch():
    rule = CMPPurposesVsGTMCategories()
    evidence_a = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 1 consent purpose configured.",
            raw_metadata={"purpose_names": ["Performance"]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="All 2 tags have consent settings configured.",
            raw_metadata={"tags": [
                {
                    "has_consent_settings": True,
                    "consent_settings": {
                        "consent_type": {"value": "ad_storage"}
                    }
                },
                {
                    "has_consent_settings": True,
                    "consent_settings": {
                        "consent_type": {"value": "analytics_storage"}
                    }
                }
            ]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 2
    
    # analytics_storage matches Performance
    outcomes = [f.outcome for f in findings]
    assert "agreement" in outcomes
    assert "contradiction" in outcomes

def test_cmp_vs_gtm_missing_cmp():
    rule = CMPPurposesVsGTMCategories()
    evidence_a = []
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="All 1 tags have consent settings configured.",
            raw_metadata={"tags": [
                {
                    "has_consent_settings": True,
                    "consent_settings": {
                        "consent_type": {"value": "ad_storage"}
                    }
                }
            ]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"

def test_cmp_vs_gtm_no_gtm_categories():
    rule = CMPPurposesVsGTMCategories()
    evidence_a = [
        EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="cmp:onetrust:purposes", observed_at=datetime.now(timezone.utc),
            detail="Found 3 consent purposes configured.",
            raw_metadata={"purpose_names": ["Targeting", "Performance", "Strictly Necessary"]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S6.consentGating",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="gtm:tag_analysis", observed_at=datetime.now(timezone.utc),
            detail="All 1 tags have consent settings configured.",
            raw_metadata={"tags": [
                {
                    "has_consent_settings": False,
                    "consent_settings": None
                }
            ]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 0
