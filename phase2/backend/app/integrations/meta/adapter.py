"""Meta (Facebook) adapter. PHASE2_SPEC Section 4.4.

Marketing API, read-only. Requires app review and Business Manager access.
NEVER read: audience membership, event payloads, user identifiers.
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

META_FORBIDDEN_ENDPOINTS = [
    "/act_*/customaudiences/*/users",
    "customaudiences/users",
    "/act_*/events",
    "/*/activities",
    "/*/user",
    "audience_membership",
    "user_identifiers",
    "event_payloads",
    "offlineconversions",
]


class MetaAdapter(IntegrationAdapter):
    @property
    def id(self) -> str:
        return "meta"

    @property
    def provider(self) -> str:
        return "meta_marketing"

    @property
    def required_scopes(self) -> list[str]:
        return ["ads_read", "business_management"]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("meta")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "ads_read": ["S9.sharing.audienceUpload", "S9.sharing.advancedMatching"],
            "business_management": ["S9.sharing.documented"],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return META_FORBIDDEN_ENDPOINTS

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
            metadata={"app_review_status": credentials.get("app_review_status", "pending")},
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
        ad_account_id: str = "",
    ) -> AdapterResult:
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc)
        
        # Meta UI selects a resource like 'act_<id>' or just '<id>' as ad_account_id
        # We need the ad account id clean and we need the business id if available
        meta = connection.metadata or {}
        business_id = meta.get("business_id", "")
        clean_ad_account = ad_account_id.replace("act_", "") if ad_account_id else ""
        if not clean_ad_account:
            clean_ad_account = meta.get("ad_account_id", "").replace("act_", "")
            
        client = AuditedHTTPClient(
            provider=self.provider,
            base_url="https://graph.facebook.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            # 1. Custom Audiences — metadata only
            if clean_ad_account:
                try:
                    audiences = await client.get(
                        f"/v21.0/act_{clean_ad_account}/customaudiences",
                        params={"fields": "name,subtype,approximate_count,data_source,delivery_status,operation_status,customer_file_source,permission_for_actions"},
                        operation="list_custom_audiences",
                    )

                    customer_list_audiences = []
                    website_audiences = []

                    for audience in audiences.get("data", []):
                        subtype = audience.get("subtype", "")
                        audience_info = {
                            "name": audience.get("name", ""),
                            "subtype": subtype,
                            "approximate_count": audience.get("approximate_count", 0),
                            "data_source": audience.get("data_source", {}).get("type", ""),
                            "origin": audience.get("customer_file_source", "UNKNOWN"),
                            "sharing": audience.get("permission_for_actions", {}),
                        }

                        if subtype in ("CUSTOM", "LOOKALIKE") or "customer" in subtype.lower():
                            customer_list_audiences.append(audience_info)
                        else:
                            website_audiences.append(audience_info)

                    if customer_list_audiences:
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.audienceUpload",
                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                            source="meta:custom_audiences", observed_at=now,
                            detail=(
                                f"Found {len(customer_list_audiences)} customer-list Custom Audiences. "
                                f"Each requires a recorded consent basis."
                            ),
                            raw_metadata={"audiences": customer_list_audiences},
                        ))
                except ExternalAPIError as exc:
                    errors.append(f"Custom Audiences query failed: {exc}")

                # 2. Pixel configuration & Conversions API
                try:
                    pixel = await client.get(
                        f"/v21.0/act_{clean_ad_account}/adspixels",
                        params={"fields": "name,automatic_matching_fields,first_party_cookie_status,is_created_by_business_manager,creation_time"},
                        operation="get_pixel_config",
                    )
                    pixels = pixel.get("data", [])
                    for px in pixels:
                        matching_fields = px.get("automatic_matching_fields", [])
                        if matching_fields:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.advancedMatching",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source="meta:advanced_matching", observed_at=now,
                                detail=(
                                    f"Advanced Matching enabled with fields: {', '.join(matching_fields)}. "
                                    f"Hashed personal data is being transmitted."
                                ),
                            ))
                        
                        # Conversions API Configuration indicator
                        # While true CAPI requires checking server events, we infer its presence
                        # or at least flag it as a risk area if pixels are present.
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.serverSide",
                            verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                            source="meta:conversions_api", observed_at=now,
                            detail=f"Meta Pixel ({px.get('name')}) is active. Check if Conversions API (server-side data transmission) is configured.",
                            raw_metadata={"pixel_id": px.get("id"), "first_party_cookie_status": px.get("first_party_cookie_status")}
                        ))
                except ExternalAPIError as exc:
                    errors.append(f"Pixel config query failed: {exc}")

            # 3. Business Manager data sharing & Offline Events
            if business_id:
                # Agencies & Client Businesses
                try:
                    agencies = await client.get(
                        f"/v21.0/{business_id}/agencies",
                        params={"fields": "id,name"},
                        operation="get_business_agencies",
                    )
                    clients = await client.get(
                        f"/v21.0/{business_id}/client_businesses",
                        params={"fields": "id,name"},
                        operation="get_client_businesses",
                    )
                    
                    shared_entities = agencies.get("data", []) + clients.get("data", [])
                    if shared_entities:
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.documented",
                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                            source="meta:business_sharing", observed_at=now,
                            detail=f"Business Manager shares data with {len(shared_entities)} external agencies/businesses.",
                            raw_metadata={"shared_businesses": shared_entities}
                        ))
                except ExternalAPIError as exc:
                    errors.append(f"Business sharing query failed: {exc}")

                # Offline Event Sets
                try:
                    offline_sets = await client.get(
                        f"/v21.0/{business_id}/offline_conversion_data_sets",
                        params={"fields": "id,name,creation_time"},
                        operation="get_offline_events",
                    )
                    sets_data = offline_sets.get("data", [])
                    if sets_data:
                        evidence.append(EvidenceItem(
                            obligation_id="S9.sharing.audienceUpload",
                            verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                            source="meta:offline_events", observed_at=now,
                            detail=f"Found {len(sets_data)} Offline Event Sets. Requires a documented consent basis.",
                            raw_metadata={"offline_event_sets": sets_data}
                        ))
                except ExternalAPIError as exc:
                    errors.append(f"Offline events query failed: {exc}")

            return AdapterResult(evidence=evidence, errors=errors)

        except ExternalAPIError as exc:
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S9.sharing.audienceUpload",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="meta:error", observed_at=now,
                    detail=f"Meta collection failed: {exc}",
                )],
                errors=[str(exc)], partial=True,
            )
        finally:
            await client.close()
