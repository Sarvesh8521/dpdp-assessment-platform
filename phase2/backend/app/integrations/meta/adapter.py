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
from app.domain.evidence import StructuredEvidence

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

        if not clean_ad_account and not business_id:
            for obl_id in self.obligations_answered:
                evidence.append(EvidenceItem(
                    obligation_id=obl_id,
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="meta:error", observed_at=now,
                    detail="Meta connection is active but no Business resource is selected.",
                    structured_detail=StructuredEvidence(
                        source="meta", integration="meta", operation="connect",
                        resource_type="ad_account",
                        location="Unavailable",
                        observed_at=now, observed_state="No resource selected",
                        expected_state="Resource mapped",
                        detail="Meta connection is active but no Business resource is selected.",
                        reason="Configuration error",
                    ),
                ))
            errors.append("No resource selected")
            return AdapterResult(evidence=evidence, errors=errors)

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
                        for audience in customer_list_audiences:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.audienceUpload",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source=f"meta:custom_audience:{audience['name']}", observed_at=now,
                                detail=f"Found customer-list Custom Audience '{audience['name']}'. Requires a recorded consent basis.",
                                structured_detail=StructuredEvidence(
                                    source="meta", integration="meta", operation="list_custom_audiences",
                                    resource_type="custom_audience", resource_id=audience.get("id", f"act_{clean_ad_account}"), resource_name=audience["name"],
                                    location=f"Business: {business_id or 'Unavailable'} > Audience: {audience.get('id', 'Unknown')} ({audience.get('name', 'Unknown')})",
                                    observed_at=now, observed_state="Customer-list Audience", expected_state="Documented consent basis",
                                    detail=f"Found customer-list Custom Audience '{audience['name']}'.",
                                    metadata={"audience_info": audience}
                                )
                            ))
                except ExternalAPIError as exc:
                    errors.append(f"Custom Audiences query failed: {exc}")
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.audienceUpload",
                        verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                        source="meta:error:custom_audiences", observed_at=now,
                        detail=f"Failed to read Custom Audiences: {exc}",
                        structured_detail=StructuredEvidence(
                            source="meta", integration="meta", operation="list_custom_audiences",
                            resource_type="ad_account", resource_id=f"act_{clean_ad_account}" if clean_ad_account else None,
                            location="Unavailable",
                            observed_at=now, observed_state="Collection error",
                            expected_state="Successful collection",
                            detail=f"Custom Audiences query failed: {exc}",
                            reason=f"API error: {exc}",
                        ),
                    ))

                # 2. Pixel configuration & Conversions API
                try:
                    pixel = await client.get(
                        f"/v21.0/act_{clean_ad_account}/adspixels",
                        params={"fields": "name,automatic_matching_fields,first_party_cookie_status,is_created_by_business_manager,creation_time,is_server_events_enabled,server_events_enabled"},
                        operation="get_pixel_config",
                    )
                    pixels = pixel.get("data", [])
                    for px in pixels:
                        matching_fields = px.get("automatic_matching_fields", [])
                        if matching_fields:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.advancedMatching",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source=f"meta:advanced_matching:{px.get('name')}", observed_at=now,
                                detail=f"Advanced Matching enabled with fields: {', '.join(matching_fields)}. Hashed personal data is being transmitted.",
                                structured_detail=StructuredEvidence(
                                    source="meta", integration="meta", operation="get_pixel_config",
                                    resource_type="pixel", resource_id=px.get("id"), resource_name=px.get("name"),
                                    location=f"Business: {business_id or 'Unavailable'} > Pixel: {px.get('id', 'Unknown')} ({px.get('name', 'Unknown')})",
                                    observed_at=now, observed_state="Advanced Matching enabled", expected_state="Consent documented for advanced matching",
                                    detail=f"Advanced Matching enabled with fields: {', '.join(matching_fields)}.",
                                    metadata={"matching_fields": matching_fields}
                                )
                            ))
                        
                        # Conversions API Configuration indicator
                        # Never infer CAPI usage just because a Pixel exists.
                        # Meta doesn't easily expose CAPI status without event reading (which is forbidden),
                        # but if direct evidence exists (e.g. server_events_enabled flag if exposed), we use it.
                        
                        # We request "is_server_events_enabled" just in case Meta exposes it directly.
                        has_capi = px.get("is_server_events_enabled") or px.get("server_events_enabled")
                        
                        if has_capi:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.serverSide",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source=f"meta:conversions_api:{px.get('name')}", observed_at=now,
                                detail=f"Conversions API (server-side sharing) is explicitly enabled for Pixel ({px.get('name')}).",
                                structured_detail=StructuredEvidence(
                                    source="meta", integration="meta", operation="get_pixel_config",
                                    resource_type="pixel", resource_name=px.get("name"),
                                    location=f"Business: {business_id or 'Unavailable'} > Pixel (CAPI): {px.get('id', 'Unknown')} ({px.get('name', 'Unknown')})",
                                    observed_at=now, observed_state="CAPI enabled", expected_state="Consent documented for server-side sharing",
                                    detail=f"Conversions API (server-side sharing) is explicitly enabled for Pixel ({px.get('name')}).",
                                    metadata={"pixel_id": px.get("id"), "first_party_cookie_status": px.get("first_party_cookie_status")}
                                )
                            ))
                        else:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.serverSide",
                                verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                                source=f"meta:conversions_api:{px.get('name')}", observed_at=now,
                                detail=f"Meta Pixel ({px.get('name')}) is active, but Conversions API (server-side sharing) status cannot be automatically determined without direct evidence.",
                                structured_detail=StructuredEvidence(
                                    source="meta", integration="meta", operation="get_pixel_config",
                                    resource_type="pixel", resource_name=px.get("name"),
                                    location=f"Business: {business_id or 'Unavailable'} > Pixel (CAPI): {px.get('id', 'Unknown')} ({px.get('name', 'Unknown')})",
                                    observed_at=now, observed_state="Unknown (Requires Human Review)", expected_state="CAPI evaluated",
                                    detail=f"Meta Pixel ({px.get('name')}) is active, but Conversions API (server-side sharing) status cannot be automatically determined without direct evidence.",
                                    metadata={"pixel_id": px.get("id"), "first_party_cookie_status": px.get("first_party_cookie_status"), "human_review_required": True}
                                )
                            ))
                except ExternalAPIError as exc:
                    errors.append(f"Pixel config query failed: {exc}")
                    for obl_id in ["S9.sharing.advancedMatching", "S9.sharing.serverSide"]:
                        evidence.append(EvidenceItem(
                            obligation_id=obl_id,
                            verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                            source="meta:error:pixels", observed_at=now,
                            detail=f"Failed to read Meta Pixel configuration: {exc}",
                            structured_detail=StructuredEvidence(
                                source="meta", integration="meta", operation="get_pixel_config",
                                resource_type="ad_account", resource_id=f"act_{clean_ad_account}" if clean_ad_account else None,
                                location="Unavailable",
                                observed_at=now, observed_state="Collection error",
                                expected_state="Successful collection",
                                detail=f"Pixel config query failed: {exc}",
                                reason=f"API error: {exc}",
                            ),
                        ))

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
                        for entity in shared_entities:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.documented",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source=f"meta:business_sharing:{entity.get('name', 'unknown')}", observed_at=now,
                                detail=f"Business Manager shares data with external agency/business '{entity.get('name', 'unknown')}'.",
                                structured_detail=StructuredEvidence(
                                    source="meta", integration="meta", operation="get_business_agencies",
                                    resource_type="shared_business", resource_id=entity.get("id"), resource_name=entity.get("name"),
                                    location=f"Business: {business_id} > Shared Entity: {entity.get('id', 'Unknown')} ({entity.get('name', 'Unknown')})",
                                    observed_at=now, observed_state="Shared Data", expected_state="Documented processing agreement",
                                    detail=f"Business Manager shares data with external agency/business '{entity.get('name')}'.",
                                    metadata={"shared_entity": entity}
                                )
                            ))
                except ExternalAPIError as exc:
                    errors.append(f"Business sharing query failed: {exc}")
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.documented",
                        verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                        source="meta:error:business_sharing", observed_at=now,
                        detail=f"Failed to read Business Manager sharing: {exc}",
                        structured_detail=StructuredEvidence(
                            source="meta", integration="meta", operation="get_business_agencies",
                            resource_type="business", resource_id=business_id,
                            location="Unavailable",
                            observed_at=now, observed_state="Collection error",
                            expected_state="Successful collection",
                            detail=f"Business sharing query failed: {exc}",
                            reason=f"API error: {exc}",
                        ),
                    ))

                # Offline Event Sets
                try:
                    offline_sets = await client.get(
                        f"/v21.0/{business_id}/offline_conversion_data_sets",
                        params={"fields": "id,name,creation_time"},
                        operation="get_offline_events",
                    )
                    sets_data = offline_sets.get("data", [])
                    if sets_data:
                        for event_set in sets_data:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.audienceUpload",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source=f"meta:offline_events:{event_set.get('name', 'unknown')}", observed_at=now,
                                detail=f"Found Offline Event Set '{event_set.get('name')}'. Requires a documented consent basis.",
                                structured_detail=StructuredEvidence(
                                    source="meta", integration="meta", operation="get_offline_events",
                                    resource_type="offline_event_set", resource_id=event_set.get("id"), resource_name=event_set.get("name"),
                                    location=f"Business: {business_id} > OfflineEventSet: {event_set.get('id', 'Unknown')} ({event_set.get('name', 'Unknown')})",
                                    observed_at=now, observed_state="Configured", expected_state="Documented consent basis",
                                    detail=f"Found Offline Event Set '{event_set.get('name')}'. Requires a documented consent basis.",
                                    metadata={"event_set": event_set}
                                )
                            ))
                except ExternalAPIError as exc:
                    errors.append(f"Offline events query failed: {exc}")
                    evidence.append(EvidenceItem(
                        obligation_id="S9.sharing.audienceUpload",
                        verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                        source="meta:error:offline_events", observed_at=now,
                        detail=f"Failed to read Offline Event Sets: {exc}",
                        structured_detail=StructuredEvidence(
                            source="meta", integration="meta", operation="get_offline_events",
                            resource_type="business", resource_id=business_id,
                            location="Unavailable",
                            observed_at=now, observed_state="Collection error",
                            expected_state="Successful collection",
                            detail=f"Offline events query failed: {exc}",
                            reason=f"API error: {exc}",
                        ),
                    ))

            return AdapterResult(evidence=evidence, errors=errors)

        except ExternalAPIError as exc:
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S9.sharing.audienceUpload",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="meta:error", observed_at=now,
                    detail=f"Meta collection failed: {exc}",
                    structured_detail=StructuredEvidence(
                        source="meta", integration="meta", operation="list_custom_audiences",
                        resource_type="ad_account", resource_id=f"act_{clean_ad_account}" if clean_ad_account else None,
                        location="Unavailable",
                        observed_at=now, observed_state="Collection error",
                        expected_state="Successful collection",
                        detail=f"Meta collection failed: {exc}",
                        reason=f"API error: {exc}",
                    ),
                )],
                errors=[str(exc)], partial=True,
            )
        finally:
            await client.close()
