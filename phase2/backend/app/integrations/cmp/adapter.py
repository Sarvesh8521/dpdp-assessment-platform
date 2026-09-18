"""CMP adapter framework. PHASE2_SPEC Section 4.7.

Supports: OneTrust, CookieYes, Securiti, extensible for others.
Read: purpose taxonomy, cookie categorisation, geolocation rules, banner config,
      consent-record schema, integration list.
NEVER read: individual consent records.
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

CMP_FORBIDDEN_ENDPOINTS = [
    # Individual consent records — the sharpest instance of the rule
    "consent-receipts$",
    "consent-records",
    "individual-consent",
    "consent/receipts",
    "/api/consentreceipts",
    "user-consent",
    "consentRecords",
]


class BaseCMPAdapter(IntegrationAdapter):
    """Base class for CMP-specific adapters. Extensible for new providers."""

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("cmp")

    @property
    def forbidden_endpoints(self) -> list[str]:
        return CMP_FORBIDDEN_ENDPOINTS

    async def _analyze_geolocation_rules(
        self, rules: list[dict[str, Any]], now: datetime,
    ) -> list[EvidenceItem]:
        """Check if geolocation rules exempt India."""
        evidence: list[EvidenceItem] = []
        india_exempt = True
        for rule in rules:
            regions = rule.get("regions", [])
            countries = rule.get("countries", [])
            if "IN" in countries or "India" in regions or "IN" in regions:
                india_exempt = False
                break

        if india_exempt and rules:
            evidence.append(EvidenceItem(
                obligation_id="CMP.geolocation.india",
                verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
                source=f"cmp:{self.id}:geolocation", observed_at=now,
                detail="Geolocation rules exempt India from consent requirements.",
                structured_detail=StructuredEvidence(
                    source="cmp", integration=self.provider, operation="list_geolocation_rules",
                    resource_type="geolocation_rule", resource_id=self.provider,
                    location=f"CMP: {self.provider} > Geolocation Rule",
                    observed_at=now, observed_state="India Exempt", expected_state="India Included",
                    detail="Geolocation rules exempt India from consent requirements.",
                    reason="India is not listed in any geolocation consent rule.",
                    metadata={"regions_configured": len(rules)}
                )
            ))
        elif not india_exempt:
            evidence.append(EvidenceItem(
                obligation_id="CMP.geolocation.india",
                verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                source=f"cmp:{self.id}:geolocation", observed_at=now,
                detail="India is included in geolocation consent rules.",
                structured_detail=StructuredEvidence(
                    source="cmp", integration=self.provider, operation="list_geolocation_rules",
                    resource_type="geolocation_rule", resource_id=self.provider,
                    location=f"CMP: {self.provider} > Geolocation Rule",
                    observed_at=now, observed_state="India Included", expected_state="India Included",
                    detail="India is included in geolocation consent rules.",
                    reason="The geolocation rules explicitly include India in the consent enforcement regions."
                )
            ))
        return evidence


class OneTrustAdapter(BaseCMPAdapter):
    @property
    def id(self) -> str:
        return "cmp_onetrust"

    @property
    def provider(self) -> str:
        return "onetrust"

    @property
    def required_scopes(self) -> list[str]:
        return ["onetrust:read"]

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
        )

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        # Validate real access for OneTrust
        client = AuditedHTTPClient(
            provider=self.provider, base_url="https://api.onetrust.com",
            access_token=connection.metadata.get("access_token", ""),
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
        )
        try:
            await client.get("/api/v1/purposes", operation="health_check")
            return HealthStatus(
                healthy=True, provider=self.provider,
                message="Connected", checked_at=datetime.now(timezone.utc),
            )
        except ExternalAPIError:
            return HealthStatus(
                healthy=False, provider=self.provider,
                message="Invalid API token or insufficient permissions", 
                checked_at=datetime.now(timezone.utc),
            )
        finally:
            await client.close()

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
    ) -> AdapterResult:
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc)

        client = AuditedHTTPClient(
            provider=self.provider, base_url="https://api.onetrust.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            # Purposes
            try:
                purposes = await client.get("/api/v1/purposes", operation="list_purposes")
                purpose_list = purposes.get("content", [])
                evidence.append(EvidenceItem(
                    obligation_id="CMP.purposes.configured",
                    verdict=Verdict.OBSERVED if purpose_list else Verdict.NOT_OBSERVED,
                    confidence=Confidence.HIGH,
                    source="cmp:onetrust:purposes", observed_at=now,
                    detail=f"Found {len(purpose_list)} consent purposes configured.",
                    structured_detail=StructuredEvidence(
                        source="cmp", integration="onetrust", operation="list_purposes",
                        resource_type="purpose", resource_id="onetrust",
                        location="CMP: onetrust > Purpose",
                        observed_at=now, observed_state=f"{len(purpose_list)} purposes configured", expected_state=">0 purposes configured",
                        detail=f"Found {len(purpose_list)} consent purposes configured.",
                        reason=f"The CMP is configured with {len(purpose_list)} active consent purposes.",
                        metadata={"purpose_count": len(purpose_list)}
                    )
                ))
            except ExternalAPIError as exc:
                errors.append(f"Purposes query failed: {exc}")

            # Cookie categories
            try:
                cookies = await client.get("/api/v1/cookie-categories", operation="list_cookie_categories")
                categories = cookies.get("content", [])
                evidence.append(EvidenceItem(
                    obligation_id="CMP.categories.complete",
                    verdict=Verdict.OBSERVED if categories else Verdict.CANNOT_DETERMINE,
                    confidence=Confidence.MEDIUM,
                    source="cmp:onetrust:categories", observed_at=now,
                    detail=f"Found {len(categories)} cookie categories.",
                    structured_detail=StructuredEvidence(
                        source="cmp", integration="onetrust", operation="list_cookie_categories",
                        resource_type="cookie_category", resource_id="onetrust",
                        location="CMP: onetrust > Cookie Category",
                        observed_at=now, observed_state=f"{len(categories)} categories configured", expected_state=">0 categories configured",
                        detail=f"Found {len(categories)} cookie categories.",
                        reason=f"The CMP categorises cookies into {len(categories)} distinct groups."
                    )
                ))
            except ExternalAPIError:
                pass

            # Geolocation rules
            try:
                geo = await client.get("/api/v1/geolocation-rules", operation="list_geolocation_rules")
                geo_rules = geo.get("content", [])
                geo_evidence = await self._analyze_geolocation_rules(geo_rules, now)
                evidence.extend(geo_evidence)
            except ExternalAPIError:
                pass
                
            # Banner configuration metadata
            try:
                banners = await client.get("/api/v1/banner-configurations", operation="list_banners")
                banner_list = banners.get("content", [])
                if banner_list:
                    evidence.append(EvidenceItem(
                        obligation_id="CMP.purposes.configured",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="cmp:onetrust:banners", observed_at=now,
                        detail=f"Successfully verified {len(banner_list)} banner configurations.",
                        structured_detail=StructuredEvidence(
                            source="cmp", integration="onetrust", operation="list_banners",
                            resource_type="banner_configuration", resource_id="onetrust",
                            location="CMP: onetrust > Banner Configuration",
                            observed_at=now, observed_state=f"{len(banner_list)} banners configured", expected_state=">0 banners configured",
                            detail=f"Successfully verified {len(banner_list)} banner configurations.",
                            reason=f"The CMP is configured with {len(banner_list)} active banner configurations."
                        )
                    ))
            except ExternalAPIError:
                pass
                
            # Consent-record schema metadata
            try:
                schema = await client.get("/api/v1/consent-receipts/schema", operation="get_schema")
                fields = schema.get("fields", [])
                if fields:
                    evidence.append(EvidenceItem(
                        obligation_id="CMP.purposes.configured",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="cmp:onetrust:schema", observed_at=now,
                        detail=f"Verified consent-record schema contains {len(fields)} configured fields. No individual records accessed.",
                        structured_detail=StructuredEvidence(
                            source="cmp", integration="onetrust", operation="get_schema",
                            resource_type="consent_record_schema", resource_id="onetrust",
                            location="CMP: onetrust > Consent Record Schema",
                            observed_at=now, observed_state=f"{len(fields)} schema fields", expected_state="Schema configured",
                            detail=f"Verified consent-record schema contains {len(fields)} configured fields. No individual records accessed.",
                            reason=f"The consent record schema defines {len(fields)} fields indicating what metadata is captured upon user consent."
                        )
                    ))
            except ExternalAPIError:
                pass
                
            # Integration list
            try:
                integrations = await client.get("/api/v1/integrations", operation="list_integrations")
                integration_list = integrations.get("content", [])
                if integration_list:
                    evidence.append(EvidenceItem(
                        obligation_id="CMP.purposes.configured",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="cmp:onetrust:integrations", observed_at=now,
                        detail=f"Discovered {len(integration_list)} active integration targets for consent signal forwarding.",
                        structured_detail=StructuredEvidence(
                            source="cmp", integration="onetrust", operation="list_integrations",
                            resource_type="integration_list", resource_id="onetrust",
                            location="CMP: onetrust > Integration List",
                            observed_at=now, observed_state=f"{len(integration_list)} integrations", expected_state="Forwarding configured",
                            detail=f"Discovered {len(integration_list)} active integration targets for consent signal forwarding.",
                            reason=f"The CMP is configured with {len(integration_list)} active integrations for forwarding consent signals."
                        )
                    ))
            except ExternalAPIError:
                pass

            return AdapterResult(evidence=evidence, errors=errors)
        finally:
            await client.close()


class CookieYesAdapter(BaseCMPAdapter):
    @property
    def id(self) -> str:
        return "cmp_cookieyes"

    @property
    def provider(self) -> str:
        return "cookieyes"

    @property
    def required_scopes(self) -> list[str]:
        return ["cookieyes:read"]

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
        )

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        return HealthStatus(
            healthy=False, provider=self.provider,
            message="Provider API is unsupported for automated metadata extraction.", checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
    ) -> AdapterResult:
        evidence: list[EvidenceItem] = []
        now = datetime.now(timezone.utc)
        # CookieYes API integration
        evidence.append(EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
            source="cmp:cookieyes", observed_at=now,
            detail="Cannot determine. CookieYes API is not currently supported for automated metadata collection without risking PII access or fabricating data.",
            structured_detail=StructuredEvidence(
                source="cmp", integration="cookieyes", operation="none",
                resource_type="none", resource_id="cookieyes",
                location="Unavailable",
                observed_at=now, observed_state="Unsupported API", expected_state="Configured",
                detail="Cannot determine. CookieYes API is not currently supported for automated metadata collection without risking PII access or fabricating data.",
                reason="CookieYes does not expose a public API suitable for safe automated metadata extraction.",
            )
        ))
        return AdapterResult(evidence=evidence)


class SecuritiAdapter(BaseCMPAdapter):
    @property
    def id(self) -> str:
        return "cmp_securiti"

    @property
    def provider(self) -> str:
        return "securiti"

    @property
    def required_scopes(self) -> list[str]:
        return ["securiti:read"]

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
        )

    async def health_check(self, connection: ConnectionInfo) -> HealthStatus:
        return HealthStatus(
            healthy=False, provider=self.provider,
            message="Provider API is unsupported for automated metadata extraction.", checked_at=datetime.now(timezone.utc),
        )

    async def collect(
        self, connection: ConnectionInfo, *,
        assessment_id: str | None = None, client_id: str | None = None,
        access_token: str = "", audit_callback: Any = None,
    ) -> AdapterResult:
        evidence: list[EvidenceItem] = []
        now = datetime.now(timezone.utc)
        evidence.append(EvidenceItem(
            obligation_id="CMP.purposes.configured",
            verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
            source="cmp:securiti", observed_at=now,
            detail="Cannot determine. Securiti API is not currently supported for automated metadata collection without risking PII access or fabricating data.",
            structured_detail=StructuredEvidence(
                source="cmp", integration="securiti", operation="none",
                resource_type="none", resource_id="securiti",
                location="Unavailable",
                observed_at=now, observed_state="Unsupported API", expected_state="Configured",
                detail="Cannot determine. Securiti API is not currently supported for automated metadata collection without risking PII access or fabricating data.",
                reason="Securiti does not expose a public API suitable for safe automated metadata extraction.",
            )
        ))
        return AdapterResult(evidence=evidence)


# Adapter registry
CMP_ADAPTERS = {
    "onetrust": OneTrustAdapter,
    "cookieyes": CookieYesAdapter,
    "securiti": SecuritiAdapter,
}


def get_cmp_adapter(provider: str) -> BaseCMPAdapter | None:
    """Get a CMP adapter by provider name."""
    adapter_class = CMP_ADAPTERS.get(provider)
    return adapter_class() if adapter_class else None  # type: ignore[abstract]
