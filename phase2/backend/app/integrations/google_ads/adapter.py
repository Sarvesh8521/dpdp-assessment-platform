"""Google Ads adapter.

PHASE2_SPEC Section 4.3.

Read-only via manager account link.
NEVER read user list contents. Metadata only.

Legacy Customer Match upload path was discontinued April 2026 — flag any client still attempting it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domain.evidence import Confidence, EvidenceItem, Verdict
from app.domain.obligations import get_obligations_for_integration
from app.integrations.base.adapter import (
    AdapterResult, ConnectionInfo, HealthStatus, IntegrationAdapter,
)
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError
from app.domain.evidence import StructuredEvidence

logger = get_logger(__name__)

GOOGLE_ADS_FORBIDDEN_ENDPOINTS = [
    "/userLists",
    "/offlineUserDataJobs"
]


class GoogleAdsAdapter(IntegrationAdapter):
    """Google Ads integration — Customer Match metadata, conversions, audience sharing."""

    @property
    def id(self) -> str:
        return "google_ads"

    @property
    def provider(self) -> str:
        return "google_ads"

    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/adwords"]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("google_ads")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "https://www.googleapis.com/auth/adwords": [
                "S9.sharing.audienceUpload",
                "S9.sharing.enhancedConversions",
                "S9.sharing.documented",
                "S7.purposeLimitation",
                "S8.dataMinimization",
            ],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return GOOGLE_ADS_FORBIDDEN_ENDPOINTS

    async def connect(self, credentials: dict[str, Any], audit_callback: Any) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider,
            scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
            metadata=credentials.get("metadata", {}),
        )

    async def health_check(
        self,
        connection: ConnectionInfo,
        audit_callback: Any,
        *,
        client_id: str | None = None,
        assessment_id: str | None = None,
    ) -> HealthStatus:
        # Note: True health check is implemented in integrations.py because it requires fetching tokens
        return HealthStatus(
            healthy=True, provider=self.provider,
            message="Connected", checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self, connection: ConnectionInfo, audit_callback: Any, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", 
        customer_id: str = "",
    ) -> AdapterResult:
        """Collect Google Ads metadata — Customer Match, conversions, audience sharing."""
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc)

        developer_token = connection.metadata.get("developer_token", "")
        if not developer_token:
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S9.sharing.audienceUpload",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="google_ads:error", observed_at=now,
                    detail="Missing Google Ads developer token. Cannot determine compliance.",
                    structured_detail=StructuredEvidence(
                        source="google_ads", integration="google_ads", operation="auth",
                        resource_type="customer", resource_id=customer_id,
                        location="Unavailable",
                        observed_at=now, observed_state="Missing developer token",
                        expected_state="Valid developer token",
                        detail="Missing Google Ads developer token.",
                        reason="A developer token is required for Google Ads API access.",
                    ),
                )],
                errors=["Missing developer token"], partial=True,
            )

        customer_id = customer_id.replace("-", "") if customer_id else ""
        headers = {
            "developer-token": developer_token,
            "login-customer-id": customer_id
        }

        client = AuditedHTTPClient(
            provider=self.provider, audit_callback=audit_callback, base_url="https://googleads.googleapis.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            )

        async def run_gaql(query: str, op_name: str) -> list[Any]:
            search_result = await client.post(
                f"/v18/customers/{customer_id}/googleAds:searchStream",
                json_body={"query": query},
                headers=headers,
                operation=op_name,
            )
            rows = []
            for batch in search_result if isinstance(search_result, list) else [search_result]:
                for row in batch.get("results", []) if isinstance(batch, dict) else (batch if isinstance(batch, list) else []):
                    if isinstance(row, list):
                        rows.extend(row)
                    else:
                        rows.append(row)
            return rows

        try:
            # Query Customer Match user lists (metadata only)
            try:
                query = (
                    "SELECT user_list.name, user_list.size_for_display, "
                    "user_list.type, user_list.membership_life_span, "
                    "user_list.size_range_for_display, user_list.description "
                    "FROM user_list WHERE user_list.type = 'CRM_BASED'"
                )
                rows = await run_gaql(query, "search_customer_match_lists")

                user_lists = []
                for row in rows:
                    ul = row.get("userList", {})
                    if ul:
                        user_lists.append({
                            "name": ul.get("name", ""),
                            "size_display": ul.get("sizeForDisplay", ""),
                            "type": ul.get("type", ""),
                            "membership_lifespan": ul.get("membershipLifeSpan", ""),
                            "size_range": ul.get("sizeRangeForDisplay", ""),
                            "upload_date": "Cannot determine (not exposed safely via API)"
                        })

                if user_lists:
                    for ul in user_lists:
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.audienceUpload",
                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                            source=f"google_ads:customer_match:{ul['name']}", observed_at=now,
                            detail=f"Customer Match list '{ul['name']}' requires a recorded consent basis.",
                            structured_detail=StructuredEvidence(
                                source="google_ads", integration="google_ads", operation="search_customer_match_lists",
                                resource_type="customer_list", resource_id=customer_id, resource_name=ul["name"],
                                location=f"Customer: {customer_id} > UserList: {ul.get('id', '')} ({ul['name']})",
                                observed_at=now, observed_state="CRM_BASED user list found", expected_state="Consent documented for upload",
                                detail=f"Customer Match list '{ul['name']}' requires a recorded consent basis.",
                                reason="The CRM_BASED user list was found in the Google Ads account, necessitating a lawful basis for audience upload.",
                                metadata={"list_metadata": ul}
                            )
                        ))
                else:
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.audienceUpload",
                        verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
                        source="google_ads:customer_match", observed_at=now,
                        detail="No Customer Match lists found.",
                        structured_detail=StructuredEvidence(
                            source="google_ads", integration="google_ads", operation="search_customer_match_lists",
                            resource_type="customer_list", resource_id=customer_id,
                            location=f"Customer: {customer_id}",
                            observed_at=now, observed_state="0 CRM_BASED lists", expected_state="N/A",
                            detail="No Customer Match lists found.",
                            reason="No CRM_BASED user lists were observed, so no audience upload obligations were triggered.",
                        )
                    ))
            except ExternalAPIError as exc:
                if exc.status_code in (401, 403): raise
                errors.append(f"Customer Match query failed: {exc}")

            # Check conversion actions for Enhanced Conversions
            try:
                conv_query = (
                    "SELECT conversion_action.name, conversion_action.type, "
                    "conversion_action.category, conversion_action.status "
                    "FROM conversion_action"
                )
                rows = await run_gaql(conv_query, "search_conversion_actions")

                enhanced_conv_found = False
                for row in rows:
                    ca = row.get("conversionAction", {})
                    if "ENHANCED" in str(ca.get("type", "")).upper():
                        enhanced_conv_found = True
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.enhancedConversions",
                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                            source=f"google_ads:enhanced_conversions:{ca.get('name', 'unknown')}", observed_at=now,
                            detail=f"Enhanced Conversions enabled for '{ca.get('name', 'unknown')}' — hashed personal data transmitted.",
                            structured_detail=StructuredEvidence(
                                source="google_ads", integration="google_ads", operation="search_conversion_actions",
                                resource_type="conversion_action", resource_name=ca.get("name", ""),
                                location=f"Customer: {customer_id} > ConversionAction: {ca.get('id', '')} ({ca.get('name', '')})",
                                observed_at=now, observed_state=ca.get("type", ""), expected_state="Consent documented for enhanced conversions",
                                detail=f"Enhanced Conversions enabled for '{ca.get('name', 'unknown')}'.",
                                reason="The conversion action is configured as ENHANCED, indicating that hashed personal data is transmitted.",
                                metadata={"conversion_action": ca}
                            )
                        ))
            except ExternalAPIError as exc:
                if exc.status_code in (401, 403): raise
                errors.append(f"Conversion actions query failed: {exc}")

            # Check Consent Mode / Customer Settings
            try:
                consent_query = (
                    "SELECT customer.id, customer.remarketing_setting.google_global_site_tag, "
                    "customer.consent_setting.ad_user_data, customer.consent_setting.ad_personalization "
                    "FROM customer LIMIT 1"
                )
                rows = await run_gaql(consent_query, "search_customer_settings")
                
                if rows:
                    c = rows[0].get("customer", {})
                    rs = c.get("remarketingSetting", {})
                    cs = c.get("consentSetting", {})
                    
                    if rs.get("googleGlobalSiteTag"):
                        evidence.append(EvidenceItem(
                            obligation_id="S7.purposeLimitation",
                            verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                            source="google_ads:remarketing", observed_at=now,
                            detail="Global site tag remarketing setting is enabled.",
                            structured_detail=StructuredEvidence(
                                source="google_ads", integration="google_ads", operation="search_customer_settings",
                                resource_type="customer_setting", resource_id=c.get("id"),
                                resource_name=f"Customer {customer_id}",
                                location=f"Customer: {customer_id} > Remarketing Setting",
                                observed_at=now, observed_state="Remarketing enabled", expected_state="Purpose specified",
                                detail="Global site tag remarketing setting is enabled.",
                                reason="Global site tag remarketing setting is enabled, requiring specific purpose declarations.",
                            )
                        ))
                        
                    # Consent Mode Diagnostics
                    if cs:
                        ad_user_data = cs.get("adUserData", "Cannot determine")
                        ad_personalization = cs.get("adPersonalization", "Cannot determine")
                        evidence.append(EvidenceItem(
                            obligation_id="S7.purposeLimitation",
                            verdict=Verdict.OBSERVED if ad_user_data != "Cannot determine" else Verdict.CANNOT_DETERMINE,
                            confidence=Confidence.HIGH if ad_user_data != "Cannot determine" else Confidence.LOW,
                            source="google_ads:consent_mode", observed_at=now,
                            detail=f"Consent Mode settings - Ad User Data: {ad_user_data}, Ad Personalization: {ad_personalization}",
                            structured_detail=StructuredEvidence(
                                source="google_ads", integration="google_ads", operation="search_customer_settings",
                                resource_type="customer_setting", resource_id=c.get("id"),
                                location=f"Customer: {customer_id} > Consent Setting",
                                observed_at=now, observed_state=f"adUserData={ad_user_data}", expected_state="Configured properly",
                                detail=f"Consent Mode settings - Ad User Data: {ad_user_data}, Ad Personalization: {ad_personalization}",
                                reason="Consent Mode settings indicate specific ad user data and personalization signals.",
                                metadata={"consent_setting": cs}
                            )
                        ))
                    else:
                        evidence.append(EvidenceItem(
                            obligation_id="S7.purposeLimitation",
                            verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                            source="google_ads:consent_mode", observed_at=now,
                            detail="Consent Mode diagnostics not exposed or unconfigured.",
                            structured_detail=StructuredEvidence(
                                source="google_ads", integration="google_ads", operation="search_customer_settings",
                                resource_type="customer_setting", resource_id=c.get("id"),
                                resource_name=f"Customer {customer_id}",
                                location=f"Customer: {customer_id} > Consent Setting",
                                observed_at=now, observed_state="Unknown", expected_state="Configured properly",
                                detail="Consent Mode diagnostics not exposed or unconfigured.",
                                reason="Consent Mode diagnostics not exposed by the Google Ads API for this customer.",
                            )
                        ))
            except ExternalAPIError as exc:
                if exc.status_code in (401, 403): raise
                errors.append(f"Customer settings query failed: {exc}")
                
            # Check Data Manager / Client Links
            try:
                link_query = (
                    "SELECT customer_client_link.status, customer_client_link.client_customer "
                    "FROM customer_client_link WHERE customer_client_link.status = 'ACTIVE'"
                )
                rows = await run_gaql(link_query, "search_customer_client_links")
                
                links = [row.get("customerClientLink", {}).get("clientCustomer", "") for row in rows]
                if links:
                    for link in links:
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.documented",
                            verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                            source=f"google_ads:client_links:{link}", observed_at=now,
                            detail=f"Found active customer client link: {link}. Data Manager or sub-account sharing might be active.",
                            structured_detail=StructuredEvidence(
                                source="google_ads", integration="google_ads", operation="search_customer_client_links",
                                resource_type="customer_client_link", resource_id=link,
                                location=f"Customer: {customer_id} > Client Link: {link}",
                                observed_at=now, observed_state="ACTIVE", expected_state="Sharing documented",
                                detail=f"Found active customer client link: {link}.",
                                reason="An active customer client link exists, indicating potential Data Manager usage or audience sharing."
                            )
                        ))
            except ExternalAPIError as exc:
                if exc.status_code in (401, 403): raise
                errors.append(f"Client links query failed: {exc}")
                
            # Check Legacy Customer Match uploads
            evidence.append(EvidenceItem(
                obligation_id="S9.sharing.audienceUpload",
                verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                source="google_ads:legacy_upload", observed_at=now,
                detail="Legacy Customer Match upload path (offlineUserDataJobs) cannot be safely audited without risking PII exposure.",
                structured_detail=StructuredEvidence(
                    source="google_ads", integration="google_ads", operation="evaluate_legacy_upload",
                    resource_type="offline_user_data_job", resource_id=customer_id,
                    location=f"Customer: {customer_id} > OfflineUserDataJob",
                    observed_at=now, observed_state="Cannot determine", expected_state="Not using legacy uploads",
                    detail="Legacy Customer Match upload path (offlineUserDataJobs) cannot be safely audited without risking PII exposure.",
                    reason="Reading offlineUserDataJobs would risk exposing PII; endpoint is forbidden.",
                )
            ))

            return AdapterResult(evidence=evidence, errors=errors)

        except ExternalAPIError as exc:
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S9.sharing.audienceUpload",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="google_ads:error", observed_at=now,
                    detail=f"Google Ads collection failed: {exc}",
                    structured_detail=StructuredEvidence(
                        source="google_ads", integration="google_ads", operation="search_customer_match_lists",
                        resource_type="customer", resource_id=customer_id,
                        location="Unavailable",
                        observed_at=now, observed_state="Collection error",
                        expected_state="Successful collection",
                        detail=f"Google Ads collection failed: {exc}",
                        reason=f"API error: {exc}",
                    ),
                )],
                errors=[str(exc)], partial=True,
            )
        finally:
            await client.close()
