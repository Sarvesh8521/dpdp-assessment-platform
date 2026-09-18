from datetime import datetime, timezone

from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import GitHubSDKsVsDeclared

def test_github_sdks_vs_declared_agreement():
    rule = GitHubSDKsVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S10.scope.sdks",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="github:static_analysis", observed_at=datetime.now(timezone.utc),
            detail="Found SDKs.",
            raw_metadata={"sdks": [{"sdk": "Firebase"}, {"sdk": "AppsFlyer"}]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.platforms",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:marketing_platforms", observed_at=datetime.now(timezone.utc),
            detail="Declared platforms.",
            raw_metadata={"marketing_platforms": ["Google Analytics for Firebase", "AppsFlyer", "Meta"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "agreement"
    assert "Firebase" in findings[0].evidence_a_detail
    assert "appsflyer" in findings[0].evidence_b_detail.lower()

def test_github_sdks_vs_declared_contradiction():
    rule = GitHubSDKsVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S10.scope.sdks",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="github:static_analysis", observed_at=datetime.now(timezone.utc),
            detail="Found SDKs.",
            raw_metadata={"sdks": [{"sdk": "CleverTap"}, {"sdk": "AppsFlyer"}]}
        )
    ]
    evidence_b = [
        EvidenceItem(
            obligation_id="S10.scope.platforms",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="scope_sheet:marketing_platforms", observed_at=datetime.now(timezone.utc),
            detail="Declared platforms.",
            raw_metadata={"marketing_platforms": ["AppsFlyer"]}
        )
    ]

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "CleverTap" in findings[0].detail
    assert "AppsFlyer" not in findings[0].detail  # It was declared
    assert findings[0].human_review_required is True

def test_github_sdks_vs_declared_no_declared_platforms():
    rule = GitHubSDKsVsDeclared()
    evidence_a = [
        EvidenceItem(
            obligation_id="S10.scope.sdks",
            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
            source="github:static_analysis", observed_at=datetime.now(timezone.utc),
            detail="Found SDKs.",
            raw_metadata={"sdks": [{"sdk": "Branch"}]}
        )
    ]
    evidence_b = [] # No scope sheet data

    findings = rule.reconcile(evidence_a, evidence_b)
    assert len(findings) == 1
    assert findings[0].outcome == "insufficient_data"
    assert "Branch" in findings[0].detail
