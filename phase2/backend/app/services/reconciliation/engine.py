"""Cross-source reconciliation engine.

PHASE2_SPEC Section 5: 'Any single integration is a config dump. The value
is in the contradictions between them.'

7 reconciliation rules as independent testable components.
"""

from __future__ import annotations

import abc

from app.core.logging import get_logger
from app.domain.evidence import Confidence, EvidenceItem, Verdict

logger = get_logger(__name__)


class ReconciliationRule(abc.ABC):
    """Base class for reconciliation rules."""

    @property
    @abc.abstractmethod
    def id(self) -> str:
        """Unique rule identifier."""

    @property
    @abc.abstractmethod
    def source_a(self) -> str:
        """First source."""

    @property
    @abc.abstractmethod
    def source_b(self) -> str:
        """Second source."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """What this rule checks."""

    @property
    @abc.abstractmethod
    def obligation_ids(self) -> list[str]:
        """Obligations this rule maps to."""

    @abc.abstractmethod
    def reconcile(
        self,
        evidence_a: list[EvidenceItem],
        evidence_b: list[EvidenceItem],
    ) -> list[ReconciliationFinding]:
        """Execute the reconciliation rule."""


class ReconciliationFinding:
    """Result of a reconciliation check."""

    def __init__(
        self,
        rule_id: str,
        obligation_id: str,
        source_a: str,
        source_b: str,
        outcome: str,  # agreement | contradiction | partial_match | insufficient_data
        confidence: Confidence,
        detail: str,
        human_review_required: bool = False,
        evidence_a_detail: str = "",
        evidence_b_detail: str = "",
    ) -> None:
        self.rule_id = rule_id
        self.obligation_id = obligation_id
        self.source_a = source_a
        self.source_b = source_b
        self.outcome = outcome
        self.confidence = confidence
        self.detail = detail
        self.human_review_required = human_review_required
        self.evidence_a_detail = evidence_a_detail
        self.evidence_b_detail = evidence_b_detail


# ── Rule 1: GTM tags vs Phase 1 observed tags ──
class GTMvsPhase1Tags(ReconciliationRule):
    """A gap means hardcoded tags outside the container."""

    @property
    def id(self) -> str:
        return "recon_gtm_vs_phase1_tags"

    @property
    def source_a(self) -> str:
        return "gtm"

    @property
    def source_b(self) -> str:
        return "phase1_scanner"

    @property
    def description(self) -> str:
        return "Tags in GTM vs tags observed by Phase 1 scanner"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S6.consentGating", "S12.governance.tagInventory"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        # Compare tag inventories from GTM vs scanner
        gtm_tags = set()
        scanner_tags = set()

        for e in evidence_a:
            if e.raw_metadata and "tags" in e.raw_metadata:
                for tag in e.raw_metadata["tags"]:
                    gtm_tags.add(tag.get("vendor", tag.get("type", "")))

        for e in evidence_b:
            if e.raw_metadata and "tags" in e.raw_metadata:
                for tag in e.raw_metadata["tags"]:
                    scanner_tags.add(tag.get("vendor", tag.get("type", "")))

        scanner_only = scanner_tags - gtm_tags
        if scanner_only:
            findings.append(ReconciliationFinding(
                rule_id=self.id,
                obligation_id="S6.consentGating",
                source_a="gtm", source_b="phase1_scanner",
                outcome="contradiction",
                confidence=Confidence.HIGH,
                detail=f"Tags observed by scanner but NOT in GTM: {', '.join(scanner_only)}. These are likely hardcoded outside the tag manager.",
                human_review_required=True,
            ))
        elif gtm_tags and scanner_tags:
            findings.append(ReconciliationFinding(
                rule_id=self.id,
                obligation_id="S6.consentGating",
                source_a="gtm", source_b="phase1_scanner",
                outcome="agreement",
                confidence=Confidence.HIGH,
                detail="GTM tag inventory matches scanner observations.",
            ))

        return findings


# ── Rule 2: GTM consent settings vs observed consent signals ──
class GTMConsentVsObserved(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_gtm_consent_vs_observed"

    @property
    def source_a(self) -> str:
        return "gtm"

    @property
    def source_b(self) -> str:
        return "phase1_scanner"

    @property
    def description(self) -> str:
        return "GTM consent settings vs consent signals observed"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S6.consentMode", "S6.consentPropagation"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        # Compare configured consent settings with observed behavior
        gtm_consent_configured = any(
            e.verdict == Verdict.OBSERVED and "consent" in e.obligation_id.lower()
            for e in evidence_a
        )
        scanner_consent_observed = any(
            e.verdict == Verdict.OBSERVED and "consent" in e.obligation_id.lower()
            for e in evidence_b
        )

        if gtm_consent_configured and not scanner_consent_observed:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentMode",
                source_a="gtm", source_b="phase1_scanner",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail="GTM has consent settings configured but scanner did not observe consent signals. Configured but not working.",
                human_review_required=True,
            ))
        elif not gtm_consent_configured and scanner_consent_observed:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentMode",
                source_a="gtm", source_b="phase1_scanner",
                outcome="contradiction", confidence=Confidence.MEDIUM,
                detail="Scanner observed consent signals but GTM has no consent settings configured. Working but not configured in GTM.",
                human_review_required=True,
            ))
        elif gtm_consent_configured and scanner_consent_observed:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentMode",
                source_a="gtm", source_b="phase1_scanner",
                outcome="agreement", confidence=Confidence.HIGH,
                detail="GTM consent configuration matches scanner observations.",
            ))

        return findings


# ── Rule 3: GA4 linked services vs declared scope ──
class GA4LinkedVsDeclared(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_ga4_linked_vs_declared"

    @property
    def source_a(self) -> str:
        return "ga4"

    @property
    def source_b(self) -> str:
        return "scope_sheet"

    @property
    def description(self) -> str:
        return "GA4 linked services vs declared scope"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S9.sharing.documented"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        linked_services = set()
        for e in evidence_a:
            if e.source.startswith("ga4:") and "linked" in e.source.lower():
                if e.raw_metadata:
                    linked_services.add(e.raw_metadata.get("link_type", ""))

        if linked_services:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.documented",
                source_a="ga4", source_b="scope_sheet",
                outcome="contradiction" if linked_services else "agreement",
                confidence=Confidence.HIGH,
                detail=f"GA4 linked services found: {', '.join(linked_services)}. Verify all are declared in scope.",
                human_review_required=True,
            ))

        return findings


# ── Rule 4: Customer Match lists vs consent purposes ──
class AdsMatchVsConsent(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_ads_match_vs_consent"

    @property
    def source_a(self) -> str:
        return "google_ads"

    @property
    def source_b(self) -> str:
        return "cmp"

    @property
    def description(self) -> str:
        return "Customer Match lists vs consent purposes"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S9.sharing.audienceUpload"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        has_customer_match = any(
            e.verdict == Verdict.OBSERVED and "audienceUpload" in e.obligation_id
            for e in evidence_a
        )
        has_consent_purpose = any(
            e.verdict == Verdict.OBSERVED and "purpose" in e.obligation_id.lower()
            for e in evidence_b
        )

        if has_customer_match and not has_consent_purpose:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.audienceUpload",
                source_a="google_ads", source_b="cmp",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail="Customer Match lists exist but no matching consent purpose is configured in CMP.",
                human_review_required=True,
            ))

        return findings


# ── Rule 5: App SDKs vs declared marketing platforms ──
class GitHubSDKsVsDeclared(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_github_sdks_vs_declared"

    @property
    def source_a(self) -> str:
        return "github"

    @property
    def source_b(self) -> str:
        return "scope_sheet"

    @property
    def description(self) -> str:
        return "App SDKs vs declared marketing platforms"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S10.scope.sdks", "S10.scope.platforms"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        found_sdks = set()
        for e in evidence_a:
            if e.raw_metadata and "sdks" in e.raw_metadata:
                for sdk in e.raw_metadata["sdks"]:
                    found_sdks.add(sdk.get("sdk", ""))

        if found_sdks:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.sdks",
                source_a="github", source_b="scope_sheet",
                outcome="contradiction",
                confidence=Confidence.HIGH,
                detail=f"SDKs found in code: {', '.join(found_sdks)}. Verify all are declared.",
                human_review_required=True,
            ))

        return findings


# ── Rule 6: Search Console properties vs declared ──
class SCPropertiesVsDeclared(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_sc_properties_vs_declared"

    @property
    def source_a(self) -> str:
        return "search_console"

    @property
    def source_b(self) -> str:
        return "scope_sheet"

    @property
    def description(self) -> str:
        return "Search Console properties vs declared properties"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S10.scope.properties"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        for e in evidence_a:
            if e.verdict == Verdict.NOT_OBSERVED and "undeclared" in e.detail.lower():
                findings.append(ReconciliationFinding(
                    rule_id=self.id, obligation_id="S10.scope.properties",
                    source_a="search_console", source_b="scope_sheet",
                    outcome="contradiction", confidence=Confidence.HIGH,
                    detail=e.detail,
                    human_review_required=True,
                ))
        return findings


# ── Rule 7: CMP purposes vs GTM tag categories ──
class CMPPurposesVsGTMCategories(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_cmp_purposes_vs_gtm"

    @property
    def source_a(self) -> str:
        return "cmp"

    @property
    def source_b(self) -> str:
        return "gtm"

    @property
    def description(self) -> str:
        return "CMP purposes vs GTM tag categories"

    @property
    def obligation_ids(self) -> list[str]:
        return ["CMP.categories.complete"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        gtm_uncategorized = any(
            e.verdict == Verdict.NOT_OBSERVED and "consent" in e.detail.lower()
            for e in evidence_b
        )

        if gtm_uncategorized:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="CMP.categories.complete",
                source_a="cmp", source_b="gtm",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail="Tags in GTM without consent settings are ungoverned — not categorized in any CMP purpose.",
                human_review_required=True,
            ))

        return findings


# ── Rule Registry ──
ALL_RECONCILIATION_RULES: list[ReconciliationRule] = [
    GTMvsPhase1Tags(),
    GTMConsentVsObserved(),
    GA4LinkedVsDeclared(),
    AdsMatchVsConsent(),
    GitHubSDKsVsDeclared(),
    SCPropertiesVsDeclared(),
    CMPPurposesVsGTMCategories(),
]


def run_reconciliation(
    evidence_by_source: dict[str, list[EvidenceItem]],
) -> list[ReconciliationFinding]:
    """Run all reconciliation rules against collected evidence."""
    all_findings: list[ReconciliationFinding] = []

    for rule in ALL_RECONCILIATION_RULES:
        source_a_evidence = evidence_by_source.get(rule.source_a, [])
        source_b_evidence = evidence_by_source.get(rule.source_b, [])

        if source_a_evidence or source_b_evidence:
            try:
                rule_findings = rule.reconcile(source_a_evidence, source_b_evidence)
                all_findings.extend(rule_findings)
                logger.info(
                    "reconciliation_rule_executed",
                    rule_id=rule.id,
                    findings_count=len(rule_findings),
                )
            except Exception as exc:
                logger.error(
                    "reconciliation_rule_failed",
                    rule_id=rule.id,
                    error=str(exc),
                )

    return all_findings
