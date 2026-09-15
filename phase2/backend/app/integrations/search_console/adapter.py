"""Search Console adapter. PHASE2_SPEC Section 4.5.

Scope: webmasters.readonly
Read: verified properties, sitemaps
NEVER read: search query data

Purpose: finds subdomains and properties the client forgot to mention.
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

SC_FORBIDDEN_ENDPOINTS = [
    "/webmasters/v3/sites/*/searchAnalytics/query",
    "searchAnalytics",
    "/v1/urlInspection",
    "urlInspection",
    "query",  # Search query data
]


class SearchConsoleAdapter(IntegrationAdapter):
    @property
    def id(self) -> str:
        return "search_console"

    @property
    def provider(self) -> str:
        return "google_search_console"

    @property
    def required_scopes(self) -> list[str]:
        return ["https://www.googleapis.com/auth/webmasters.readonly"]

    @property
    def obligations_answered(self) -> list[str]:
        return get_obligations_for_integration("search_console")

    @property
    def scope_obligation_map(self) -> dict[str, list[str]]:
        return {
            "https://www.googleapis.com/auth/webmasters.readonly": ["S10.scope.properties"],
        }

    @property
    def forbidden_endpoints(self) -> list[str]:
        return SC_FORBIDDEN_ENDPOINTS

    async def connect(self, credentials: dict[str, Any]) -> ConnectionInfo:
        return ConnectionInfo(
            provider=self.provider, scopes=self.required_scopes,
            connected_at=datetime.now(timezone.utc),
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
        declared_properties: list[str] | None = None,
    ) -> AdapterResult:
        evidence: list[EvidenceItem] = []
        errors: list[str] = []
        now = datetime.now(timezone.utc)
        declared = set(declared_properties or [])

        # Get the selected property URL (siteUrl) from the connection metadata
        # The frontend stores it as resource_id or ad_account_id depending on how it's passed
        meta = connection.metadata or {}
        site_url = meta.get("resource_id") or ""

        client = AuditedHTTPClient(
            provider=self.provider,
            base_url="https://www.googleapis.com",
            access_token=access_token,
            forbidden_endpoints=self.forbidden_endpoints,
            obligations_served=self.obligations_answered,
            client_id=client_id, assessment_id=assessment_id,
            audit_callback=audit_callback,
        )

        try:
            # 1. Fetch Verified Properties
            try:
                result = await client.get(
                    "/webmasters/v3/sites", operation="list_sites",
                )
                sites = result.get("siteEntry", [])

                verified_properties = []
                undeclared = []

                for site in sites:
                    url = site.get("siteUrl", "")
                    permission = site.get("permissionLevel", "")
                    verified_properties.append({
                        "url": url,
                        "permission": permission,
                    })
                    if declared and url not in declared:
                        undeclared.append(url)

                if undeclared:
                    evidence.append(EvidenceItem(
                        obligation_id="S10.scope.properties",
                        verdict=Verdict.NOT_OBSERVED, confidence=Confidence.HIGH,
                        source="search_console:verified_sites", observed_at=now,
                        detail=(
                            f"Found {len(undeclared)} verified properties not declared in scope: "
                            f"{', '.join(undeclared)}"
                        ),
                        raw_metadata={"undeclared": undeclared, "total_verified": len(verified_properties)},
                    ))
                else:
                    evidence.append(EvidenceItem(
                        obligation_id="S10.scope.properties",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="search_console:verified_sites", observed_at=now,
                        detail=f"All {len(verified_properties)} verified properties are declared in scope.",
                        raw_metadata={"total_verified": len(verified_properties)},
                    ))
            except ExternalAPIError as exc:
                errors.append(f"Property discovery failed: {exc}")
                
            # 2. Fetch Sitemaps for selected property
            if site_url:
                from urllib.parse import quote
                safe_url = quote(site_url, safe='')
                try:
                    sitemaps_result = await client.get(
                        f"/webmasters/v3/sites/{safe_url}/sitemaps", 
                        operation="list_sitemaps"
                    )
                    sitemaps = sitemaps_result.get("sitemap", [])
                    sitemap_list = []
                    
                    for sm in sitemaps:
                        sitemap_list.append({
                            "path": sm.get("path", ""),
                            "type": sm.get("type", "Unknown"),
                            "warnings": sm.get("warnings", 0),
                            "errors": sm.get("errors", 0),
                            "isPending": sm.get("isPending", False),
                            "isSitemapsIndex": sm.get("isSitemapsIndex", False),
                        })
                        
                    evidence.append(EvidenceItem(
                        obligation_id="S10.scope.properties",
                        verdict=Verdict.OBSERVED, confidence=Confidence.HIGH,
                        source="search_console:sitemaps", observed_at=now,
                        detail=f"Found {len(sitemap_list)} sitemaps for property {site_url}.",
                        raw_metadata={"sitemaps": sitemap_list, "site_url": site_url}
                    ))
                except ExternalAPIError as exc:
                    errors.append(f"Sitemap collection failed for {site_url}: {exc}")

            return AdapterResult(evidence=evidence, errors=errors)

        except Exception as exc:
            return AdapterResult(
                evidence=[EvidenceItem(
                    obligation_id="S10.scope.properties",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="search_console:error", observed_at=now,
                    detail=f"Search Console collection failed: {exc}",
                )],
                errors=[str(exc)], partial=True,
            )
        finally:
            await client.close()
