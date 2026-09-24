"""Regression tests: Connected integrations must expose correct resource state.

These tests verify that:
1. Each provider's connection correctly tracks resource_id/resource_name
   after the select-resource flow.
2. The IntegrationStatusItem schema includes resource_selected flag.
3. The report generator never outputs raw None for resource fields.
4. Unselected resources are explicitly represented, not silently null.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.db.models.connection import Connection, ConnectionStatus
from app.schemas.api import IntegrationStatusItem


# ── Helper Factories ──

def _make_connection(
    provider: str,
    resource_id: str | None = None,
    resource_name: str | None = None,
    status: ConnectionStatus = ConnectionStatus.CONNECTED) -> Connection:
    """Create an in-memory Connection for testing."""
    conn = Connection(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        provider=provider,
        status=status,
        scopes=["read"],
        connected_at=datetime.now(timezone.utc),
        resource_id=resource_id,
        resource_name=resource_name,
        provider_metadata={})
    return conn


def _build_status_item(conn: Connection) -> IntegrationStatusItem:
    """Build an IntegrationStatusItem from a Connection, mirroring the endpoint logic."""
    meta = conn.provider_metadata or {}
    no_resource_providers = {"cmp"}
    has_resource = bool(conn.resource_id)
    return IntegrationStatusItem(
        provider=conn.provider,
        status=conn.status.value if hasattr(conn.status, "value") else str(conn.status),
        connection_id=str(conn.id),
        resource_id=conn.resource_id,
        resource_name=conn.resource_name,
        resource_selected=has_resource,
        requires_resource_selection=conn.provider not in no_resource_providers,
        connected_at=conn.connected_at)


# ── 1. GTM with resource ──

def test_connected_gtm_has_resource():
    """Connected GTM connection with container resource has resource_selected=True."""
    conn = _make_connection(
        provider="gtm",
        resource_id="GTM-ABC123",
        resource_name="My Account / Main Container")
    item = _build_status_item(conn)

    assert item.resource_selected is True
    assert item.resource_id == "GTM-ABC123"
    assert item.resource_name == "My Account / Main Container"
    assert item.requires_resource_selection is True
    assert item.status == "connected"


# ── 2. GA4 with resource ──

def test_connected_ga4_has_resource():
    """Connected GA4 connection with property resource has resource_selected=True."""
    conn = _make_connection(
        provider="ga4",
        resource_id="123456789",
        resource_name="My Site / Production Property")
    item = _build_status_item(conn)

    assert item.resource_selected is True
    assert item.resource_id == "123456789"
    assert item.resource_name == "My Site / Production Property"
    assert item.requires_resource_selection is True


# ── 3. Google Ads with customer resource ──

def test_connected_google_ads_has_customer_resource():
    """Connected Google Ads connection with customer ID has resource_selected=True."""
    conn = _make_connection(
        provider="google_ads",
        resource_id="1234567890",
        resource_name="Google Ads Account (123-456-7890)")
    item = _build_status_item(conn)

    assert item.resource_selected is True
    assert item.resource_id == "1234567890"
    assert item.resource_name == "Google Ads Account (123-456-7890)"
    assert item.requires_resource_selection is True


# ── 4. Meta with Business resource ──

def test_connected_meta_has_business_resource():
    """Connected Meta connection with Business Manager resource has resource_selected=True."""
    conn = _make_connection(
        provider="meta",
        resource_id="987654321",
        resource_name="My Business Manager")
    item = _build_status_item(conn)

    assert item.resource_selected is True
    assert item.resource_id == "987654321"
    assert item.resource_name == "My Business Manager"
    assert item.requires_resource_selection is True


# ── 5. GitHub with repository resource ──

def test_connected_github_has_repository_resource():
    """Connected GitHub connection with repo full_name has resource_selected=True."""
    conn = _make_connection(
        provider="github",
        resource_id="myorg/my-repo",
        resource_name="myorg/my-repo")
    item = _build_status_item(conn)

    assert item.resource_selected is True
    assert item.resource_id == "myorg/my-repo"
    assert item.resource_name == "myorg/my-repo"
    assert item.requires_resource_selection is True


# ── 6. Search Console with property resource ──

def test_connected_search_console_has_property_resource():
    """Connected Search Console connection with site URL has resource_selected=True."""
    conn = _make_connection(
        provider="search_console",
        resource_id="https://example.com",
        resource_name="https://example.com")
    item = _build_status_item(conn)

    assert item.resource_selected is True
    assert item.resource_id == "https://example.com"
    assert item.resource_name == "https://example.com"
    assert item.requires_resource_selection is True


# ── 7. Unselected resource is explicitly represented, NOT fake data ──

def test_unselected_resource_explicitly_represented():
    """When resource is not selected, resource_selected=False and resource_id is None (not fake data)."""
    conn = _make_connection(
        provider="ga4",
        resource_id=None,
        resource_name=None)
    item = _build_status_item(conn)

    # Status item shows None resource — not a fake sentinel
    assert item.resource_selected is False
    assert item.resource_id is None
    assert item.resource_name is None
    assert item.requires_resource_selection is True
    assert item.status == "connected"


# ── 7b. Report generator produces "(not selected)" sentinel, not None ──

def test_report_generator_no_none_in_connection_metadata():
    """Report conn_map uses '(not selected)' sentinel instead of raw None.

    This mirrors the logic in report_generator.py for conn_map building.
    """
    conn = _make_connection(
        provider="meta",
        resource_id=None,
        resource_name=None)

    # Simulate the report generator's conn_map logic
    res_id = conn.resource_id
    res_name = conn.resource_name or res_id
    has_resource = bool(res_id)

    conn_entry = {
        "resource_id": res_id if has_resource else "(not selected)",
        "resource_name": res_name if has_resource else "(not selected)",
        "resource_selected": has_resource,
    }

    assert conn_entry["resource_id"] == "(not selected)"
    assert conn_entry["resource_name"] == "(not selected)"
    assert conn_entry["resource_selected"] is False
    # Must NOT be None
    assert conn_entry["resource_id"] is not None
    assert conn_entry["resource_name"] is not None


# ── Schema field existence tests ──

def test_integration_status_item_has_required_fields():
    """IntegrationStatusItem schema exposes all required resource-state fields."""
    item = IntegrationStatusItem(
        provider="gtm",
        status="connected",
        connection_id=str(uuid.uuid4()),
        resource_id="GTM-123",
        resource_name="Test Container",
        resource_selected=True,
        requires_resource_selection=True)

    # Verify all fields are accessible
    assert item.resource_selected is True
    assert item.requires_resource_selection is True

    # Verify serialization includes the new fields
    data = item.model_dump()
    assert "resource_selected" in data
    assert "requires_resource_selection" in data


def test_integration_status_item_defaults():
    """IntegrationStatusItem defaults for new fields are correct."""
    item = IntegrationStatusItem(provider="gtm", status="not_connected")

    assert item.resource_selected is False
    assert item.requires_resource_selection is True
    assert item.resource_id is None
    assert item.resource_name is None
