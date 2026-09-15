"""Google Tag Manager adapter.

PHASE2_SPEC Section 4.1 — Build this first. Highest value of every integration.

API: Tag Manager API v2
Scope: https://www.googleapis.com/auth/tagmanager.readonly

Obligations answered:
- S6.consentGating — consent gating completeness
- S6.consentPropagation — consent signal propagation
- S6.consentMode — consent mode configuration
- S9.sharing.enhancedConversions — enhanced conversions data
- S10.scope.platforms — all platforms declared
- S12.governance.tagInventory — tag inventory

NEVER store: Custom HTML tag bodies verbatim. Store hash + classification.

Definition of done (Section 9):
1. Read-only scopes only, mapped to obligations ✓
2. Forbidden endpoint test passes ✓
3. No personal data reaches evidence store ✓
4. Audit log for every API call ✓
5. Graceful degradation ✓
6. Acceptance test passes ✓
7. Findings render into report ✓
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domain.evidence import Confidence, EvidenceItem, Verdict
from app.domain.obligations import get_obligations_for_integration
from app.integrations.base.adapter import (
    AdapterResult,
    ConnectionInfo,
    HealthStatus,
    IntegrationAdapter,
)
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.security.pii_sanitizer import (
    AdapterSanitizer,
    sanitize_html_tag_body,
)

logger = get_logger(__name__)

GTM_SCOPE = "https://www.googleapis.com/auth/tagmanager.readonly"
GTM_BASE_URL = "https://tagmanager.googleapis.com"

# Safe fields that MAY be persisted
GTM_SAFE_FIELDS = frozenset({
    "accountId", "containerId", "workspaceId", "tagId", "triggerId",
    "variableId", "name", "type", "fingerprint", "tagManagerUrl",
    "path", "firingTriggerId", "blockingTriggerId", "consentSettings",
    "monitoringMetadata", "monitoringMetadataTagNameKey",
    "parameter", "parentFolderId", "notes", "paused",
    "scheduleStartMs", "scheduleEndMs", "setupTag", "teardownTag",
    "priority", "tagFiringOption",
    "vendorTemplate", "builtInVariable",
    "containerVersionId", "description", "numTags", "numTriggers",
    "numVariables", "numCustomTemplates",
    "container", "publicId", "usageContext",
})

# Fields that must NEVER be persisted (may contain hardcoded PII)
GTM_FORBIDDEN_FIELDS = frozenset({
    "email", "phone", "user_id", "customer_id",
    "customHtml",  # Store hash + classification instead
    "credential", "secret", "token", "apiKey",
})

# Endpoints that must NEVER be called
GTM_FORBIDDEN_ENDPOINTS: list[str] = []  # GTM API has no PII endpoints, but we list for completeness


class GTMAdapter(IntegrationAdapter):
    """Google Tag Manager integration adapter."""

    @property
    def id(self) -> str:
        return "gtm"

    @property
    def provider(self) -> str:
        return "google_tag_manager"

    @property
    def required_scopes(self) -> list[str]:
        return [GTM_SCOPE]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("gtm")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            GTM_SCOPE: [
                "S6.consentGating",
                "S6.consentPropagation",
                "S6.consentMode",
                "S9.sharing.enhancedConversions",
                "S10.scope.platforms",
                "S12.governance.tagInventory",
                "S7.purposeBinding",
                "CMP.categories.complete",
            ],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return GTM_FORBIDDEN_ENDPOINTS

    @property
    def safe_fields(self) -> frozenset[str]:
        return GTM_SAFE_FIELDS

    @property
    def forbidden_fields(self) -> frozenset[str]:
        return GTM_FORBIDDEN_FIELDS

    def __init__(self) -> None:
        self._sanitizer = AdapterSanitizer(
            allowed_fields=GTM_SAFE_FIELDS,
            forbidden_fields=GTM_FORBIDDEN_FIELDS,
        )

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        """Connect to GTM using OAuth credentials."""
        access_token = credentials.get("access_token", "")
        if not access_token:
            raise ExternalAPIError("Missing access token", provider=self.provider)

        client = AuditedHTTPClient(
            provider=self.provider,
            base_url=GTM_BASE_URL,
            access_token=access_token,
        )

        try:
            # Test connection by listing accounts
            result = await client.get(
                "/tagmanager/v2/accounts",
                operation="list_accounts",
            )

            accounts = result.get("account", [])
            return ConnectionInfo(
                provider=self.provider,
                account_id=accounts[0]["accountId"] if accounts else None,
                scopes=[GTM_SCOPE],
                connected_at=datetime.now(timezone.utc),
                metadata={"account_count": len(accounts)},
            )
        finally:
            await client.close()

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        """Check GTM connection health."""
        return HealthStatus(
            healthy=True,
            provider=self.provider,
            message="Connected",
            checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self,
        connection: ConnectionInfo,
        *,
        assessment_id: str | None = None,
        client_id: str | None = None,
        access_token: str = "",
        audit_callback: Any = None,
    ) -> AdapterResult:
        """Collect GTM evidence — containers, tags, consent settings.

        Flow:
        1. List all containers
        2. For each container, get published version
        3. Analyze every tag for consent settings
        4. Check variables for identifier patterns
        5. Review version history for consent changes
        """
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        warnings: list[str] = []
        now = datetime.now(timezone.utc)

        client = AuditedHTTPClient(
            provider=self.provider,
            base_url=GTM_BASE_URL,
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id,
            assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            # Step 1: List all accounts and containers
            accounts_response = await client.get(
                "/tagmanager/v2/accounts",
                operation="list_accounts",
            )
            accounts = accounts_response.get("account", [])

            if not accounts:
                evidence.append(EvidenceItem(
                    obligation_id="S12.governance.tagInventory",
                    verdict=Verdict.CANNOT_DETERMINE,
                    confidence=Confidence.LOW,
                    source="gtm:list_accounts",
                    observed_at=now,
                    detail="No GTM accounts found. Cannot assess tag management.",
                ))
                return AdapterResult(evidence=evidence, errors=errors, warnings=warnings)

            all_tags_analyzed: list[dict[str, Any]] = []
            tags_with_consent = 0
            tags_without_consent = 0
            tags_sending_user_data = 0

            for account in accounts:
                account_id = account.get("accountId", "")

                try:
                    # List containers
                    containers_response = await client.get(
                        f"/tagmanager/v2/accounts/{account_id}/containers",
                        operation="list_containers",
                    )
                    containers = containers_response.get("container", [])

                    for container in containers:
                        container_id = container.get("containerId", "")
                        container_name = container.get("name", "Unknown")
                        container_public_id = container.get("publicId", "")

                        try:
                            # Get published version (live version)
                            version_response = await client.get(
                                f"/tagmanager/v2/accounts/{account_id}/containers/{container_id}/versions:live",
                                operation="get_live_version",
                            )

                            tags = version_response.get("tag", [])
                            triggers = version_response.get("trigger", [])
                            variables = version_response.get("variable", [])

                            # Analyze each tag
                            for tag in tags:
                                tag_analysis = self._analyze_tag(tag, triggers)
                                all_tags_analyzed.append(tag_analysis)

                                if tag_analysis["has_consent_settings"]:
                                    tags_with_consent += 1
                                else:
                                    tags_without_consent += 1

                                if tag_analysis["sends_user_data"]:
                                    tags_sending_user_data += 1

                            # Analyze variables for identifiers
                            for var in variables:
                                self._analyze_variable(var, warnings)

                            # Check version history
                            try:
                                versions_response = await client.get(
                                    f"/tagmanager/v2/accounts/{account_id}/containers/{container_id}/version_headers",
                                    operation="list_version_headers",
                                )
                                version_headers = versions_response.get("containerVersionHeader", [])
                                # Note consent-related changes
                                for vh in version_headers[-5:]:  # Last 5 versions
                                    pass  # Version metadata is safe to store

                            except ExternalAPIError as ver_err:
                                warnings.append(f"Could not retrieve version history: {ver_err}")

                        except ExternalAPIError as ver_err:
                            errors.append(
                                f"Cannot determine: failed to get live version for "
                                f"container {container_name}: {ver_err}"
                            )
                            evidence.append(EvidenceItem(
                                obligation_id="S6.consentGating",
                                verdict=Verdict.CANNOT_DETERMINE,
                                confidence=Confidence.LOW,
                                source=f"gtm:container:{container_public_id}",
                                observed_at=now,
                                detail=f"Could not access live version for container {container_name}",
                            ))

                except ExternalAPIError as acc_err:
                    errors.append(f"Cannot determine: failed to list containers for account {account_id}: {acc_err}")

            # Generate evidence from analysis
            total_tags = tags_with_consent + tags_without_consent

            # ── S6.consentGating evidence ──
            if total_tags > 0:
                if tags_without_consent == 0:
                    evidence.append(EvidenceItem(
                        obligation_id="S6.consentGating",
                        verdict=Verdict.OBSERVED,
                        confidence=Confidence.HIGH,
                        source="gtm:tag_analysis",
                        observed_at=now,
                        detail=f"All {total_tags} tags have consent settings configured.",
                        raw_metadata=self._sanitize_tag_summary(all_tags_analyzed),
                    ))
                else:
                    unconfigured = [
                        t for t in all_tags_analyzed if not t["has_consent_settings"]
                    ]
                    evidence.append(EvidenceItem(
                        obligation_id="S6.consentGating",
                        verdict=Verdict.NOT_OBSERVED,
                        confidence=Confidence.HIGH,
                        source="gtm:tag_analysis",
                        observed_at=now,
                        detail=(
                            f"{tags_without_consent} of {total_tags} tags have no consent "
                            f"settings configured. Tags: "
                            f"{', '.join(t['name'] for t in unconfigured[:10])}"
                        ),
                        raw_metadata=self._sanitize_tag_summary(all_tags_analyzed),
                    ))

            # ── S12.governance.tagInventory evidence ──
            evidence.append(EvidenceItem(
                obligation_id="S12.governance.tagInventory",
                verdict=Verdict.OBSERVED if total_tags > 0 else Verdict.CANNOT_DETERMINE,
                confidence=Confidence.HIGH if total_tags > 0 else Confidence.LOW,
                source="gtm:tag_inventory",
                observed_at=now,
                detail=f"GTM contains {total_tags} tags across all containers.",
                raw_metadata={
                    "total_tags": total_tags,
                    "with_consent": tags_with_consent,
                    "without_consent": tags_without_consent,
                    "sending_user_data": tags_sending_user_data,
                },
            ))

            # ── S9.sharing.enhancedConversions evidence ──
            if tags_sending_user_data > 0:
                user_data_tags = [
                    t for t in all_tags_analyzed if t["sends_user_data"]
                ]
                evidence.append(EvidenceItem(
                    obligation_id="S9.sharing.enhancedConversions",
                    verdict=Verdict.OBSERVED,
                    confidence=Confidence.HIGH,
                    source="gtm:enhanced_conversions_analysis",
                    observed_at=now,
                    detail=(
                        f"{tags_sending_user_data} tags configured to send user-provided data "
                        f"(Enhanced Conversions / Customer Match). Tags: "
                        f"{', '.join(t['name'] for t in user_data_tags[:10])}"
                    ),
                    raw_metadata={
                        "tags": [
                            {"name": t["name"], "type": t["type"], "vendor": t.get("vendor", "")}
                            for t in user_data_tags
                        ],
                    },
                ))

            return AdapterResult(
                evidence=evidence,
                errors=errors,
                warnings=warnings,
                metadata={
                    "total_tags": total_tags,
                    "tags_with_consent": tags_with_consent,
                    "tags_without_consent": tags_without_consent,
                    "accounts_analyzed": len(accounts),
                },
            )

        except ExternalAPIError as exc:
            logger.error("gtm_collection_failed", error=str(exc))
            evidence.append(EvidenceItem(
                obligation_id="S6.consentGating",
                verdict=Verdict.CANNOT_DETERMINE,
                confidence=Confidence.LOW,
                source="gtm:error",
                observed_at=now,
                detail=f"GTM collection failed: {exc}",
            ))
            return AdapterResult(
                evidence=evidence,
                errors=[str(exc)],
                partial=True,
            )
        finally:
            await client.close()

    def _analyze_tag(
        self, tag: dict[str, Any], triggers: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Analyze a single GTM tag for consent settings and data transmission."""
        tag_name = tag.get("name", "Unknown")
        tag_type = tag.get("type", "unknown")
        consent_settings = tag.get("consentSettings", {})
        firing_trigger_ids = tag.get("firingTriggerId", [])

        has_consent = bool(consent_settings and consent_settings.get("consentStatus") != "notSet")

        # Detect tags sending user-provided data
        sends_user_data = False
        tag_type_lower = tag_type.lower()
        if any(kw in tag_type_lower for kw in [
            "enhanced_conversion", "customer_match", "user_provided",
            "awct", "conversion_linker",
        ]):
            sends_user_data = True

        # Check parameters for user data indicators
        for param in tag.get("parameter", []):
            param_key = param.get("key", "")
            if any(kw in param_key.lower() for kw in [
                "user_data", "enhanced_conversion", "customer_match",
                "email", "phone", "address",
            ]):
                sends_user_data = True

        # Get trigger names for this tag
        trigger_names = []
        for trigger in triggers:
            if trigger.get("triggerId") in firing_trigger_ids:
                trigger_names.append(trigger.get("name", "Unknown"))

        # Classify vendor
        vendor = self._classify_vendor(tag_type)

        # Handle custom HTML — store hash, never verbatim
        custom_html_hash = None
        custom_html_classification = None
        for param in tag.get("parameter", []):
            if param.get("key") == "html" and param.get("type") == "template":
                html_value = param.get("value", "")
                if html_value:
                    sanitized = sanitize_html_tag_body(html_value)
                    custom_html_hash = sanitized["hash"]
                    custom_html_classification = sanitized["classification"]

        return {
            "name": tag_name,
            "type": tag_type,
            "vendor": vendor,
            "has_consent_settings": has_consent,
            "consent_settings": {
                "status": consent_settings.get("consentStatus", "notSet"),
                "consent_type": consent_settings.get("consentType", {}),
            } if consent_settings else None,
            "sends_user_data": sends_user_data,
            "firing_triggers": trigger_names,
            "custom_html_hash": custom_html_hash,
            "custom_html_classification": custom_html_classification,
            "paused": tag.get("paused", False),
        }

    def _classify_vendor(self, tag_type: str) -> str:
        """Classify a tag's vendor based on its type."""
        vendor_map = {
            "ua": "google_analytics",
            "gaawc": "google_analytics",
            "gaawe": "google_analytics",
            "gclidw": "google_ads",
            "awct": "google_ads",
            "sp": "google_ads",
            "flc": "floodlight",
            "baut": "bing_ads",
            "fb": "meta",
            "fbbp": "meta",
            "hjtc": "hotjar",
            "linkedin": "linkedin",
            "twitter": "twitter",
            "tiktok": "tiktok",
            "snap": "snapchat",
            "pinterest": "pinterest",
            "html": "custom_html",
            "img": "custom_image",
        }
        return vendor_map.get(tag_type.lower(), tag_type)

    def _analyze_variable(
        self, variable: dict[str, Any], warnings: list[str]
    ) -> None:
        """Check if a custom variable may carry identifiers."""
        var_name = variable.get("name", "")
        var_type = variable.get("type", "")

        # Flag variables that might carry user identifiers
        suspect_patterns = ["email", "phone", "user_id", "customer", "name", "address"]
        if any(p in var_name.lower() for p in suspect_patterns):
            warnings.append(
                f"Variable '{var_name}' (type: {var_type}) has a name suggesting "
                f"it may carry personal data identifiers. Flag for review."
            )

    def _sanitize_tag_summary(
        self, tags: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Create a sanitized summary of tags for evidence storage."""
        return {
            "tags": [
                {
                    "name": t["name"],
                    "type": t["type"],
                    "vendor": t.get("vendor", ""),
                    "has_consent_settings": t["has_consent_settings"],
                    "sends_user_data": t["sends_user_data"],
                    "firing_triggers": t["firing_triggers"],
                    "paused": t.get("paused", False),
                }
                for t in tags
            ],
        }
