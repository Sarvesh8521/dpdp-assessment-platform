"""Adapter Registry."""

from app.integrations.gtm.adapter import GTMAdapter
from app.integrations.ga4.adapter import GA4Adapter
from app.integrations.google_ads.adapter import GoogleAdsAdapter
from app.integrations.search_console.adapter import SearchConsoleAdapter
from app.integrations.cmp.adapter import OneTrustAdapter
from app.integrations.meta.adapter import MetaAdapter
from app.integrations.github_adapter.adapter import GitHubAdapter
from app.integrations.base.adapter import IntegrationAdapter

ADAPTERS: dict[str, type[IntegrationAdapter]] = {
    "GTM": GTMAdapter,
    "gtm": GTMAdapter,
    "google_tag_manager": GTMAdapter,
    "GA4": GA4Adapter,
    "ga4": GA4Adapter,
    "google_analytics_4": GA4Adapter,
    "GoogleAds": GoogleAdsAdapter,
    "google_ads": GoogleAdsAdapter,
    "SearchConsole": SearchConsoleAdapter,
    "search_console": SearchConsoleAdapter,
    "google_search_console": SearchConsoleAdapter,
    "CMP": OneTrustAdapter,
    "cmp": OneTrustAdapter,
    "onetrust": OneTrustAdapter,
    "Meta": MetaAdapter,
    "meta": MetaAdapter,
    "GitHub": GitHubAdapter,
    "github": GitHubAdapter,
}

def get_adapter(integration_id: str) -> IntegrationAdapter | None:
    # Try exact match, then lower-case match
    adapter_class = ADAPTERS.get(integration_id) or ADAPTERS.get(integration_id.lower())
    if adapter_class:
        return adapter_class()
    return None
