from datetime import datetime, timezone
from app.domain.evidence import EvidenceItem, Verdict, Confidence
from app.services.reconciliation.engine import (
    run_reconciliation,
    GTMvsPhase1Tags,
    GTMConsentVsObserved,
    GA4LinkedVsDeclared,
    AdsMatchVsConsent,
    GitHubSDKsVsDeclared,
    SCPropertiesVsDeclared,
    CMPPurposesVsGTMCategories,
)

def create_evidence(source, verdict, obligation, detail="", raw_metadata=None):
    return EvidenceItem(
        obligation_id=obligation,
        verdict=verdict,
        confidence=Confidence.HIGH,
        source=source,
        observed_at=datetime.now(timezone.utc),
        detail=detail,
        raw_metadata=raw_metadata
    )

def test_rule1_gtm_vs_phase1_tags():
    rule = GTMvsPhase1Tags()
    
    # Contradiction: scanner found tags not in GTM
    gtm_evidence = [create_evidence("gtm:tags", Verdict.OBSERVED, "S12", raw_metadata={"tags": [{"type": "google_analytics"}]})]
    scanner_evidence = [create_evidence("phase1_scanner:tags", Verdict.OBSERVED, "S12", raw_metadata={"tags": [{"type": "meta"}, {"type": "google_analytics"}]})]
    
    findings = rule.reconcile(gtm_evidence, scanner_evidence)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "meta" in findings[0].detail

def test_rule2_gtm_consent_vs_observed():
    rule = GTMConsentVsObserved()
    
    gtm_evidence = [create_evidence("gtm:tag_analysis", Verdict.OBSERVED, "S6.consentMode")]
    scanner_evidence = [create_evidence("phase1_scanner:consent", Verdict.NOT_OBSERVED, "S6.consentMode")]
    
    findings = rule.reconcile(gtm_evidence, scanner_evidence)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "Configured but not working" in findings[0].detail

def test_rule3_ga4_linked_vs_declared():
    rule = GA4LinkedVsDeclared()
    
    ga4_evidence = [create_evidence("ga4:linked", Verdict.OBSERVED, "S9.sharing.documented", raw_metadata={"link_type": "googleAdsLinks"})]
    scope_evidence = []
    
    findings = rule.reconcile(ga4_evidence, scope_evidence)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "googleAdsLinks" in findings[0].detail

def test_rule4_ads_match_vs_consent():
    rule = AdsMatchVsConsent()
    
    ads_evidence = [create_evidence("google_ads", Verdict.OBSERVED, "S9.sharing.audienceUpload")]
    cmp_evidence = [create_evidence("cmp", Verdict.NOT_OBSERVED, "CMP.purpose")]
    
    findings = rule.reconcile(ads_evidence, cmp_evidence)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"

def test_rule5_github_sdks_vs_declared():
    rule = GitHubSDKsVsDeclared()
    
    github_evidence = [create_evidence("github", Verdict.OBSERVED, "S10.scope.sdks", raw_metadata={"sdks": [{"sdk": "react-native-fbsdk-next"}]})]
    
    findings = rule.reconcile(github_evidence, [])
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    assert "react-native-fbsdk-next" in findings[0].detail

def test_rule6_sc_properties_vs_declared():
    rule = SCPropertiesVsDeclared()
    
    sc_evidence = [create_evidence("search_console", Verdict.NOT_OBSERVED, "S10", detail="undeclared property https://example.com")]
    
    findings = rule.reconcile(sc_evidence, [])
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"

def test_rule7_cmp_purposes_vs_gtm():
    rule = CMPPurposesVsGTMCategories()
    
    cmp_evidence = []
    gtm_evidence = [create_evidence("gtm", Verdict.NOT_OBSERVED, "CMP.categories.complete", detail="tags without consent")]
    
    findings = rule.reconcile(cmp_evidence, gtm_evidence)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
    
def test_run_reconciliation():
    evidence = {
        "gtm": [create_evidence("gtm:tags", Verdict.OBSERVED, "S12", raw_metadata={"tags": [{"type": "google_analytics"}]})],
        "phase1_scanner": [create_evidence("phase1_scanner:tags", Verdict.OBSERVED, "S12", raw_metadata={"tags": [{"type": "meta"}, {"type": "google_analytics"}]})]
    }
    findings = run_reconciliation(evidence)
    assert len(findings) == 1
    assert findings[0].outcome == "contradiction"
