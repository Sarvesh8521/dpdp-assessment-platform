"""Meta (Facebook) adapter. PHASE2_SPEC Section 4.4.

Marketing API, read-only. Requires app review and Business Manager access.
NEVER read: audience membership, event payloads, user identifiers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domain.evidence import Confidence, EvidenceItem, StructuredEvidence, Verdict
from app.domain.obligations import validate_obligation_code
from app.integrations.base.adapter import (
    AdapterResult,
    ConnectionInfo,
    HealthStatus,
    IntegrationAdapter,
)
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError

logger = get_logger(__name__)

META_OBLIGATIONS = [
    "S7.purposeLimitation",
    "S8.dataMinimization",
    "S9.sharing.documented",
    "S9.sharing.audienceUpload",
    "S9.sharing.enhancedConversions",
    "S9.sharing.serverSide",
    "S9.sharing.advancedMatching",
    "S12.governance.vendorManagement",
]

# Custom Audience metadata is allowed. Membership, users, and event payloads are not.
META_FORBIDDEN_ENDPOINTS = [
    "/users",
    "/events",
    "/activities",
    "/customaudiences/",
]

CUSTOMER_LIST_SUBTYPES = {"CUSTOM", "CUSTOMER_LIST"}
WEBSITE_AUDIENCE_SUBTYPES = {"WEBSITE", "WEB", "PIXEL"}
OTHER_AUDIENCE_SUBTYPES = {
    "APP",
    "ENGAGEMENT",
    "LOOKALIKE",
    "OFFLINE_CONVERSION",
    "VIDEO",
    "PAGE",
    "INSTAGRAM_BUSINESS_PROFILE",
    "LEAD_FORM",
}
CUSTOMER_FILE_ORIGINS = {
    "USER_PROVIDED_ONLY",
    "PARTNER_PROVIDED_ONLY",
    "BOTH_USER_AND_PARTNER_PROVIDED",
    "USER_PROVIDED",
    "PARTNER_PROVIDED",
    "FILE_IMPORTED",
    "CUSTOMER_FILE",
}


def _validated_meta_obligations() -> list[str]:
    obligations = [code for code in META_OBLIGATIONS if validate_obligation_code(code)]
    missing = set(META_OBLIGATIONS) - set(obligations)
    if missing:
        logger.error("meta_obligation_missing_from_register", obligations=sorted(missing))
    return obligations


def _clean_id(value: Any) -> str:
    return str(value or "").strip()


def _safe_permission_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _safe_permission_metadata(v)
            for k, v in value.items()
            if not any(secret in str(k).lower() for secret in ("token", "secret", "email", "phone"))
        }
    if isinstance(value, list):
        return [_safe_permission_metadata(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _data_source_type(audience: dict[str, Any]) -> str:
    data_source = audience.get("data_source") or {}
    if not isinstance(data_source, dict):
        return _clean_id(data_source).upper()
    return _clean_id(
        data_source.get("type")
        or data_source.get("sub_type")
        or data_source.get("source_type")
    ).upper()


def _classify_audience(audience: dict[str, Any]) -> str:
    subtype = _clean_id(audience.get("subtype")).upper()
    origin = _clean_id(audience.get("customer_file_source")).upper()
    data_source_type = _data_source_type(audience)

    if subtype in WEBSITE_AUDIENCE_SUBTYPES or data_source_type in WEBSITE_AUDIENCE_SUBTYPES:
        return "website"
    if subtype in OTHER_AUDIENCE_SUBTYPES:
        return subtype.lower()
    if origin in CUSTOMER_FILE_ORIGINS or data_source_type in CUSTOMER_FILE_ORIGINS:
        return "customer_list"
    if subtype in CUSTOMER_LIST_SUBTYPES:
        return "customer_list"
    if subtype:
        return subtype.lower()
    return "unknown"


def _audience_metadata(audience: dict[str, Any]) -> dict[str, Any]:
    data_source = audience.get("data_source") if isinstance(audience.get("data_source"), dict) else {}
    audience_type = _classify_audience(audience)
    return {
        "id": _clean_id(audience.get("id")) or None,
        "name": _clean_id(audience.get("name")) or "Unnamed audience",
        "subtype": _clean_id(audience.get("subtype")).upper() or "UNKNOWN",
        "type": audience_type,
        "approximate_size": audience.get("approximate_count") or audience.get("approximate_size"),
        "origin": _clean_id(audience.get("customer_file_source") or data_source.get("type")) or "UNKNOWN",
        "data_source": _data_source_type(audience) or "UNKNOWN",
        "sharing": _safe_permission_metadata(audience.get("permission_for_actions") or {}),
    }


def _field_present(resource: dict[str, Any], *field_names: str) -> bool:
    return any(field in resource for field in field_names)


def _highest_confidence(items: list[dict[str, Any]]) -> str:
    order = {"high": 3, "medium": 2, "low": 1}
    if not items:
        return Confidence.LOW.value
    return max(items, key=lambda item: order.get(str(item.get("confidence", "")).lower(), 0)).get(
        "confidence",
        Confidence.LOW.value,
    )


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
        return _validated_meta_obligations()

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "ads_read": [
                "S7.purposeLimitation",
                "S8.dataMinimization",
                "S9.sharing.audienceUpload",
                "S9.sharing.enhancedConversions",
                "S9.sharing.serverSide",
                "S9.sharing.advancedMatching",
            ],
            "business_management": [
                "S9.sharing.documented",
                "S12.governance.vendorManagement",
            ],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return META_FORBIDDEN_ENDPOINTS

    @property
    def safe_fields(self) -> frozenset[str]:
        return frozenset({
            "id",
            "name",
            "subtype",
            "type",
            "approximate_size",
            "origin",
            "data_source",
            "sharing",
            "first_party_cookie_status",
            "automatic_matching_fields",
            "server_events_enabled",
            "is_server_events_enabled",
        })

    @property
    def forbidden_fields(self) -> frozenset[str]:
        return frozenset({
            "users",
            "members",
            "events",
            "event_payload",
            "email",
            "phone",
            "access_token",
            "token",
        })

    async def connect(self, credentials: dict[str, Any], audit_callback: Any) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider,
            scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
            metadata={"app_review_status": credentials.get("app_review_status", "pending")},
        )

    async def health_check(
        self,
        connection: ConnectionInfo,
        audit_callback: Any,
        *,
        client_id: str | None = None,
        assessment_id: str | None = None,
    ) -> HealthStatus:
        return HealthStatus(
            healthy=True,
            provider=self.provider,
            message="Connected",
            checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self,
        connection: ConnectionInfo,
        audit_callback: Any,
        *,
        assessment_id: str | None = None,
        client_id: str | None = None,
        access_token: str = "",
        ad_account_id: str = "",
        business_id: str = "",
        **kwargs: Any,
    ) -> AdapterResult:
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc)

        meta = connection.metadata or {}
        business_id = _clean_id(
            business_id
            or meta.get("business_id")
            or meta.get("resource_id")
            or kwargs.get("business_id")
        )
        clean_ad_account = _clean_id(ad_account_id or meta.get("ad_account_id"))
        clean_ad_account = clean_ad_account.replace("act_", "")

        check_state = {
            obl: {"observed": 0, "failures": [], "checks": set(), "facts": {}}
            for obl in self.obligations_answered
        }

        def mark_check(obligation_ids: list[str], check: str, facts: dict[str, Any] | None = None) -> None:
            for obligation_id in obligation_ids:
                if obligation_id in check_state:
                    check_state[obligation_id]["checks"].add(check)
                    if facts:
                        check_state[obligation_id]["facts"].update(facts)

        def add_evidence(
            obligation_id: str,
            verdict: Verdict,
            confidence: Confidence,
            source: str,
            detail: str,
            operation: str,
            resource_type: str,
            *,
            resource_id: str | None = None,
            resource_name: str | None = None,
            location: str = "Unavailable",
            observed_state: str | None = None,
            expected_state: str | None = None,
            reason: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> None:
            if obligation_id not in check_state:
                return
            if verdict == Verdict.OBSERVED:
                check_state[obligation_id]["observed"] += 1
            evidence.append(EvidenceItem(
                obligation_id=obligation_id,
                verdict=verdict,
                confidence=confidence,
                source=source,
                observed_at=now,
                detail=detail,
                structured_detail=StructuredEvidence(
                    source="meta",
                    integration="meta",
                    operation=operation,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    resource_name=resource_name,
                    location=location,
                    observed_at=now,
                    observed_state=observed_state,
                    expected_state=expected_state,
                    detail=detail,
                    reason=reason,
                    metadata=metadata,
                ),
            ))

        def add_failure(
            obligation_ids: list[str],
            source: str,
            operation: str,
            detail: str,
            *,
            resource_type: str,
            resource_id: str | None = None,
            check: str,
        ) -> None:
            errors.append(detail)
            for obligation_id in obligation_ids:
                if obligation_id not in check_state:
                    continue
                check_state[obligation_id]["failures"].append(detail)
                add_evidence(
                    obligation_id,
                    Verdict.CANNOT_DETERMINE,
                    Confidence.LOW,
                    source,
                    detail,
                    operation,
                    resource_type,
                    resource_id=resource_id,
                    location="Unavailable",
                    observed_state="Collection unavailable",
                    expected_state="Successful read-only Meta API collection",
                    reason=detail,
                    metadata={"check": check},
                )

        client = AuditedHTTPClient(
            provider=self.provider,
            audit_callback=audit_callback,
            base_url="https://graph.facebook.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id,
            assessment_id=assessment_id,
        )

        try:
            if not business_id:
                detail = "Meta connection is active but no Business Manager resource is selected."
                for obl_id in self.obligations_answered:
                    add_evidence(
                        obl_id,
                        Verdict.CANNOT_DETERMINE,
                        Confidence.LOW,
                        "meta:error:no_business_resource",
                        detail,
                        "resource_selection",
                        "business",
                        observed_state="No Business Manager selected",
                        expected_state="Selected Meta Business Manager resource",
                        reason=detail,
                    )
                errors.append("No Meta Business Manager resource selected")
                return AdapterResult(evidence=evidence, errors=errors, partial=True)

            ad_accounts_to_scan: list[str] = []
            pixels_to_scan: list[str] = []

            if clean_ad_account:
                ad_accounts_to_scan.append(clean_ad_account)
                mark_check(
                    ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                    "ad_account_discovery",
                    {"ad_accounts_scanned": 1, "mode": "ad_account_only"},
                )
            else:
                try:
                    resp_ad = await client.get(
                        f"/v21.0/{business_id}/owned_ad_accounts",
                        params={"fields": "id,name,account_id"},
                        operation="get_business_ad_accounts",
                    )
                    for item in resp_ad.get("data", []):
                        aid = _clean_id(item.get("account_id") or item.get("id")).replace("act_", "")
                        if aid and aid not in ad_accounts_to_scan:
                            ad_accounts_to_scan.append(aid)
                    mark_check(
                        ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                        "ad_account_discovery",
                        {"ad_accounts_scanned": len(ad_accounts_to_scan), "mode": "business_only"},
                    )
                except ExternalAPIError as exc:
                    logger.warning("failed_to_fetch_business_ad_accounts", business_id=business_id, error=str(exc))
                    add_failure(
                        ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                        "meta:error:ad_accounts",
                        "get_business_ad_accounts",
                        f"Failed to read Meta Business ad accounts for {business_id}: {exc}",
                        resource_type="business",
                        resource_id=business_id,
                        check="ad_account_discovery",
                    )

            try:
                resp_px = await client.get(
                    f"/v21.0/{business_id}/owned_pixels",
                    params={"fields": "id,name"},
                    operation="get_business_pixels",
                )
                for item in resp_px.get("data", []):
                    pid = _clean_id(item.get("id"))
                    if pid and pid not in pixels_to_scan:
                        pixels_to_scan.append(pid)
                mark_check(
                    [
                        "S7.purposeLimitation",
                        "S8.dataMinimization",
                        "S9.sharing.enhancedConversions",
                        "S9.sharing.serverSide",
                        "S9.sharing.advancedMatching",
                    ],
                    "pixel_discovery",
                    {"pixels_discovered": len(pixels_to_scan)},
                )
            except ExternalAPIError as exc:
                logger.warning("failed_to_fetch_business_pixels", business_id=business_id, error=str(exc))
                add_failure(
                    [
                        "S7.purposeLimitation",
                        "S8.dataMinimization",
                        "S9.sharing.enhancedConversions",
                        "S9.sharing.serverSide",
                        "S9.sharing.advancedMatching",
                    ],
                    "meta:error:pixels",
                    "get_business_pixels",
                    f"Failed to read Meta Pixel list for {business_id}: {exc}",
                    resource_type="business",
                    resource_id=business_id,
                    check="pixel_discovery",
                )

            customer_list_count = 0
            audience_count = 0
            for acc in ad_accounts_to_scan:
                try:
                    audiences = await client.get(
                        f"/v21.0/act_{acc}/customaudiences",
                        params={
                            "fields": (
                                "id,name,subtype,approximate_count,data_source,"
                                "delivery_status,operation_status,customer_file_source,"
                                "permission_for_actions"
                            )
                        },
                        operation="list_custom_audiences",
                    )
                    mark_check(
                        ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                        "custom_audiences",
                    )

                    for raw_audience in audiences.get("data", []):
                        audience_count += 1
                        audience = _audience_metadata(raw_audience)
                        audience_type = audience["type"]
                        audience_name = audience["name"]
                        audience_id = audience.get("id")
                        location = (
                            f"Business: {business_id} > Ad Account: act_{acc} > "
                            f"Audience: {audience_id or audience_name}"
                        )

                        if audience_type == "customer_list":
                            customer_list_count += 1
                            add_evidence(
                                "S9.sharing.audienceUpload",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:custom_audience:{audience_id or audience_name}",
                                (
                                    f"Customer-list Custom Audience '{audience_name}' was found in "
                                    f"Meta ad account act_{acc}."
                                ),
                                "list_custom_audiences",
                                "custom_audience",
                                resource_id=audience_id or f"act_{acc}",
                                resource_name=audience_name,
                                location=location,
                                observed_state="Customer-list Custom Audience present",
                                expected_state="No customer-list upload without documented lawful basis",
                                reason="Customer-list audiences indicate uploaded or matched customer data configuration.",
                                metadata={"audience": audience, "business_id": business_id, "ad_account_id": f"act_{acc}"},
                            )
                            add_evidence(
                                "S8.dataMinimization",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:custom_audience:{audience_id or audience_name}:minimization",
                                (
                                    f"Customer-list Custom Audience '{audience_name}' stores a "
                                    "customer data matching configuration."
                                ),
                                "list_custom_audiences",
                                "custom_audience",
                                resource_id=audience_id or f"act_{acc}",
                                resource_name=audience_name,
                                location=location,
                                observed_state="Customer-list matching configured",
                                expected_state="Only necessary data used for the documented purpose",
                                reason="Customer-list matching creates a data minimization obligation for uploaded identifiers.",
                                metadata={"audience": audience, "business_id": business_id, "ad_account_id": f"act_{acc}"},
                            )

                        if audience_type in {"customer_list", "website", "app", "engagement", "offline_conversion"}:
                            add_evidence(
                                "S7.purposeLimitation",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:custom_audience:{audience_id or audience_name}:purpose",
                                (
                                    f"Meta Custom Audience '{audience_name}' of type "
                                    f"'{audience_type}' is configured for advertising use."
                                ),
                                "list_custom_audiences",
                                "custom_audience",
                                resource_id=audience_id or f"act_{acc}",
                                resource_name=audience_name,
                                location=location,
                                observed_state=f"Audience type: {audience_type}",
                                expected_state="Audience use limited to documented purposes",
                                reason="Advertising audiences require purpose limitation evidence.",
                                metadata={"audience": audience, "business_id": business_id, "ad_account_id": f"act_{acc}"},
                            )
                except ExternalAPIError as exc:
                    add_failure(
                        ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                        "meta:error:custom_audiences",
                        "list_custom_audiences",
                        f"Failed to read Custom Audience metadata for act_{acc}: {exc}",
                        resource_type="ad_account",
                        resource_id=f"act_{acc}",
                        check="custom_audiences",
                    )

            if not ad_accounts_to_scan:
                mark_check(
                    ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                    "custom_audiences",
                )

            mark_check(
                ["S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"],
                "custom_audience_classification",
                {
                    "custom_audience_count": audience_count,
                    "customer_list_audience_count": customer_list_count,
                },
            )

            advanced_matching_observed = 0
            capi_observed = 0
            pixels_with_complete_capi = 0
            pixels_with_complete_matching = 0

            for pid in pixels_to_scan:
                try:
                    px = await client.get(
                        f"/v21.0/{pid}",
                        params={
                            "fields": (
                                "id,name,automatic_matching_fields,first_party_cookie_status,"
                                "is_created_by_business_manager,creation_time,"
                                "is_server_events_enabled,server_events_enabled"
                            )
                        },
                        operation="get_pixel_config",
                    )
                    px_id = _clean_id(px.get("id") or pid)
                    px_name = _clean_id(px.get("name")) or "Unnamed Pixel"
                    location = f"Business: {business_id} > Pixel: {px_id} ({px_name})"
                    first_party_cookie_status = px.get("first_party_cookie_status")
                    matching_fields = px.get("automatic_matching_fields")

                    add_evidence(
                        "S7.purposeLimitation",
                        Verdict.OBSERVED,
                        Confidence.HIGH,
                        f"meta:pixel:{px_id}:purpose",
                        f"Meta Pixel '{px_name}' is configured for Meta advertising measurement.",
                        "get_pixel_config",
                        "pixel",
                        resource_id=px_id,
                        resource_name=px_name,
                        location=location,
                        observed_state="Pixel configured",
                        expected_state="Pixel use limited to documented purposes",
                        reason="Meta Pixel configuration indicates advertising measurement/tracking is active.",
                        metadata={
                            "business_id": business_id,
                            "pixel_id": px_id,
                            "first_party_cookie_status": first_party_cookie_status,
                        },
                    )

                    if _field_present(px, "automatic_matching_fields"):
                        pixels_with_complete_matching += 1
                        if isinstance(matching_fields, list) and matching_fields:
                            advanced_matching_observed += 1
                            field_labels = [str(field) for field in matching_fields]
                            add_evidence(
                                "S9.sharing.advancedMatching",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:advanced_matching:{px_id}",
                                (
                                    f"Advanced Matching is enabled for Meta Pixel '{px_name}' "
                                    f"with configured matching fields: {', '.join(field_labels)}."
                                ),
                                "get_pixel_config",
                                "pixel",
                                resource_id=px_id,
                                resource_name=px_name,
                                location=location,
                                observed_state="Advanced Matching enabled",
                                expected_state="Advanced Matching disabled unless consent basis is documented",
                                reason="Advanced Matching transmits hashed matching fields to Meta.",
                                metadata={
                                    "business_id": business_id,
                                    "pixel_id": px_id,
                                    "matching_fields": field_labels,
                                },
                            )
                            add_evidence(
                                "S9.sharing.enhancedConversions",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:enhanced_conversions:{px_id}",
                                (
                                    f"Meta Pixel '{px_name}' has matching-field transmission configured "
                                    "through Advanced Matching."
                                ),
                                "get_pixel_config",
                                "pixel",
                                resource_id=px_id,
                                resource_name=px_name,
                                location=location,
                                observed_state="Matching-field transmission configured",
                                expected_state="No enhanced conversion-style matching without consent basis",
                                reason="Meta Advanced Matching is the Meta-side matching configuration relevant to this obligation.",
                                metadata={
                                    "business_id": business_id,
                                    "pixel_id": px_id,
                                    "matching_fields": field_labels,
                                },
                            )
                            add_evidence(
                                "S8.dataMinimization",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:advanced_matching:{px_id}:minimization",
                                (
                                    f"Meta Pixel '{px_name}' has Advanced Matching fields configured, "
                                    "creating a data minimization obligation."
                                ),
                                "get_pixel_config",
                                "pixel",
                                resource_id=px_id,
                                resource_name=px_name,
                                location=location,
                                observed_state="Advanced Matching fields configured",
                                expected_state="Only necessary matching fields enabled",
                                reason="Advanced Matching field configuration must be minimized to what is necessary.",
                                metadata={
                                    "business_id": business_id,
                                    "pixel_id": px_id,
                                    "matching_fields": field_labels,
                                },
                            )
                    else:
                        add_failure(
                            ["S9.sharing.advancedMatching", "S9.sharing.enhancedConversions", "S8.dataMinimization"],
                            "meta:error:advanced_matching",
                            "get_pixel_config",
                            f"Meta Pixel '{px_name}' did not expose Advanced Matching configuration.",
                            resource_type="pixel",
                            resource_id=px_id,
                            check="advanced_matching",
                        )

                    if _field_present(px, "is_server_events_enabled", "server_events_enabled"):
                        pixels_with_complete_capi += 1
                        has_capi = bool(px.get("is_server_events_enabled") or px.get("server_events_enabled"))
                        if has_capi:
                            capi_observed += 1
                            add_evidence(
                                "S9.sharing.serverSide",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:conversions_api:{px_id}",
                                f"Conversions API/server-side event sharing is enabled for Meta Pixel '{px_name}'.",
                                "get_pixel_config",
                                "pixel",
                                resource_id=px_id,
                                resource_name=px_name,
                                location=location,
                                observed_state="Conversions API enabled",
                                expected_state="No server-side sharing without documented consent basis",
                                reason="Server-side Meta event sharing is enabled for this Pixel.",
                                metadata={
                                    "business_id": business_id,
                                    "pixel_id": px_id,
                                    "first_party_cookie_status": first_party_cookie_status,
                                },
                            )
                            add_evidence(
                                "S9.sharing.enhancedConversions",
                                Verdict.OBSERVED,
                                Confidence.HIGH,
                                f"meta:conversions_api:{px_id}:enhanced_conversions",
                                (
                                    f"Meta Pixel '{px_name}' has server-side conversion transmission "
                                    "configured through Conversions API."
                                ),
                                "get_pixel_config",
                                "pixel",
                                resource_id=px_id,
                                resource_name=px_name,
                                location=location,
                                observed_state="Server-side conversion transmission configured",
                                expected_state="No enhanced conversion-style sharing without consent basis",
                                reason="Meta Conversions API is server-side conversion sharing relevant to this obligation.",
                                metadata={"business_id": business_id, "pixel_id": px_id},
                            )
                    else:
                        add_failure(
                            ["S9.sharing.serverSide", "S9.sharing.enhancedConversions"],
                            "meta:error:conversions_api",
                            "get_pixel_config",
                            f"Meta Pixel '{px_name}' did not expose Conversions API/server-side status.",
                            resource_type="pixel",
                            resource_id=px_id,
                            check="conversions_api",
                        )
                except ExternalAPIError as exc:
                    add_failure(
                        [
                            "S7.purposeLimitation",
                            "S8.dataMinimization",
                            "S9.sharing.enhancedConversions",
                            "S9.sharing.serverSide",
                            "S9.sharing.advancedMatching",
                        ],
                        "meta:error:pixels",
                        "get_pixel_config",
                        f"Failed to read Meta Pixel configuration for {pid}: {exc}",
                        resource_type="pixel",
                        resource_id=pid,
                        check="pixel_config",
                    )

            mark_check(
                [
                    "S7.purposeLimitation",
                    "S8.dataMinimization",
                    "S9.sharing.enhancedConversions",
                    "S9.sharing.serverSide",
                    "S9.sharing.advancedMatching",
                ],
                "pixel_config",
                {
                    "pixels_scanned": len(pixels_to_scan),
                    "advanced_matching_pixels": advanced_matching_observed,
                    "capi_pixels": capi_observed,
                    "pixels_with_advanced_matching_status": pixels_with_complete_matching,
                    "pixels_with_capi_status": pixels_with_complete_capi,
                },
            )

            shared_entities: list[dict[str, Any]] = []
            try:
                agencies = await client.get(
                    f"/v21.0/{business_id}/agencies",
                    params={"fields": "id,name"},
                    operation="get_business_agencies",
                )
                shared_entities.extend([
                    {**entity, "relationship": "agency"}
                    for entity in agencies.get("data", [])
                ])

                try:
                    clients = await client.get(
                        f"/v21.0/{business_id}/client_businesses",
                        params={"fields": "id,name"},
                        operation="get_client_businesses",
                    )
                except ExternalAPIError as first_exc:
                    logger.warning("meta_client_businesses_edge_failed", business_id=business_id, error=str(first_exc))
                    clients = await client.get(
                        f"/v21.0/{business_id}/clients",
                        params={"fields": "id,name"},
                        operation="get_client_businesses",
                    )

                shared_entities.extend([
                    {**entity, "relationship": "client_business"}
                    for entity in clients.get("data", [])
                ])
                mark_check(
                    ["S9.sharing.documented", "S12.governance.vendorManagement"],
                    "business_sharing",
                    {"shared_entities": len(shared_entities)},
                )

                for entity in shared_entities:
                    entity_id = _clean_id(entity.get("id"))
                    entity_name = _clean_id(entity.get("name")) or "Unnamed shared entity"
                    relationship = _clean_id(entity.get("relationship")) or "business"
                    sharing_operation = "get_business_agencies" if relationship == "agency" else "get_client_businesses"
                    metadata = {
                        "business_id": business_id,
                        "shared_entity": {
                            "id": entity_id or None,
                            "name": entity_name,
                            "relationship": relationship,
                        },
                    }
                    add_evidence(
                        "S9.sharing.documented",
                        Verdict.OBSERVED,
                        Confidence.HIGH,
                        f"meta:business_sharing:{entity_id or entity_name}",
                        (
                            f"Meta Business Manager shares assets/data with external "
                            f"{relationship} '{entity_name}'."
                        ),
                        sharing_operation,
                        "shared_business",
                        resource_id=entity_id or business_id,
                        resource_name=entity_name,
                        location=f"Business: {business_id} > Shared Entity: {entity_id or entity_name}",
                        observed_state=f"Shared {relationship}",
                        expected_state="Documented third-party sharing and processing terms",
                        reason="External Meta Business sharing requires documented data-sharing terms.",
                        metadata=metadata,
                    )
                    add_evidence(
                        "S12.governance.vendorManagement",
                        Verdict.OBSERVED,
                        Confidence.HIGH,
                        f"meta:business_sharing:{entity_id or entity_name}:vendor",
                        (
                            f"Meta Business Manager has an external {relationship} "
                            f"'{entity_name}' requiring vendor governance."
                        ),
                        sharing_operation,
                        "shared_business",
                        resource_id=entity_id or business_id,
                        resource_name=entity_name,
                        location=f"Business: {business_id} > Shared Entity: {entity_id or entity_name}",
                        observed_state=f"External {relationship} connected",
                        expected_state="Vendor oversight and documented processing responsibilities",
                        reason="External partner access to Meta assets requires vendor management.",
                        metadata=metadata,
                    )
            except ExternalAPIError as exc:
                add_failure(
                    ["S9.sharing.documented", "S12.governance.vendorManagement"],
                    "meta:error:business_sharing",
                    "get_business_sharing",
                    f"Failed to read Meta Business Manager sharing configuration: {exc}",
                    resource_type="business",
                    resource_id=business_id,
                    check="business_sharing",
                )

            offline_sets_seen: set[str] = set()
            offline_sets: list[dict[str, Any]] = []

            for acc in ad_accounts_to_scan:
                try:
                    offline_resp = await client.get(
                        f"/v21.0/act_{acc}/offline_conversion_data_sets",
                        params={"fields": "id,name,creation_time"},
                        operation="get_offline_event_sets",
                    )
                    mark_check(
                        ["S9.sharing.audienceUpload", "S8.dataMinimization"],
                        "offline_event_sets",
                    )
                    for dataset in offline_resp.get("data", []):
                        dataset_id = _clean_id(dataset.get("id"))
                        if dataset_id and dataset_id not in offline_sets_seen:
                            offline_sets_seen.add(dataset_id)
                            offline_sets.append(dataset)
                except ExternalAPIError as exc:
                    add_failure(
                        ["S9.sharing.audienceUpload", "S8.dataMinimization"],
                        "meta:error:offline_events",
                        "get_offline_event_sets",
                        f"Failed to read Meta Offline Event Set configuration for act_{acc}: {exc}",
                        resource_type="ad_account",
                        resource_id=f"act_{acc}",
                        check="offline_event_sets",
                    )

            if not ad_accounts_to_scan:
                mark_check(
                    ["S9.sharing.audienceUpload", "S8.dataMinimization"],
                    "offline_event_sets",
                )

            mark_check(
                ["S9.sharing.audienceUpload", "S8.dataMinimization"],
                "offline_event_sets",
                {"offline_event_sets": len(offline_sets)},
            )
            
            for dataset in offline_sets:
                dataset_id = _clean_id(dataset.get("id"))
                dataset_name = _clean_id(dataset.get("name")) or "Unnamed Offline Event Set"
                metadata = {
                    "business_id": business_id,
                    "offline_event_set": {
                        "id": dataset_id or None,
                        "name": dataset_name,
                        "creation_time": dataset.get("creation_time"),
                    },
                }
                add_evidence(
                    "S9.sharing.audienceUpload",
                    Verdict.OBSERVED,
                    Confidence.HIGH,
                    f"meta:offline_events:{dataset_id or dataset_name}",
                    f"Meta Offline Event Set '{dataset_name}' is configured for ad account(s) in the Business Manager.",
                    "get_offline_event_sets",
                    "offline_event_set",
                    resource_id=dataset_id or business_id,
                    resource_name=dataset_name,
                    location=f"Business: {business_id} > Offline Event Set: {dataset_id or dataset_name}",
                    observed_state="Offline Event Set present",
                    expected_state="No offline event matching without documented consent basis",
                    reason="Offline Event Sets support offline conversion matching with Meta.",
                    metadata=metadata,
                )
                add_evidence(
                    "S8.dataMinimization",
                    Verdict.OBSERVED,
                    Confidence.HIGH,
                    f"meta:offline_events:{dataset_id or dataset_name}:minimization",
                    f"Meta Offline Event Set '{dataset_name}' creates an offline matching data flow.",
                    "get_offline_event_sets",
                    "offline_event_set",
                    resource_id=dataset_id or business_id,
                    resource_name=dataset_name,
                    location=f"Business: {business_id} > Offline Event Set: {dataset_id or dataset_name}",
                    observed_state="Offline matching configured",
                    expected_state="Only necessary offline conversion data used",
                    reason="Offline conversion matching creates a data minimization obligation.",
                    metadata=metadata,
                )

            required_checks = {
                "S7.purposeLimitation": {"custom_audiences", "custom_audience_classification", "pixel_discovery", "pixel_config"},
                "S8.dataMinimization": {"custom_audiences", "custom_audience_classification", "pixel_discovery", "pixel_config", "offline_event_sets"},
                "S9.sharing.documented": {"business_sharing"},
                "S9.sharing.audienceUpload": {"custom_audiences", "custom_audience_classification", "offline_event_sets"},
                "S9.sharing.enhancedConversions": {"pixel_discovery", "pixel_config"},
                "S9.sharing.serverSide": {"pixel_discovery", "pixel_config"},
                "S9.sharing.advancedMatching": {"pixel_discovery", "pixel_config"},
                "S12.governance.vendorManagement": {"business_sharing"},
            }

            not_observed_details = {
                "S7.purposeLimitation": "No Meta audiences or pixels requiring purpose-limitation review were found.",
                "S8.dataMinimization": "No Meta customer-list, Advanced Matching, or offline matching configuration was found.",
                "S9.sharing.documented": "No external Meta Business Manager partner sharing was found.",
                "S9.sharing.audienceUpload": "No customer-list Custom Audiences or Offline Event Sets were found.",
                "S9.sharing.enhancedConversions": "No Meta Advanced Matching or Conversions API conversion-matching configuration was found.",
                "S9.sharing.serverSide": "No Meta Conversions API/server-side event sharing was found.",
                "S9.sharing.advancedMatching": "No Meta Advanced Matching fields were configured.",
                "S12.governance.vendorManagement": "No external Meta Business Manager vendors or partner businesses were found.",
            }

            for obligation_id in self.obligations_answered:
                state = check_state[obligation_id]
                if state["observed"]:
                    continue
                if state["failures"]:
                    continue
                missing_checks = required_checks.get(obligation_id, set()) - state["checks"]
                if missing_checks:
                    detail = (
                        f"Meta evidence is incomplete for {obligation_id}; missing checks: "
                        f"{', '.join(sorted(missing_checks))}."
                    )
                    add_evidence(
                        obligation_id,
                        Verdict.CANNOT_DETERMINE,
                        Confidence.LOW,
                        f"meta:incomplete:{obligation_id}",
                        detail,
                        "meta_evidence_completeness",
                        "business",
                        resource_id=business_id,
                        location=f"Business: {business_id}",
                        observed_state="Incomplete Meta evidence",
                        expected_state="All required Meta metadata endpoints collected",
                        reason=detail,
                        metadata={"missing_checks": sorted(missing_checks), "business_id": business_id},
                    )
                    continue

                detail = not_observed_details[obligation_id]
                add_evidence(
                    obligation_id,
                    Verdict.NOT_OBSERVED,
                    Confidence.HIGH,
                    f"meta:evaluation:{obligation_id}",
                    detail,
                    "meta_configuration_evaluation",
                    "business",
                    resource_id=business_id,
                    location=f"Business: {business_id}",
                    observed_state="No violating Meta configuration found in collected metadata",
                    expected_state="No violating Meta configuration",
                    reason="Relevant read-only Meta API metadata was collected and did not show this configuration.",
                    metadata={"business_id": business_id, **state["facts"]},
                )
        except ExternalAPIError as exc:
            detail = f"Meta collection failed before endpoint-specific handling completed: {exc}"
            for obl_id in self.obligations_answered:
                add_failure(
                    [obl_id],
                    "meta:error",
                    "meta_collect",
                    detail,
                    resource_type="business",
                    resource_id=business_id or None,
                    check="meta_collect",
                )
        finally:
            await client.close()

        return AdapterResult(
            evidence=evidence,
            errors=errors,
            partial=bool(errors),
            metadata={
                "business_id": business_id,
                "ad_accounts_scanned": len(set(ad_accounts_to_scan)) if "ad_accounts_to_scan" in locals() else 0,
                "pixels_scanned": len(set(pixels_to_scan)) if "pixels_to_scan" in locals() else 0,
            },
        )
