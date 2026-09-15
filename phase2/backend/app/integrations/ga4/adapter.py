"""GA4 (Google Analytics 4) adapter.

PHASE2_SPEC Section 4.2.

API: Google Analytics Admin API v1
Scope: https://www.googleapis.com/auth/analytics.readonly

NEVER READ: report data, user-level data, User Activity API,
any endpoint returning individual behaviour.

'The tool has no legitimate need for a single row of event data.'
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

GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GA4_BASE_URL = "https://analyticsadmin.googleapis.com"

# Endpoints that MUST NEVER be called — tested with mock/spy assertions
GA4_FORBIDDEN_ENDPOINTS = [
    # Reporting endpoints — NO event data
    "analyticsdata.googleapis.com",
    "/v1beta/properties/*/runReport",
    "/v1beta/properties/*/batchRunReports",
    "/v1beta/properties/*/runRealtimeReport",
    "/v1beta/properties/*/runPivotReport",
    "/v1beta/properties/*/batchRunPivotReports",
    # User activity endpoints — NO individual behaviour
    "/v1beta/properties/*/userActivity",
    "analyticsreporting.googleapis.com",
    "/v4/reports:batchGet",
    # Data API
    "/v1beta/properties/*/checkCompatibility",
    "/v1alpha/properties/*/runFunnelReport",
]

GA4_SAFE_FIELDS = frozenset({
    "name", "displayName", "propertyType", "industryCategory",
    "timeZone", "currencyCode", "serviceLevel", "createTime", "updateTime",
    "parent", "account", "property",
    # Retention
    "eventDataRetention", "resetUserDataOnNewActivity",
    # Signals
    "state",  # Google Signals state
    # Data streams
    "type", "webStreamData", "measurementId", "firebaseAppId",
    "defaultUri", "enhancedMeasurementSettings",
    # Enhanced measurement
    "streamEnabled", "scrollsEnabled", "outboundClicksEnabled",
    "siteSearchEnabled", "videoEngagementEnabled", "fileDownloadsEnabled",
    "pageChangesEnabled", "formInteractionsEnabled",
    # Custom definitions
    "parameterName", "scope", "description", "disallowAdsPersonalization",
    # Linked services
    "adsWebConversionDataExportScope",
})

GA4_FORBIDDEN_FIELDS = frozenset({
    "email", "phone", "user_id", "customer_id",
    "event_data", "user_activity", "report_data",
    "audience_membership", "user_list",
})


class GA4Adapter(IntegrationAdapter):
    """Google Analytics 4 integration adapter — configuration metadata only."""

    @property
    def id(self) -> str:
        return "ga4"

    @property
    def provider(self) -> str:
        return "google_analytics_4"

    @property
    def required_scopes(self) -> list[str]:
        return [GA4_SCOPE]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("ga4")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            GA4_SCOPE: [
                "S8.retention", "S8.retentionJustification",
                "S9.sharing.crossDevice", "S9.sharing.documented",
                "S6.consentPropagation", "S6.consentMode",
            ],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return GA4_FORBIDDEN_ENDPOINTS

    @property
    def safe_fields(self) -> frozenset[str]:
        return GA4_SAFE_FIELDS

    @property
    def forbidden_fields(self) -> frozenset[str]:
        return GA4_FORBIDDEN_FIELDS

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        access_token = credentials.get("access_token", "")
        client = AuditedHTTPClient(
            provider=self.provider, base_url=GA4_BASE_URL, access_token=access_token,
        )
        try:
            result = await client.get("/v1beta/accountSummaries", operation="list_account_summaries")
            summaries = result.get("accountSummaries", [])
            return ConnectionInfo(
                provider=self.provider,
                scopes=[GA4_SCOPE],
                connected_at=datetime.now(timezone.utc),
                metadata={"account_count": len(summaries)},
            )
        finally:
            await client.close()

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        return HealthStatus(
            healthy=True, provider=self.provider,
            message="Connected", checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
    ) -> AdapterResult:
        """Collect GA4 configuration metadata — NEVER event/report data."""
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        warnings: list[str] = []
        now = datetime.now(timezone.utc)

        client = AuditedHTTPClient(
            provider=self.provider, base_url=GA4_BASE_URL,
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            # List properties
            summaries = await client.get(
                "/v1beta/accountSummaries", operation="list_account_summaries",
            )

            properties_found = []
            for summary in summaries.get("accountSummaries", []):
                for prop in summary.get("propertySummaries", []):
                    properties_found.append(prop)

            if not properties_found:
                evidence.append(EvidenceItem(
                    obligation_id="S8.retention",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="ga4:list_properties", observed_at=now,
                    detail="No GA4 properties found.",
                ))
                return AdapterResult(evidence=evidence, errors=errors)

            for prop_summary in properties_found:
                prop_name = prop_summary.get("property", "")
                if not prop_name:
                    continue

                try:
                    # Get property details
                    _ = await client.get(
                        f"/v1beta/{prop_name}", operation="get_property",
                    )

                    # Data retention settings
                    try:
                        retention = await client.get(
                            f"/v1beta/{prop_name}/dataRetentionSettings",
                            operation="get_data_retention",
                        )
                        event_retention = retention.get("eventDataRetention", "UNKNOWN")
                        reset_on_activity = retention.get("resetUserDataOnNewActivity", False)

                        # Flag maximum retention without justification
                        if "FIFTY" in str(event_retention).upper() or "FOURTEEN" in str(event_retention).upper():
                            evidence.append(EvidenceItem(
                                obligation_id="S8.retention",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source="ga4:data_retention", observed_at=now,
                                detail=f"Data retention: {event_retention}, reset on activity: {reset_on_activity}",
                                raw_metadata={"retention": event_retention, "reset_on_activity": reset_on_activity},
                            ))
                        else:
                            evidence.append(EvidenceItem(
                                obligation_id="S8.retentionJustification",
                                verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
                                source="ga4:data_retention", observed_at=now,
                                detail=f"Retention set to maximum ({event_retention}) — requires documented justification.",
                                raw_metadata={"retention": event_retention},
                            ))
                    except ExternalAPIError:
                        errors.append(f"Could not read retention for {prop_name}")

                    # Google Signals
                    try:
                        signals = await client.get(
                            f"/v1beta/{prop_name}/googleSignalsSettings",
                            operation="get_google_signals",
                        )
                        signals_state = signals.get("state", "UNKNOWN")
                        if signals_state == "GOOGLE_SIGNALS_ENABLED":
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.crossDevice",
                                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                source="ga4:google_signals", observed_at=now,
                                detail="Google Signals is enabled — cross-device identification active.",
                                raw_metadata={"signals_state": signals_state},
                            ))
                        else:
                            evidence.append(EvidenceItem(
                                obligation_id="S9.sharing.crossDevice",
                                verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
                                source="ga4:google_signals", observed_at=now,
                                detail=f"Google Signals state: {signals_state}",
                            ))
                    except ExternalAPIError:
                        errors.append(f"Could not read Signals for {prop_name}")

                    # Linked services (Ads, BigQuery, etc.)
                    for link_type in ["googleAdsLinks", "bigQueryLinks", "searchAds360Links", "displayVideo360AdvertiserLinks"]:
                        try:
                            links = await client.get(
                                f"/v1beta/{prop_name}/{link_type}",
                                operation=f"list_{link_type}",
                            )
                            link_items = links.get(link_type.replace("Links", "Link").replace("links", "Link"), [])
                            if not link_items:
                                link_items = links.get(link_type, [])
                            if link_items:
                                evidence.append(EvidenceItem(
                                    obligation_id="S9.sharing.documented",
                                    verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                                    source=f"ga4:{link_type}", observed_at=now,
                                    detail=f"Linked service found: {link_type} ({len(link_items)} links)",
                                    raw_metadata={"link_type": link_type, "count": len(link_items)},
                                ))
                        except ExternalAPIError:
                            pass  # Not all link types exist for all properties

                    # Data streams
                    try:
                        streams = await client.get(
                            f"/v1beta/{prop_name}/dataStreams",
                            operation="list_data_streams",
                        )
                        for stream in streams.get("dataStreams", []):
                            stream_type = stream.get("type", "")
                            if stream_type == "WEB_DATA_STREAM":
                                em_settings = stream.get("webStreamData", {}).get("enhancedMeasurementSettings")
                                if em_settings:
                                    evidence.append(EvidenceItem(
                                        obligation_id="S8.dataMinimization",
                                        verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                                        source="ga4:enhanced_measurement", observed_at=now,
                                        detail=f"Enhanced measurement enabled on stream {stream.get('displayName', '')}",
                                    ))
                    except ExternalAPIError:
                        pass

                    # Custom dimensions/metrics — names only, check for PII patterns
                    try:
                        custom_dims = await client.get(
                            f"/v1beta/{prop_name}/customDimensions",
                            operation="list_custom_dimensions",
                        )
                        for dim in custom_dims.get("customDimensions", []):
                            dim_name = dim.get("parameterName", "")
                            if any(pii in dim_name.lower() for pii in [
                                "email", "phone", "name", "address", "user_id", "customer"
                            ]):
                                evidence.append(EvidenceItem(
                                    obligation_id="S8.dataMinimization",
                                    verdict=Verdict.OBSERVED, confidence=Confidence.MEDIUM,
                                    source="ga4:custom_dimensions", observed_at=now,
                                    detail=f"Custom dimension '{dim_name}' has a name suggesting PII. Flag for human review.",
                                    raw_metadata={"parameter_name": dim_name, "scope": dim.get("scope", "")},
                                ))
                    except ExternalAPIError:
                        pass

                except ExternalAPIError as prop_err:
                    errors.append(f"Error analyzing property {prop_name}: {prop_err}")

            return AdapterResult(evidence=evidence, errors=errors, warnings=warnings)

        except ExternalAPIError as exc:
            logger.error("ga4_collection_failed", error=str(exc))
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S8.retention",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="ga4:error", observed_at=now,
                    detail=f"GA4 collection failed: {exc}",
                )],
                errors=[str(exc)], partial=True,
            )
        finally:
            await client.close()
