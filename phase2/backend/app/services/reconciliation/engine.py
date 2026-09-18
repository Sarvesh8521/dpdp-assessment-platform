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
        if not evidence_b:
            return []
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
        if not evidence_b:
            return []
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
        
        if not evidence_a:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.documented",
                source_a="ga4", source_b="scope_sheet",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="Scope sheet declares platforms, but GA4 integration is not connected to verify linked services.",
                human_review_required=True,
                evidence_a_detail="GA4 evidence missing.",
                evidence_b_detail="Scope sheet platforms provided."
            )]
        
        # 1. Gather all GA4 linked services
        linked_services = []
        for e in evidence_a:
            if e.source.startswith("ga4:") and "linked" in e.source.lower():
                if e.raw_metadata and "link_type" in e.raw_metadata:
                    link_type = e.raw_metadata.get("link_type", "")
                    if link_type:
                        linked_services.append((link_type, e))

        if not linked_services:
            return []

        # 2. Gather declared platforms from scope_sheet
        declared_platforms = set()
        scope_evidence_items = []
        for e in evidence_b:
            if e.source == "scope_sheet:marketing_platforms" and e.raw_metadata and "marketing_platforms" in e.raw_metadata:
                for p in e.raw_metadata["marketing_platforms"]:
                    declared_platforms.add("".join(c for c in p.lower() if c.isalnum()))
                scope_evidence_items.append(e)

        # If no scope data exists at all for platforms, we cannot determine if they are declared
        if not scope_evidence_items:
            for link_type, e in linked_services:
                findings.append(ReconciliationFinding(
                    rule_id=self.id, obligation_id="S9.sharing.documented",
                    source_a="ga4", source_b="scope_sheet",
                    outcome="insufficient_data",
                    confidence=Confidence.LOW,
                    detail=f"GA4 linked service '{link_type}' found, but no scope sheet data provided to verify.",
                    human_review_required=True,
                    evidence_a_detail=e.detail,
                    evidence_b_detail="No declared platforms provided in scope sheet",
                ))
            return findings

        # 3. Compare each linked service against declared platforms
        for link_type, e in linked_services:
            normalized_link = "".join(c for c in link_type.lower() if c.isalnum())
            
            is_declared = any(
                normalized_link in p or p in normalized_link
                for p in declared_platforms if p
            )

            if is_declared:
                findings.append(ReconciliationFinding(
                    rule_id=self.id, obligation_id="S9.sharing.documented",
                    source_a="ga4", source_b="scope_sheet",
                    outcome="agreement",
                    confidence=Confidence.HIGH,
                    detail=f"GA4 linked service '{link_type}' is declared in the scope sheet.",
                    human_review_required=False,
                    evidence_a_detail=e.detail,
                    evidence_b_detail=f"Declared platforms in scope sheet include: {', '.join(declared_platforms)}",
                ))
            else:
                findings.append(ReconciliationFinding(
                    rule_id=self.id, obligation_id="S9.sharing.documented",
                    source_a="ga4", source_b="scope_sheet",
                    outcome="contradiction",
                    confidence=Confidence.HIGH,
                    detail=f"GA4 linked service '{link_type}' is active but NOT declared in the scope sheet.",
                    human_review_required=True,
                    evidence_a_detail=e.detail,
                    evidence_b_detail=f"Declared platforms in scope sheet include: {', '.join(declared_platforms)}",
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
        if not evidence_a:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.audienceUpload",
                source_a="google_ads", source_b="cmp",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="CMP integration is connected, but Google Ads integration is not connected to verify Customer Match lists.",
                human_review_required=True,
                evidence_a_detail="Google Ads evidence missing.",
                evidence_b_detail="CMP evidence provided."
            )]
            
        # 1. Identify if Customer Match is present
        has_customer_match = False
        ads_evidence = None
        for e in evidence_a:
            if e.verdict == Verdict.OBSERVED and "audienceUpload" in e.obligation_id:
                has_customer_match = True
                ads_evidence = e
                break

        if not has_customer_match:
            return []

        # 2. Identify the applicable CMP purpose/category evidence
        cmp_purposes = []
        cmp_evidence = None
        for e in evidence_b:
            if "purpose" in e.obligation_id.lower() or "categories" in e.obligation_id.lower():
                if e.verdict == Verdict.OBSERVED:
                    cmp_evidence = e
                    if e.raw_metadata and "purposes" in e.raw_metadata:
                        cmp_purposes.extend([str(p).lower() for p in e.raw_metadata["purposes"]])
                    elif e.raw_metadata and "categories" in e.raw_metadata:
                        cmp_purposes.extend([str(c).lower() for c in e.raw_metadata["categories"]])
                    elif e.raw_metadata and "purpose_names" in e.raw_metadata:
                        cmp_purposes.extend([str(p).lower() for p in e.raw_metadata["purpose_names"]])
                    else:
                        cmp_purposes.append(e.detail.lower())

        # 3. Missing CMP evidence
        if not cmp_evidence:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.audienceUpload",
                source_a="google_ads", source_b="cmp",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="Customer Match lists exist, but no CMP purpose evidence was provided to verify consent.",
                human_review_required=True,
                evidence_a_detail=ads_evidence.detail if ads_evidence else "",
                evidence_b_detail="No CMP evidence provided.",
            )]

        # 4. Check for matching purpose
        matching_keywords = ["marketing", "advertising", "targeting", "retargeting"]
        has_matching_purpose = any(
            keyword in purpose
            for purpose in cmp_purposes
            for keyword in matching_keywords
        )

        if has_matching_purpose:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.audienceUpload",
                source_a="google_ads", source_b="cmp",
                outcome="agreement", confidence=Confidence.HIGH,
                detail="Customer Match lists exist, and a matching consent purpose (advertising/marketing) was found in CMP.",
                human_review_required=False,
                evidence_a_detail=ads_evidence.detail,
                evidence_b_detail=cmp_evidence.detail,
            )]
        else:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S9.sharing.audienceUpload",
                source_a="google_ads", source_b="cmp",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail="Customer Match lists exist, but NO matching advertising/marketing purpose was found in CMP.",
                human_review_required=True,
                evidence_a_detail=ads_evidence.detail,
                evidence_b_detail=cmp_evidence.detail,
            )]


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
        if not evidence_a:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.sdks",
                source_a="github", source_b="scope_sheet",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="Scope sheet declares marketing platforms, but GitHub integration is not connected to verify SDKs.",
                human_review_required=True,
                evidence_a_detail="GitHub evidence missing.",
                evidence_b_detail="Scope sheet platforms provided."
            )]

        found_sdks = set()
        for e in evidence_a:
            if e.raw_metadata and "sdks" in e.raw_metadata:
                for sdk in e.raw_metadata["sdks"]:
                    found_sdks.add(sdk.get("sdk", ""))

        if not found_sdks:
            return findings

        declared_platforms = set()
        scope_provided = False
        for e in evidence_b:
            if e.source == "scope_sheet:marketing_platforms" and e.raw_metadata and "marketing_platforms" in e.raw_metadata:
                scope_provided = True
                for p in e.raw_metadata["marketing_platforms"]:
                    declared_platforms.add(p.lower().strip())
                    
        if not scope_provided:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.sdks",
                source_a="github", source_b="scope_sheet",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail=f"GitHub SDKs found ({', '.join(found_sdks)}), but no marketing platforms were declared in scope sheet to verify against.",
                human_review_required=True,
                evidence_a_detail=f"SDKs found: {', '.join(found_sdks)}",
                evidence_b_detail="Scope sheet evidence missing."
            )]

        undeclared = []
        for sdk in found_sdks:
            sdk_lower = sdk.lower()
            if not any(d in sdk_lower or sdk_lower in d for d in declared_platforms if d):
                undeclared.append(sdk)

        if undeclared:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.sdks",
                source_a="github", source_b="scope_sheet",
                outcome="contradiction",
                confidence=Confidence.HIGH,
                detail=f"SDKs found in code but NOT declared in marketing platforms: {', '.join(undeclared)}.",
                human_review_required=True,
                evidence_a_detail=f"SDKs found: {', '.join(found_sdks)}",
                evidence_b_detail=f"Declared platforms: {', '.join(declared_platforms) if declared_platforms else 'None'}"
            ))
        else:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.sdks",
                source_a="github", source_b="scope_sheet",
                outcome="agreement",
                confidence=Confidence.HIGH,
                detail="All SDKs found in code are properly declared in scope.",
                human_review_required=False,
                evidence_a_detail=f"SDKs found: {', '.join(found_sdks)}",
                evidence_b_detail=f"Declared platforms: {', '.join(declared_platforms)}"
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
        
        if not evidence_a:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.properties",
                source_a="search_console", source_b="scope_sheet",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="Scope sheet declares properties, but Search Console integration is not connected to verify.",
                human_review_required=True,
                evidence_a_detail="Search Console evidence missing.",
                evidence_b_detail="Scope sheet properties provided."
            )]

        found_properties = set()
        for e in evidence_a:
            if "search_console:verified_site:" in e.source and e.raw_metadata and "property_url" in e.raw_metadata:
                found_properties.add(e.raw_metadata["property_url"].strip().lower())
                
        if not found_properties:
            return findings
            
        declared_properties = set()
        scope_provided = False
        for e in evidence_b:
            if e.source == "scope_sheet:search_console_properties" and e.raw_metadata and "search_console_properties" in e.raw_metadata:
                scope_provided = True
                for p in e.raw_metadata["search_console_properties"]:
                    declared_properties.add(p.strip().lower())
                    
        if not scope_provided:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.properties",
                source_a="search_console", source_b="scope_sheet",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail=f"Search console verified properties found ({', '.join(found_properties)}), but no properties were declared in scope sheet to verify against.",
                human_review_required=True,
                evidence_a_detail=f"Verified properties: {', '.join(found_properties)}",
                evidence_b_detail="Scope sheet evidence missing."
            )]
                    
        undeclared = []
        for prop in found_properties:
            # Check if property is in declared properties (allow substring matches for subdomains)
            if not any(d in prop or prop in d for d in declared_properties if d):
                undeclared.append(prop)
                
        if undeclared:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.properties",
                source_a="search_console", source_b="scope_sheet",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail=f"Properties verified in Search Console but NOT declared in scope: {', '.join(undeclared)}.",
                human_review_required=True,
                evidence_a_detail=f"Verified properties: {', '.join(found_properties)}",
                evidence_b_detail=f"Declared properties: {', '.join(declared_properties) if declared_properties else 'None'}"
            ))
        else:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S10.scope.properties",
                source_a="search_console", source_b="scope_sheet",
                outcome="agreement", confidence=Confidence.HIGH,
                detail="All properties verified in Search Console are properly declared in scope.",
                human_review_required=False,
                evidence_a_detail=f"Verified properties: {', '.join(found_properties)}",
                evidence_b_detail=f"Declared properties: {', '.join(declared_properties)}"
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
        
        gtm_categories = set()
        gtm_evidence = None

        for e in evidence_b:
            if e.source == "gtm:tag_analysis" and isinstance(e.raw_metadata, dict):
                gtm_evidence = e
                tags = e.raw_metadata.get("tags", [])
                for tag in tags:
                    if tag.get("has_consent_settings"):
                        cs = tag.get("consent_settings") or {}
                        ctype = cs.get("consent_type")
                        
                        if isinstance(ctype, dict):
                            val = ctype.get("value")
                            if isinstance(val, str):
                                for v in val.split(','):
                                    gtm_categories.add(v.strip().lower())
                            elif isinstance(val, list):
                                for v in val:
                                    gtm_categories.add(str(v).strip().lower())
                        elif isinstance(ctype, str):
                            for v in ctype.split(','):
                                gtm_categories.add(v.strip().lower())
                        elif isinstance(ctype, list):
                            for v in ctype:
                                gtm_categories.add(str(v).strip().lower())

        if not evidence_b:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="CMP.categories.complete",
                source_a="cmp", source_b="gtm",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="CMP purposes exist, but GTM integration is not connected to verify categories.",
                human_review_required=True,
                evidence_a_detail="CMP evidence provided.",
                evidence_b_detail="GTM evidence missing."
            )]

        if not gtm_categories:
            return findings

        cmp_purposes = set()
        cmp_evidence = None
        for e in evidence_a:
            if "purpose" in e.obligation_id.lower() or "categories" in e.obligation_id.lower():
                if e.verdict == Verdict.OBSERVED:
                    cmp_evidence = e
                    if e.raw_metadata and "purposes" in e.raw_metadata:
                        cmp_purposes.update(str(p).lower() for p in e.raw_metadata["purposes"])
                    elif e.raw_metadata and "categories" in e.raw_metadata:
                        cmp_purposes.update(str(c).lower() for c in e.raw_metadata["categories"])
                    elif e.raw_metadata and "purpose_names" in e.raw_metadata:
                        cmp_purposes.update(str(p).lower() for p in e.raw_metadata["purpose_names"])
                    else:
                        cmp_purposes.add(e.detail.lower())

        if not cmp_evidence:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="CMP.categories.complete",
                source_a="cmp", source_b="gtm",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail=f"GTM tags require consent for: {', '.join(gtm_categories)}, but no CMP evidence was provided to verify them.",
                human_review_required=True,
                evidence_a_detail="No CMP evidence provided.",
                evidence_b_detail=gtm_evidence.detail if gtm_evidence else "",
            ))
            return findings

        # Mapping dictionary
        CATEGORY_MAP = {
            "ad_storage": ["targeting", "advertising", "marketing", "ad"],
            "ad_user_data": ["targeting", "advertising", "marketing", "ad"],
            "ad_personalization": ["targeting", "advertising", "marketing", "ad", "personalization"],
            "analytics_storage": ["performance", "analytics", "statistics", "measurement"],
            "personalization_storage": ["functional", "personalization", "preferences"],
            "functionality_storage": ["functional", "preferences", "necessary", "essential"],
            "security_storage": ["necessary", "essential", "security"],
        }

        for gtm_cat in gtm_categories:
            mapped_keywords = CATEGORY_MAP.get(gtm_cat, [gtm_cat])
            has_match = any(
                kw in cmp_purpose
                for cmp_purpose in cmp_purposes
                for kw in mapped_keywords
            )

            if has_match:
                findings.append(ReconciliationFinding(
                    rule_id=self.id, obligation_id="CMP.categories.complete",
                    source_a="cmp", source_b="gtm",
                    outcome="agreement", confidence=Confidence.HIGH,
                    detail=f"GTM consent category '{gtm_cat}' is governed by a matching CMP purpose.",
                    human_review_required=False,
                    evidence_a_detail=cmp_evidence.detail,
                    evidence_b_detail=f"GTM tags require: {gtm_cat}",
                ))
            else:
                findings.append(ReconciliationFinding(
                    rule_id=self.id, obligation_id="CMP.categories.complete",
                    source_a="cmp", source_b="gtm",
                    outcome="contradiction", confidence=Confidence.HIGH,
                    detail=f"GTM requires consent for '{gtm_cat}', but NO corresponding CMP purpose is configured.",
                    human_review_required=True,
                    evidence_a_detail=cmp_evidence.detail,
                    evidence_b_detail=f"GTM tags require: {gtm_cat}",
                ))

        return findings


# ── Rule 8: GTM ↔ CMP configuration ──
class GTMCMPConfigReconciliation(ReconciliationRule):
    @property
    def id(self) -> str:
        return "recon_gtm_cmp_config"

    @property
    def source_a(self) -> str:
        return "gtm"

    @property
    def source_b(self) -> str:
        return "cmp"

    @property
    def description(self) -> str:
        return "GTM Consent Mode configuration vs CMP configuration"

    @property
    def obligation_ids(self) -> list[str]:
        return ["S6.consentGating"]

    def reconcile(self, evidence_a: list[EvidenceItem], evidence_b: list[EvidenceItem]) -> list[ReconciliationFinding]:
        findings: list[ReconciliationFinding] = []
        
        if not evidence_a:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentGating",
                source_a="gtm", source_b="cmp",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="CMP integration is connected, but GTM integration is not connected to verify consent settings.",
                human_review_required=True,
                evidence_a_detail="GTM evidence missing.",
                evidence_b_detail="CMP evidence provided."
            )]

        if not evidence_b:
            return [ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentGating",
                source_a="gtm", source_b="cmp",
                outcome="insufficient_data", confidence=Confidence.LOW,
                detail="GTM tags have Consent Settings enabled, but CMP integration is not connected to verify.",
                human_review_required=True,
                evidence_a_detail="GTM evidence provided.",
                evidence_b_detail="CMP evidence missing."
            )]
        
        gtm_evidence = None
        gtm_has_consent_settings = False
        total_tags = 0
        
        for e in evidence_a:
            if e.source == "gtm:tag_analysis" and isinstance(e.raw_metadata, dict):
                gtm_evidence = e
                tags = e.raw_metadata.get("tags", [])
                total_tags += len(tags)
                if any(t.get("has_consent_settings") for t in tags):
                    gtm_has_consent_settings = True
                    
        # If GTM has no tags at all, we can't evaluate
        if total_tags == 0:
            return []
            
        cmp_evidence = None
        cmp_is_configured = False
        for e in evidence_b:
            if e.verdict == Verdict.OBSERVED and ("purpose" in e.obligation_id.lower() or "categories" in e.obligation_id.lower()):
                cmp_evidence = e
                cmp_is_configured = True
                break

        if gtm_has_consent_settings and not cmp_is_configured:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentGating",
                source_a="gtm", source_b="cmp",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail="GTM has tags configured with Consent Settings (Consent Mode), but NO CMP is configured to provide those signals.",
                human_review_required=True,
                evidence_a_detail=gtm_evidence.detail if gtm_evidence else "GTM tags require consent.",
                evidence_b_detail="No CMP configuration observed."
            ))
        elif cmp_is_configured and not gtm_has_consent_settings:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentGating",
                source_a="gtm", source_b="cmp",
                outcome="contradiction", confidence=Confidence.HIGH,
                detail="CMP is configured and active, but GTM has ZERO tags configured with Consent Settings to respect it.",
                human_review_required=True,
                evidence_a_detail="No tags in GTM have consent settings configured.",
                evidence_b_detail=cmp_evidence.detail if cmp_evidence else "CMP is configured."
            ))
        else:
            findings.append(ReconciliationFinding(
                rule_id=self.id, obligation_id="S6.consentGating",
                source_a="gtm", source_b="cmp",
                outcome="agreement", confidence=Confidence.HIGH,
                detail="GTM and CMP configuration status are aligned (both configured or both unconfigured).",
                human_review_required=False,
                evidence_a_detail="GTM consent settings match CMP presence.",
                evidence_b_detail="CMP presence matches GTM consent settings."
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
    GTMCMPConfigReconciliation(),
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
