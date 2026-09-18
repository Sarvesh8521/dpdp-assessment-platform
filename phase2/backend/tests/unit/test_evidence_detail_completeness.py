"""Tests proving adapter → evidence → finding preserves all evidence fields.

Covers:
1. StructuredEvidence required fields enforcement
2. Metadata PII key rejection
3. Per-adapter evidence completeness (all 7 adapters)
4. FindingService stamps finding_id into evidence_details
"""

import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.domain.evidence import (
    Confidence,
    EvidenceItem,
    StructuredEvidence,
    Verdict,
)


# ─────────────────────────────────────────────────
# 1. StructuredEvidence Required Fields
# ─────────────────────────────────────────────────


class TestStructuredEvidenceRequiredFields:
    """operation, resource_type, observed_at, detail are now required."""

    def test_valid_minimal(self):
        se = StructuredEvidence(
            source="test",
            integration="test",
            operation="test_op",
            resource_type="widget",
            observed_at=datetime.now(timezone.utc),
            detail="A valid detail.",
        )
        assert se.operation == "test_op"
        assert se.resource_type == "widget"

    def test_missing_operation_raises(self):
        with pytest.raises(ValidationError, match="operation"):
            StructuredEvidence(
                source="test",
                integration="test",
                # operation missing
                resource_type="widget",
                observed_at=datetime.now(timezone.utc),
                detail="detail",
            )

    def test_missing_resource_type_raises(self):
        with pytest.raises(ValidationError, match="resource_type"):
            StructuredEvidence(
                source="test",
                integration="test",
                operation="op",
                # resource_type missing
                observed_at=datetime.now(timezone.utc),
                detail="detail",
            )

    def test_missing_observed_at_raises(self):
        with pytest.raises(ValidationError, match="observed_at"):
            StructuredEvidence(
                source="test",
                integration="test",
                operation="op",
                resource_type="widget",
                # observed_at missing
                detail="detail",
            )

    def test_missing_detail_raises(self):
        with pytest.raises(ValidationError, match="detail"):
            StructuredEvidence(
                source="test",
                integration="test",
                operation="op",
                resource_type="widget",
                observed_at=datetime.now(timezone.utc),
                # detail missing
            )

    def test_finding_id_optional(self):
        se = StructuredEvidence(
            source="test",
            integration="test",
            operation="op",
            resource_type="widget",
            observed_at=datetime.now(timezone.utc),
            detail="detail",
        )
        assert se.finding_id is None

    def test_finding_id_settable(self):
        fid = str(uuid.uuid4())
        se = StructuredEvidence(
            source="test",
            integration="test",
            operation="op",
            resource_type="widget",
            observed_at=datetime.now(timezone.utc),
            detail="detail",
            finding_id=fid,
        )
        assert se.finding_id == fid


# ─────────────────────────────────────────────────
# 2. Metadata PII Rejection
# ─────────────────────────────────────────────────


class TestMetadataPIIRejection:
    """Metadata dicts containing PII-suggestive keys must be rejected."""

    FORBIDDEN_KEYS = ["email", "phone", "secret", "token", "password", "aadhaar", "ssn"]

    @pytest.mark.parametrize("bad_key", FORBIDDEN_KEYS)
    def test_forbidden_key_raises(self, bad_key):
        with pytest.raises(ValidationError, match="forbidden"):
            StructuredEvidence(
                source="test",
                integration="test",
                operation="op",
                resource_type="widget",
                observed_at=datetime.now(timezone.utc),
                detail="detail",
                metadata={bad_key: "value"},
            )

    def test_safe_metadata_allowed(self):
        se = StructuredEvidence(
            source="test",
            integration="test",
            operation="op",
            resource_type="widget",
            observed_at=datetime.now(timezone.utc),
            detail="detail",
            metadata={"container": "GTM-XXXX", "total_tags": 5},
        )
        assert se.metadata["container"] == "GTM-XXXX"

    def test_none_metadata_allowed(self):
        se = StructuredEvidence(
            source="test",
            integration="test",
            operation="op",
            resource_type="widget",
            observed_at=datetime.now(timezone.utc),
            detail="detail",
            metadata=None,
        )
        assert se.metadata is None


# ─────────────────────────────────────────────────
# 3. Per-adapter evidence completeness helpers
# ─────────────────────────────────────────────────

# Required fields that every StructuredEvidence must have
REQUIRED_FIELDS = {"source", "integration", "operation", "resource_type", "observed_at", "detail"}

# At least one of these should be populated
IDENTITY_FIELDS = {"resource_id", "resource_name"}


def assert_evidence_complete(evidence_items: list[EvidenceItem], adapter_name: str):
    """Assert every evidence item with structured_detail has all required fields."""
    items_with_detail = [e for e in evidence_items if e.structured_detail is not None]
    assert len(items_with_detail) > 0, f"{adapter_name}: No evidence items with structured_detail"

    for ev in items_with_detail:
        sd = ev.structured_detail
        for field in REQUIRED_FIELDS:
            val = getattr(sd, field, None)
            assert val is not None, (
                f"{adapter_name}: structured_detail.{field} is None "
                f"for source={ev.source}, obligation={ev.obligation_id}"
            )

        # At least one identity field
        has_identity = any(getattr(sd, f, None) is not None for f in IDENTITY_FIELDS)
        assert has_identity, (
            f"{adapter_name}: Neither resource_id nor resource_name is set "
            f"for source={ev.source}, obligation={ev.obligation_id}"
        )


# ─────────────────────────────────────────────────
# 3a. GTM Adapter Evidence Completeness
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.integrations.gtm.adapter.AuditedHTTPClient")
async def test_gtm_evidence_completeness(mock_client_class):
    from app.integrations.gtm.adapter import GTMAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = GTMAdapter()
    connection = ConnectionInfo(provider="gtm", scopes=["read"], connected_at=datetime.now(timezone.utc))

    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()

    async def mock_get(endpoint, **kwargs):
        if "accounts" in endpoint and "containers" not in endpoint:
            return {"account": [{"accountId": "123"}]}
        elif "containers" in endpoint and "versions:live" not in endpoint and "version_headers" not in endpoint:
            return {"container": [{"containerId": "456", "name": "Test", "publicId": "GTM-TEST"}]}
        elif "versions:live" in endpoint:
            return {
                "tag": [{"tagId": "t1", "name": "Tag One", "type": "html",
                         "consentSettings": {"consentStatus": "needed"}, "firingTriggerId": ["tr1"]}],
                "trigger": [{"triggerId": "tr1", "name": "All Pages"}],
                "variable": [],
            }
        elif "version_headers" in endpoint:
            return {"containerVersionHeader": [{"containerVersionId": "v1", "name": "V1", "numTags": 1}]}
        return {}

    mock_instance.get.side_effect = mock_get

    result = await adapter.collect(connection)
    assert_evidence_complete(result.evidence, "GTM")


# ─────────────────────────────────────────────────
# 3b. GA4 Adapter Evidence Completeness
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.integrations.ga4.adapter.AuditedHTTPClient")
async def test_ga4_evidence_completeness(mock_client_class):
    from app.integrations.ga4.adapter import GA4Adapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = GA4Adapter()
    connection = ConnectionInfo(provider="ga4", scopes=["read"], connected_at=datetime.now(timezone.utc))

    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()

    async def mock_get(endpoint, **kwargs):
        if "accountSummaries" in endpoint:
            return {"accountSummaries": [{"propertySummaries": [{"property": "properties/123", "displayName": "Test Prop"}]}]}
        elif "dataRetentionSettings" in endpoint:
            return {"eventDataRetention": "FOURTEEN_MONTHS", "resetUserDataOnNewActivity": True}
        elif "googleSignalsSettings" in endpoint:
            return {"state": "GOOGLE_SIGNALS_ENABLED"}
        elif "dataStreams" in endpoint:
            return {"dataStreams": []}
        elif "customDimensions" in endpoint:
            return {"customDimensions": []}
        elif endpoint.endswith("properties/123"):
            return {"name": "properties/123", "displayName": "Test Prop"}
        return {}

    mock_instance.get.side_effect = mock_get

    result = await adapter.collect(connection)
    assert_evidence_complete(result.evidence, "GA4")


# ─────────────────────────────────────────────────
# 3c. Google Ads Adapter Evidence Completeness
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.integrations.google_ads.adapter.AuditedHTTPClient")
async def test_google_ads_evidence_completeness(mock_client_class):
    from app.integrations.google_ads.adapter import GoogleAdsAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = GoogleAdsAdapter()
    connection = ConnectionInfo(
        provider="google_ads", scopes=["adwords"],
        connected_at=datetime.now(timezone.utc),
        metadata={"developer_token": "test"},
    )

    mock_instance = mock_client_class.return_value
    mock_instance.close = AsyncMock()

    async def mock_post(url, json_body, *args, **kwargs):
        query = json_body.get("query", "")
        if "user_list" in query:
            return [{"results": [{"userList": {"name": "CRM List", "type": "CRM_BASED"}}]}]
        elif "conversion_action" in query:
            return [{"results": [{"conversionAction": {"name": "Purchase", "type": "ENHANCED_CONVERSIONS"}}]}]
        elif "customer.consent_setting" in query:
            return [{"results": [{"customer": {"id": "123", "consentSetting": {"adUserData": "GRANTED"}}}]}]
        elif "customer_client_link" in query:
            return [{"results": [{"customerClientLink": {"status": "ACTIVE", "clientCustomer": "customers/999"}}]}]
        return [{"results": []}]

    mock_instance.post = AsyncMock(side_effect=mock_post)

    result = await adapter.collect(connection, customer_id="12345")
    assert_evidence_complete(result.evidence, "Google Ads")


# ─────────────────────────────────────────────────
# 3d. Search Console Adapter Evidence Completeness
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.integrations.search_console.adapter.AuditedHTTPClient")
async def test_search_console_evidence_completeness(mock_client_class):
    from app.integrations.search_console.adapter import SearchConsoleAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = SearchConsoleAdapter()
    connection = ConnectionInfo(
        provider="search_console", scopes=["read"],
        connected_at=datetime.now(timezone.utc),
        metadata={"resource_id": "https://example.com"},
    )

    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()

    async def mock_get(endpoint, **kwargs):
        if endpoint == "/webmasters/v3/sites":
            return {"siteEntry": [{"siteUrl": "https://example.com", "permissionLevel": "siteOwner"}]}
        elif "sitemaps" in endpoint:
            return {"sitemap": [{"path": "https://example.com/sitemap.xml", "type": "sitemap"}]}
        return {}

    mock_instance.get.side_effect = mock_get

    result = await adapter.collect(connection)
    assert_evidence_complete(result.evidence, "Search Console")


# ─────────────────────────────────────────────────
# 3e. Meta Adapter Evidence Completeness
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.integrations.meta.adapter.AuditedHTTPClient")
async def test_meta_evidence_completeness(mock_client_class):
    from app.integrations.meta.adapter import MetaAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = MetaAdapter()
    connection = ConnectionInfo(
        provider="meta", scopes=["ads_read"],
        connected_at=datetime.now(timezone.utc),
        metadata={"business_id": "biz123", "ad_account_id": "act_456"},
    )

    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()

    async def mock_get(endpoint, **kwargs):
        if "customaudiences" in endpoint:
            return {"data": [{"id": "aud_1", "name": "Lookalike", "subtype": "CUSTOM", "approximate_count": 100}]}
        elif "adspixels" in endpoint:
            return {"data": [{"id": "px_1", "name": "Main Pixel", "automatic_matching_fields": ["em"]}]}
        elif "agencies" in endpoint:
            return {"data": [{"id": "ag_1", "name": "Agency Co"}]}
        elif "client_businesses" in endpoint:
            return {"data": []}
        elif "offline_conversion_data_sets" in endpoint:
            return {"data": [{"id": "oc_1", "name": "Offline Set"}]}
        return {"data": []}

    mock_instance.get.side_effect = mock_get

    result = await adapter.collect(connection, ad_account_id="act_456")
    assert_evidence_complete(result.evidence, "Meta")


# ─────────────────────────────────────────────────
# 3f. GitHub Adapter Evidence Completeness
# ─────────────────────────────────────────────────

import tarfile
import tempfile
import os


def create_test_tarball(target_path, files_dict):
    with tempfile.TemporaryDirectory() as td:
        for fname, content in files_dict.items():
            fpath = Path(td) / fname
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content, encoding="utf-8")
        with tarfile.open(target_path, "w:gz") as tar:
            tar.add(td, arcname="repo")


@pytest.mark.asyncio
@patch("app.integrations.github_adapter.adapter.AuditedHTTPClient")
async def test_github_evidence_completeness(mock_client_class):
    from app.integrations.github_adapter.adapter import GitHubAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = GitHubAdapter()
    connection = ConnectionInfo(
        provider="github", scopes=["contents:read"],
        connected_at=datetime.now(timezone.utc),
    )

    mock_instance = mock_client_class.return_value
    mock_instance.download_file = AsyncMock()
    mock_instance.close = AsyncMock()

    async def mock_download(endpoint, target_path, **kwargs):
        files = {
            "package.json": '{"dependencies": {"react-native-fbsdk-next": "1.0.0"}}',
            "AndroidManifest.xml": '<uses-permission android:name="android.permission.ACCESS_FINE_LOCATION"/>',
            "src/App.ts": 'FirebaseApp.initializeApp();',
        }
        create_test_tarball(target_path, files)

    mock_instance.download_file.side_effect = mock_download

    result = await adapter.collect(connection, repositories=["test/repo"])

    # Verify all evidence items have resource_id
    for ev in result.evidence:
        if ev.structured_detail:
            assert ev.structured_detail.resource_id is not None, (
                f"GitHub: resource_id is None for source={ev.source}"
            )

    assert_evidence_complete(result.evidence, "GitHub")


# ─────────────────────────────────────────────────
# 3g. CMP Adapter Evidence Completeness
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.integrations.cmp.adapter.AuditedHTTPClient")
async def test_cmp_onetrust_evidence_completeness(mock_client_class):
    from app.integrations.cmp.adapter import OneTrustAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = OneTrustAdapter()
    connection = ConnectionInfo(
        provider="onetrust", scopes=["onetrust:read"],
        connected_at=datetime.now(timezone.utc),
    )

    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()

    async def mock_get(endpoint, **kwargs):
        if "purposes" in endpoint:
            return {"content": [{"id": "p1", "name": "Analytics"}]}
        elif "cookie-categories" in endpoint:
            return {"content": [{"id": "c1", "name": "Functional"}]}
        elif "geolocation-rules" in endpoint:
            return {"content": [{"regions": ["IN"], "countries": ["IN"]}]}
        elif "banner-configurations" in endpoint:
            return {"content": [{"id": "b1"}]}
        elif "consent-receipts/schema" in endpoint:
            return {"fields": [{"name": "purpose"}]}
        elif "integrations" in endpoint:
            return {"content": [{"id": "i1"}]}
        return {}

    mock_instance.get.side_effect = mock_get

    result = await adapter.collect(connection)
    assert_evidence_complete(result.evidence, "CMP (OneTrust)")


@pytest.mark.asyncio
async def test_cmp_cookieyes_evidence_completeness():
    from app.integrations.cmp.adapter import CookieYesAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = CookieYesAdapter()
    connection = ConnectionInfo(
        provider="cookieyes", scopes=["cookieyes:read"],
        connected_at=datetime.now(timezone.utc),
    )

    result = await adapter.collect(connection)
    assert_evidence_complete(result.evidence, "CMP (CookieYes)")


@pytest.mark.asyncio
async def test_cmp_securiti_evidence_completeness():
    from app.integrations.cmp.adapter import SecuritiAdapter
    from app.integrations.base.adapter import ConnectionInfo

    adapter = SecuritiAdapter()
    connection = ConnectionInfo(
        provider="securiti", scopes=["securiti:read"],
        connected_at=datetime.now(timezone.utc),
    )

    result = await adapter.collect(connection)
    assert_evidence_complete(result.evidence, "CMP (Securiti)")


# ─────────────────────────────────────────────────
# 4. FindingService stamps finding_id
# ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_finding_service_stamps_finding_id():
    """Prove FindingService.generate_findings_for_assessment stamps finding_id."""
    from app.db.models.client import Client
    from app.db.models.assessment import Assessment
    from app.db.models.evidence import Evidence
    from app.db.models.finding import Finding
    from app.db.models.obligation import Obligation, ObligationSeverity
    from app.services.finding_service import FindingService

    client_id = uuid.UUID(int=1)
    assessment_id = uuid.uuid4()
    obligation_id = uuid.uuid4()

    session = AsyncMock()
    session.add = MagicMock()

    db_evidence = Evidence(
        id=uuid.uuid4(),
        client_id=client_id,
        assessment_id=assessment_id,
        obligation_id=obligation_id,
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="gtm:tags",
        observed_at=datetime.now(timezone.utc),
        detail="Tag detail",
        structured_detail={
            "source": "gtm",
            "integration": "gtm",
            "operation": "get_live_version",
            "resource_type": "tag",
            "resource_id": "t1",
            "resource_name": "Tag One",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "detail": "Tag detail",
        },
    )

    obligation = Obligation(
        id=obligation_id,
        code="S6.consentGating",
        title="Consent Gating",
        severity=ObligationSeverity.HIGH,
    )

    # Mock DB query results
    evidence_scalar = MagicMock()
    evidence_scalar.all.return_value = [db_evidence]
    obl_scalar = MagicMock()
    obl_scalar.scalar_one_or_none.return_value = obligation
    recon_scalar = MagicMock()
    recon_scalar.all.return_value = []

    session.execute.side_effect = [
        MagicMock(scalars=MagicMock(return_value=evidence_scalar)),  # evidence query
        MagicMock(),  # delete findings
        MagicMock(scalar_one_or_none=MagicMock(return_value=obligation)),  # obligation query
        MagicMock(scalars=MagicMock(return_value=recon_scalar)),  # recon query
    ]

    service = FindingService(session, client_id)
    findings = await service.generate_findings_for_assessment(assessment_id)

    assert len(findings) == 1
    finding = findings[0]

    # The crucial assertion: finding_id was stamped
    assert finding.evidence_details is not None
    assert len(finding.evidence_details) == 1
    assert "finding_id" in finding.evidence_details[0]
    assert finding.evidence_details[0]["finding_id"] == str(finding.id)
