"""Resource fetchers for integration providers.

After OAuth authentication, these functions fetch the available resources
(containers, properties, repos, businesses, etc.) that the user can select.

SECURITY: Uses only read-only API endpoints. Never writes data.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResourceFetchError(Exception):
    """Failed to fetch resources from provider."""
    pass


async def fetch_gtm_containers(access_token: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch GTM accounts and containers."""
    resources = []
    async with AuditedHTTPClient(
        provider="gtm", access_token=access_token, audit_callback=audit_callback,
        client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.gtm"]
    ) as client:
        try:
            resp = await client.get(
                "https://www.googleapis.com/tagmanager/v2/accounts",
                operation="gtm_fetch_accounts"
            )
            accounts = resp.get("account", [])

            for account in accounts:
                account_id = account.get("accountId", "")
                account_path = account.get("path", f"accounts/{account_id}")

                containers_resp = await client.get(
                    f"https://www.googleapis.com/tagmanager/v2/{account_path}/containers",
                    operation="gtm_fetch_containers"
                )
                containers = containers_resp.get("container", [])
                for container in containers:
                    resources.append({
                        "id": container.get("containerId", ""),
                        "name": f"{account.get('name', 'Unknown')} / {container.get('name', 'Unnamed')}",
                        "metadata": {
                            "account_id": account_id,
                            "account_name": account.get("name"),
                            "container_name": container.get("name"),
                            "public_id": container.get("publicId"),
                        },
                    })
        except ExternalAPIError as exc:
            logger.warning(f"GTM accounts fetch failed: {exc}")
            raise ResourceFetchError(f"Failed to fetch GTM accounts: {exc}")

    return resources

async def fetch_ga4_properties(access_token: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch GA4 properties."""
    resources = []
    async with AuditedHTTPClient(
        provider="ga4", access_token=access_token, audit_callback=audit_callback,
        client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.ga4"]
    ) as client:
        try:
            resp = await client.get(
                "https://analyticsadmin.googleapis.com/v1beta/accountSummaries",
                operation="ga4_fetch_account_summaries"
            )
            summaries = resp.get("accountSummaries", [])
            for account in summaries:
                account_name = account.get("displayName", "Unknown")
                for prop in account.get("propertySummaries", []):
                    prop_name = prop.get("displayName", "Unnamed")
                    prop_id = prop.get("property", "").replace("properties/", "")
                    resources.append({
                        "id": prop_id,
                        "name": f"{account_name} / {prop_name}",
                        "metadata": {
                            "account": account.get("account"),
                            "property_resource": prop.get("property"),
                        },
                    })
        except ExternalAPIError as exc:
            logger.warning(f"GA4 account summaries fetch failed: {exc}")
            raise ResourceFetchError(f"Failed to fetch GA4 properties: {exc}")

    return resources

async def fetch_search_console_sites(access_token: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch Search Console verified properties."""
    resources = []
    async with AuditedHTTPClient(
        provider="search_console", access_token=access_token, audit_callback=audit_callback,
        client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.search_console"]
    ) as client:
        try:
            resp = await client.get(
                "https://www.googleapis.com/webmasters/v3/sites",
                operation="search_console_fetch_sites"
            )
            sites = resp.get("siteEntry", [])
            for site in sites:
                site_url = site.get("siteUrl", "")
                resources.append({
                    "id": site_url,
                    "name": site_url,
                    "metadata": {
                        "permission_level": site.get("permissionLevel"),
                    },
                })
        except ExternalAPIError as exc:
            logger.warning(f"Search Console sites fetch failed: {exc}")
            raise ResourceFetchError(f"Failed to fetch Search Console sites: {exc}")

    return resources

async def fetch_google_ads_customers(access_token: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch accessible Google Ads customer accounts."""
    resources = []
    settings = get_settings()
    dev_token = getattr(settings, "google_ads_developer_token", None) or os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")

    if dev_token:
        async with AuditedHTTPClient(
            provider="google_ads", access_token=access_token, audit_callback=audit_callback,
            client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.google_ads"]
        ) as client:
            try:
                resp = await client.get(
                    "https://googleads.googleapis.com/v17/customers:listAccessibleCustomers",
                    headers={"developer-token": dev_token},
                    operation="google_ads_list_customers"
                )
                for res_name in resp.get("resourceNames", []):
                    cid = res_name.replace("customers/", "")
                    formatted_cid = f"{cid[:3]}-{cid[3:6]}-{cid[6:]}" if len(cid) == 10 else cid
                    resources.append({
                        "id": cid,
                        "name": f"Google Ads Account ({formatted_cid})",
                        "metadata": {"resource_name": res_name, "customer_id": formatted_cid},
                    })
            except ExternalAPIError as exc:
                logger.warning("google_ads_list_customers_error", error=str(exc))

    return resources

async def validate_google_ads_customer(
    access_token: str, customer_id: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None
) -> dict[str, Any] | None:
    """Validate a Google Ads customer ID."""
    clean_id = customer_id.replace("-", "").strip()
    if not clean_id.isdigit() or len(clean_id) != 10:
        return None

    formatted = f"{clean_id[:3]}-{clean_id[3:6]}-{clean_id[6:]}"
    settings = get_settings()
    dev_token = getattr(settings, "google_ads_developer_token", None) or os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN", "")

    if dev_token:
        async with AuditedHTTPClient(
            provider="google_ads", access_token=access_token, audit_callback=audit_callback,
            client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.google_ads"]
        ) as client:
            try:
                resp = await client.get(
                    f"https://googleads.googleapis.com/v17/customers/{clean_id}",
                    headers={"developer-token": dev_token},
                    operation="google_ads_validate_customer"
                )
                account_name = resp.get("descriptiveName", f"Google Ads Account ({formatted})")
                return {
                    "id": clean_id,
                    "name": account_name,
                    "metadata": {"customer_id": formatted, "customer_id_normalized": clean_id},
                }
            except ExternalAPIError as exc:
                logger.warning("google_ads_validation_network_error", error=str(exc))

    return {
        "id": clean_id,
        "name": f"Google Ads Account ({formatted})",
        "metadata": {
            "customer_id": formatted,
            "customer_id_normalized": clean_id,
        },
    }

async def fetch_meta_businesses(access_token: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch Meta Business Manager businesses."""
    resources = []
    async with AuditedHTTPClient(
        provider="meta", access_token=access_token, audit_callback=audit_callback,
        client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.meta"]
    ) as client:
        try:
            resp = await client.get(
                "https://graph.facebook.com/v21.0/me/businesses",
                params={"fields": "id,name"},
                operation="meta_fetch_businesses"
            )
            businesses = resp.get("data", [])
            for biz in businesses:
                resources.append({
                    "id": biz.get("id", ""),
                    "name": biz.get("name", "Unnamed Business"),
                    "metadata": {},
                })
        except ExternalAPIError as exc:
            logger.warning(f"Meta businesses fetch failed: {exc}")
            raise ResourceFetchError(f"Failed to fetch Meta businesses: {exc}")

    return resources

async def fetch_meta_business_assets(
    access_token: str, business_id: str, audit_callback: Any = None, client_id: str | None = None, assessment_id: str | None = None
) -> list[dict[str, Any]]:
    """Fetch assets for a specific Meta Business."""
    assets = []
    async with AuditedHTTPClient(
        provider="meta", access_token=access_token, audit_callback=audit_callback,
        client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.meta"]
    ) as client:
        try:
            resp = await client.get(
                f"https://graph.facebook.com/v21.0/{business_id}/owned_ad_accounts",
                params={"fields": "id,name,account_id"},
                operation="meta_fetch_ad_accounts"
            )
            for item in resp.get("data", []):
                assets.append({
                    "id": item.get("id", ""),
                    "name": f"Ad Account: {item.get('name', item.get('account_id', ''))}",
                    "type": "ad_account",
                })
        except ExternalAPIError:
            pass

        try:
            resp = await client.get(
                f"https://graph.facebook.com/v21.0/{business_id}/owned_pixels",
                params={"fields": "id,name"},
                operation="meta_fetch_pixels"
            )
            for item in resp.get("data", []):
                assets.append({
                    "id": item.get("id", ""),
                    "name": f"Pixel: {item.get('name', '')}",
                    "type": "pixel",
                })
        except ExternalAPIError:
            pass

    return assets

async def fetch_github_repos(
    access_token: str,
    installation_id: str = "",
    audit_callback: Any = None,
    client_id: str | None = None,
    assessment_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch GitHub repositories accessible to the GitHub App installation.

    PHASE2_SPEC Section 4.6: GitHub App only, scoped to named repositories.
    Uses ONLY /installation/repositories with an installation access token.
    Never calls /user/repos or accepts PATs.

    The access_token here must be an installation access token obtained via
    POST /app/installations/{id}/access_tokens (JWT-authenticated).
    """
    resources: list[dict[str, Any]] = []

    async with AuditedHTTPClient(
        provider="github", access_token=access_token, audit_callback=audit_callback,
        client_id=client_id, assessment_id=assessment_id, obligations_served=["APP.auth.github"]
    ) as client:
        try:
            resp = await client.get(
                "https://api.github.com/installation/repositories",
                params={"per_page": 100},
                operation="github_fetch_installation_repos"
            )
            if resp:
                data = resp if isinstance(resp, dict) else {}
                repos = data.get("repositories", [])
                for repo in repos:
                    resources.append({
                        "id": repo.get("full_name", ""),
                        "name": repo.get("full_name", ""),
                        "metadata": {
                            "html_url": repo.get("html_url"),
                            "private": repo.get("private", False),
                            "default_branch": repo.get("default_branch"),
                            "installation_id": installation_id,
                        },
                    })
                logger.info(
                    "github_repos_fetched",
                    count=len(resources),
                    endpoint="installation/repositories",
                )
        except Exception as exc:
            logger.warning(
                "github_installation_repos_exception",
                error=str(exc),
            )
            raise ResourceFetchError(
                f"Failed to fetch GitHub App installation repos: {exc}"
            )

    if not resources:
        raise ResourceFetchError(
            "No repositories found — ensure the GitHub App is installed "
            "and has access to at least one repository"
        )

    return resources

