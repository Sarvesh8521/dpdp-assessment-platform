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

logger = get_logger(__name__)

GOOGLE_ADS_FORBIDDEN_ENDPOINTS = [
    # User list membership — NEVER read contents
    "/v18/customers/*/userLists/*/members",
    "userListMembers",
    "offlineUserDataJobs",  # Upload jobs contain PII
    "/v18/customers/*/offlineUserDataJobs",
    "mutate",  # Any write operation
    "uploadUserData",
    "getUserListMembership",
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
        return ["https://www.googleapis.com/auth/adwords.readonly"]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("google_ads")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "https://www.googleapis.com/auth/adwords.readonly": [
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

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider,
            scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
            metadata=credentials.get("metadata", {}),
        )

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        return HealthStatus(
            healthy=True, provider=self.provider,
            message="Connected", checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
        customer_id: str = "",
    ) -> AdapterResult:
        """Collect Google Ads metadata — Customer Match, conversions, audience sharing."""
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc)

        client = AuditedHTTPClient(
            provider=self.provider,
            base_url="https://googleads.googleapis.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            # Query Customer Match user lists (metadata only)
            try:
                query = (
                    "SELECT user_list.name, user_list.size_for_display, "
                    "user_list.type, user_list.membership_life_span, "
                    "user_list.size_range_for_display, user_list.description "
                    "FROM user_list WHERE user_list.type = 'CRM_BASED'"
                )
                # Google Ads API uses POST for search queries (read-only GAQL)
                search_result = await client.get(
                    f"/v18/customers/{customer_id}/googleAds:searchStream",
                    params={"query": query},
                    operation="search_customer_match_lists",
                )

                user_lists = []
                for batch in search_result.get("results", [search_result]):
                    for row in batch if isinstance(batch, list) else [batch]:
                        ul = row.get("userList", {})
                        if ul:
                            user_lists.append({
                                "name": ul.get("name", ""),
                                "size_display": ul.get("sizeForDisplay", ""),
                                "type": ul.get("type", ""),
                                "membership_lifespan": ul.get("membershipLifeSpan", ""),
                                "size_range": ul.get("sizeRangeForDisplay", ""),
                            })

                if user_lists:
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.audienceUpload",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="google_ads:customer_match", observed_at=now,
                        detail=(
                            f"Found {len(user_lists)} Customer Match lists. "
                            f"Each requires a recorded consent basis. "
                            f"Lists: {', '.join(ul['name'] for ul in user_lists[:10])}"
                        ),
                        raw_metadata={"lists": user_lists},
                    ))
                else:
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.audienceUpload",
                        verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
                        source="google_ads:customer_match", observed_at=now,
                        detail="No Customer Match lists found.",
                    ))
            except ExternalAPIError as exc:
                errors.append(f"Customer Match query failed: {exc}")

            # Check conversion actions for Enhanced Conversions
            try:
                conv_query = (
                    "SELECT conversion_action.name, conversion_action.type, "
                    "conversion_action.category, conversion_action.status "
                    "FROM conversion_action"
                )
                conv_result = await client.get(
                    f"/v18/customers/{customer_id}/googleAds:searchStream",
                    params={"query": conv_query},
                    operation="search_conversion_actions",
                )

                enhanced_conv_found = False
                for batch in conv_result.get("results", [conv_result]):
                    for row in batch if isinstance(batch, list) else [batch]:
                        ca = row.get("conversionAction", {})
                        if "ENHANCED" in str(ca.get("type", "")).upper():
                            enhanced_conv_found = True

                if enhanced_conv_found:
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.enhancedConversions",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="google_ads:enhanced_conversions", observed_at=now,
                        detail="Enhanced Conversions enabled — hashed personal data transmitted.",
                    ))
            except ExternalAPIError as exc:
                errors.append(f"Conversion actions query failed: {exc}")

            return AdapterResult(evidence=evidence, errors=errors)

        except ExternalAPIError as exc:
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S9.sharing.audienceUpload",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="google_ads:error", observed_at=now,
                    detail=f"Google Ads collection failed: {exc}",
                )],
                errors=[str(exc)], partial=True,
            )
        finally:
            await client.close()
