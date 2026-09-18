"""DPDP Obligation Register.

Central obligation register — the spine of the platform.
NOTE: This is a draft mapping pending legal review.
TODO(legal-review): Validate all obligations against final DPDP rules.

To replace with the counsel-reviewed register:
1. Update REGISTER_VERSION to the counsel-supplied version string.
2. Replace OBLIGATIONS list entries with counsel-reviewed content.
3. Add source_reference to each entry (e.g. "DPDP Act 2023, Section 4(1)").
4. Run: python -m app.cli.seed_obligations
"""

from __future__ import annotations

# Version of the obligation register. Updated when counsel provides the final reviewed register.
REGISTER_VERSION = "draft-2026-08"

# Valid categories and severities for structural validation
_VALID_CATEGORIES = {
    "consent", "data_collection", "data_retention", "data_sharing",
    "data_processing", "transparency", "rights", "security",
    "governance", "cross_border",
}
_VALID_SEVERITIES = {"high", "medium", "low"}

OBLIGATIONS = [
    # ── Section 4: Consent & Notice ──
    {
        "code": "S4.consent.collection",
        "title": "Consent before personal data collection",
        "description": "Personal data must not be collected without valid consent from the data principal.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S4",
        "source_reference": None,  # Pending counsel review
    },
    {
        "code": "S4.consent.informed",
        "title": "Informed consent with clear notice",
        "description": "Consent must be informed — the data principal must know what data is collected, for what purpose, and how to withdraw.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S4",
        "source_reference": None,
    },
    {
        "code": "S4.consent.granular",
        "title": "Granular consent per purpose",
        "description": "Consent must be granular and purpose-specific; blanket consent is insufficient.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S4",
        "source_reference": None,
    },
    {
        "code": "S4.consent.withdrawal",
        "title": "Consent withdrawal mechanism",
        "description": "Data principals must be able to withdraw consent as easily as they gave it.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S4",
        "source_reference": None,
    },

    # ── Section 5: Notice Requirements ──
    {
        "code": "S5.notice.purpose",
        "title": "Notice of processing purpose",
        "description": "A clear notice must be provided describing the purpose of personal data processing.",
        "category": "transparency",
        "severity": "high",
        "dpdp_section": "S5",
    },
    {
        "code": "S5.notice.rights",
        "title": "Notice of data principal rights",
        "description": "Notice must inform data principals of their rights including access, correction, and erasure.",
        "category": "transparency",
        "severity": "medium",
        "dpdp_section": "S5",
    },
    {
        "code": "S5.notice.grievance",
        "title": "Grievance redressal contact in notice",
        "description": "Notice must include contact details for grievance redressal.",
        "category": "transparency",
        "severity": "medium",
        "dpdp_section": "S5",
    },

    # ── Section 6: Consent Gating & Propagation ──
    {
        "code": "S6.consentGating",
        "title": "Tags/SDKs gated on consent",
        "description": "All tracking tags and SDKs must be gated on consent — they must not fire before consent is obtained.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S6",
    },
    {
        "code": "S6.consentPropagation",
        "title": "Consent signal propagation to vendors",
        "description": "Consent signals must propagate to all vendor tags and downstream systems.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S6",
    },
    {
        "code": "S6.consentMode",
        "title": "Google Consent Mode configuration",
        "description": "Google Consent Mode (or equivalent) must be properly configured to respect consent state.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S6",
    },
    {
        "code": "S6.consentRecord",
        "title": "Consent records maintained",
        "description": "Records of consent must be maintained as evidence of lawful processing.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S6",
    },

    # ── Section 7: Purpose Limitation ──
    {
        "code": "S7.purposeLimitation",
        "title": "Processing limited to stated purpose",
        "description": "Personal data must only be processed for the purpose stated at collection time.",
        "category": "data_processing",
        "severity": "high",
        "dpdp_section": "S7",
    },
    {
        "code": "S7.purposeBinding",
        "title": "Data use bound to consent purpose",
        "description": "Downstream use of data must be bound to the original consent purpose.",
        "category": "data_processing",
        "severity": "high",
        "dpdp_section": "S7",
    },

    # ── Section 8: Data Collection & Storage ──
    {
        "code": "S8.dataMinimization",
        "title": "Data minimisation",
        "description": "Only personal data necessary for the stated purpose should be collected.",
        "category": "data_collection",
        "severity": "medium",
        "dpdp_section": "S8",
    },
    {
        "code": "S8.retention",
        "title": "Data retention policy",
        "description": "Personal data must not be retained beyond the period necessary for the purpose.",
        "category": "data_retention",
        "severity": "high",
        "dpdp_section": "S8",
    },
    {
        "code": "S8.retentionJustification",
        "title": "Retention period justified",
        "description": "Maximum retention periods must have documented justification.",
        "category": "data_retention",
        "severity": "medium",
        "dpdp_section": "S8",
    },
    {
        "code": "S8.deletion",
        "title": "Data deletion upon purpose completion",
        "description": "Personal data must be deleted when consent is withdrawn or the purpose is fulfilled.",
        "category": "data_retention",
        "severity": "high",
        "dpdp_section": "S8",
    },

    # ── Section 9: Data Sharing & Transfer ──
    {
        "code": "S9.sharing.documented",
        "title": "Data sharing documented",
        "description": "All data sharing with third parties must be documented and disclosed.",
        "category": "data_sharing",
        "severity": "high",
        "dpdp_section": "S9",
    },
    {
        "code": "S9.sharing.contractual",
        "title": "Processor contracts in place",
        "description": "Data processors must operate under a valid data processing agreement.",
        "category": "data_sharing",
        "severity": "high",
        "dpdp_section": "S9",
    },
    {
        "code": "S9.sharing.audienceUpload",
        "title": "Audience upload lawful basis",
        "description": "Customer Match / Custom Audience uploads must have a recorded consent basis.",
        "category": "data_sharing",
        "severity": "high",
        "dpdp_section": "S9",
    },
    {
        "code": "S9.sharing.enhancedConversions",
        "title": "Enhanced Conversions data transmission",
        "description": "Enhanced Conversions transmit hashed personal data — requires valid consent basis.",
        "category": "data_sharing",
        "severity": "high",
        "dpdp_section": "S9",
    },
    {
        "code": "S9.sharing.crossDevice",
        "title": "Cross-device identification disclosed",
        "description": "Cross-device identification (e.g., Google Signals) must be disclosed and consented.",
        "category": "data_sharing",
        "severity": "medium",
        "dpdp_section": "S9",
    },
    {
        "code": "S9.sharing.serverSide",
        "title": "Server-side data transmission documented",
        "description": "Server-side data transmission (CAPI, server GTM) must be documented and consented.",
        "category": "data_sharing",
        "severity": "medium",
        "dpdp_section": "S9",
    },
    {
        "code": "S9.sharing.advancedMatching",
        "title": "Advanced matching consent basis",
        "description": "Advanced matching (Meta, etc.) sends hashed PII — requires consent basis.",
        "category": "data_sharing",
        "severity": "high",
        "dpdp_section": "S9",
    },

    # ── Section 10: Scope Completeness ──
    {
        "code": "S10.scope.properties",
        "title": "All web properties in scope",
        "description": "All web properties and subdomains must be identified and included in assessment scope.",
        "category": "governance",
        "severity": "medium",
        "dpdp_section": "S10",
    },
    {
        "code": "S10.scope.platforms",
        "title": "All marketing platforms declared",
        "description": "All marketing/analytics platforms in use must be declared in scope.",
        "category": "governance",
        "severity": "medium",
        "dpdp_section": "S10",
    },
    {
        "code": "S10.scope.sdks",
        "title": "All SDKs in app declared",
        "description": "All third-party SDKs in mobile apps must be inventoried and declared.",
        "category": "governance",
        "severity": "medium",
        "dpdp_section": "S10",
    },

    # ── Section 11: Security ──
    {
        "code": "S11.security.encryption",
        "title": "Data encrypted in transit and at rest",
        "description": "Personal data must be encrypted in transit and at rest.",
        "category": "security",
        "severity": "high",
        "dpdp_section": "S11",
    },
    {
        "code": "S11.security.accessControl",
        "title": "Access controls on personal data",
        "description": "Access to personal data must be restricted and role-based.",
        "category": "security",
        "severity": "high",
        "dpdp_section": "S11",
    },

    # ── Section 12: Governance ──
    {
        "code": "S12.governance.dpo",
        "title": "Data protection officer designated",
        "description": "A Data Protection Officer or equivalent must be designated.",
        "category": "governance",
        "severity": "medium",
        "dpdp_section": "S12",
    },
    {
        "code": "S12.governance.tagInventory",
        "title": "Tag/tracker inventory maintained",
        "description": "A complete inventory of all tags and trackers must be maintained.",
        "category": "governance",
        "severity": "medium",
        "dpdp_section": "S12",
    },
    {
        "code": "S12.governance.vendorManagement",
        "title": "Vendor data processing documented",
        "description": "Each vendor's data processing must be documented with purposes and legal basis.",
        "category": "governance",
        "severity": "medium",
        "dpdp_section": "S12",
    },

    # ── CMP-specific obligations ──
    {
        "code": "CMP.purposes.configured",
        "title": "CMP purposes properly configured",
        "description": "CMP purposes must align with actual data processing activities.",
        "category": "consent",
        "severity": "medium",
        "dpdp_section": "S6",
    },
    {
        "code": "CMP.geolocation.india",
        "title": "CMP geolocation rules include India",
        "description": "CMP geolocation rules must not exempt India from consent requirements.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S4",
    },
    {
        "code": "CMP.categories.complete",
        "title": "All tags categorized in CMP",
        "description": "All tags must be categorized within the CMP — uncategorized tags are ungoverned.",
        "category": "consent",
        "severity": "medium",
        "dpdp_section": "S6",
    },

    # ── App-specific obligations ──
    {
        "code": "APP.sdk.consent",
        "title": "SDK initialization gated on consent",
        "description": "Third-party SDK initialization must be gated on user consent.",
        "category": "consent",
        "severity": "high",
        "dpdp_section": "S6",
    },
    {
        "code": "APP.permissions.justified",
        "title": "App permissions justified",
        "description": "Device permissions (location, contacts, etc.) must be justified and consented.",
        "category": "data_collection",
        "severity": "medium",
        "dpdp_section": "S8",
    },
    {
        "code": "APP.logging.noPII",
        "title": "No PII in application logs",
        "description": "Application logs must not contain personal data.",
        "category": "security",
        "severity": "high",
        "dpdp_section": "S11",
    },
    {
        "code": "APP.forms.disclosure",
        "title": "Form data collection disclosed",
        "description": "Forms collecting personal data must disclose what is collected and why.",
        "category": "transparency",
        "severity": "medium",
        "dpdp_section": "S5",
    },
]

# Mapping of obligation codes to which integrations answer them
OBLIGATION_INTEGRATION_MAP: dict[str, list[str]] = {
    "S4.consent.collection": ["gtm", "cmp", "ga4"],
    "S4.consent.informed": ["cmp"],
    "S4.consent.granular": ["cmp"],
    "S4.consent.withdrawal": ["cmp"],
    "S5.notice.purpose": ["cmp"],
    "S5.notice.rights": ["cmp"],
    "S5.notice.grievance": [],  # Manual review
    "S6.consentGating": ["gtm", "github"],
    "S6.consentPropagation": ["gtm", "ga4", "cmp"],
    "S6.consentMode": ["gtm", "ga4"],
    "S6.consentRecord": ["cmp"],
    "S7.purposeLimitation": ["ga4", "google_ads", "meta"],
    "S7.purposeBinding": ["cmp", "gtm"],
    "S8.dataMinimization": ["ga4", "google_ads", "meta"],
    "S8.retention": ["ga4"],
    "S8.retentionJustification": ["ga4"],
    "S8.deletion": ["ga4"],
    "S9.sharing.documented": ["ga4", "google_ads", "meta"],
    "S9.sharing.contractual": [],  # Manual review
    "S9.sharing.audienceUpload": ["google_ads", "meta"],
    "S9.sharing.enhancedConversions": ["google_ads", "gtm"],
    "S9.sharing.crossDevice": ["ga4"],
    "S9.sharing.serverSide": ["gtm", "meta"],
    "S9.sharing.advancedMatching": ["meta"],
    "S10.scope.properties": ["search_console"],
    "S10.scope.platforms": ["gtm", "github"],
    "S10.scope.sdks": ["github"],
    "S11.security.encryption": [],  # Manual review
    "S11.security.accessControl": [],  # Manual review
    "S12.governance.dpo": [],  # Manual review
    "S12.governance.tagInventory": ["gtm"],
    "S12.governance.vendorManagement": ["gtm", "ga4", "google_ads", "meta"],
    "CMP.purposes.configured": ["cmp"],
    "CMP.geolocation.india": ["cmp"],
    "CMP.categories.complete": ["cmp", "gtm"],
    "APP.sdk.consent": ["github"],
    "APP.permissions.justified": ["github"],
    "APP.logging.noPII": ["github"],
    "APP.forms.disclosure": ["github"],
}


def get_obligations_for_integration(integration_id: str) -> list[str]:
    """Get obligation codes that a given integration answers."""
    return [
        code for code, integrations in OBLIGATION_INTEGRATION_MAP.items()
        if integration_id in integrations
    ]


def validate_obligation_code(code: str) -> bool:
    """Validate that an obligation code exists in the register."""
    return any(o["code"] == code for o in OBLIGATIONS)


def validate_obligation_register(register: list[dict] | None = None) -> list[str]:
    """Validate structural integrity of the obligation register.

    Returns a list of error messages. Empty list means valid.
    """
    entries = register if register is not None else OBLIGATIONS
    errors: list[str] = []
    seen_codes: set[str] = set()

    required_keys = {"code", "title", "category", "severity"}

    for i, entry in enumerate(entries):
        missing = required_keys - set(entry.keys())
        if missing:
            errors.append(f"Entry {i}: missing required keys: {missing}")
            continue

        code = entry["code"]
        if not isinstance(code, str) or not code.strip():
            errors.append(f"Entry {i}: 'code' must be a non-empty string")
            continue

        if code in seen_codes:
            errors.append(f"Entry {i}: duplicate code '{code}'")
        seen_codes.add(code)

        if entry["category"] not in _VALID_CATEGORIES:
            errors.append(f"Entry {i} ({code}): invalid category '{entry['category']}'")

        if entry["severity"] not in _VALID_SEVERITIES:
            errors.append(f"Entry {i} ({code}): invalid severity '{entry['severity']}'")

    return errors
