from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.domain.evidence import Verdict
from app.integrations.base.adapter import ConnectionInfo
from app.integrations.base.http_client import AuditedHTTPClient, ExternalAPIError, ForbiddenEndpointError
from app.integrations.meta.adapter import MetaAdapter


async def dummy_audit(**kwargs: Any) -> None:
    pass


class FakeMetaClient:
    calls: list[dict[str, Any]] = []
    audit_records: list[dict[str, Any]] = []
    failures: dict[str, Exception] = {}

    def __init__(self, *args: Any, audit_callback: Any = None, provider: str = "meta", **kwargs: Any) -> None:
        self.audit_callback = audit_callback
        self.provider = provider

    async def get(self, endpoint: str, *, params: dict[str, Any] | None = None, operation: str = "", **kwargs: Any) -> dict[str, Any]:
        self.__class__.calls.append({"endpoint": endpoint, "params": params or {}, "operation": operation})
        if self.audit_callback:
            record = {"provider": self.provider, "operation": operation, "endpoint": endpoint}
            self.__class__.audit_records.append(record)
            await self.audit_callback(**record)

        for marker, exc in self.failures.items():
            if marker in endpoint:
                raise exc

        if "owned_ad_accounts" in endpoint:
            return {"data": [{"id": "act_123", "account_id": "123", "name": "Meta Ads"}]}
        if "owned_pixels" in endpoint:
            return {"data": [{"id": "px1", "name": "Primary Pixel"}, {"id": "px2", "name": "Clean Pixel"}]}
        if "customaudiences" in endpoint:
            return {
                "data": [
                    {
                        "id": "aud_customer",
                        "name": "CRM Customers",
                        "subtype": "CUSTOM",
                        "approximate_count": 1200,
                        "customer_file_source": "USER_PROVIDED_ONLY",
                        "permission_for_actions": {"can_edit": True, "can_share": False},
                    },
                    {
                        "id": "aud_web",
                        "name": "Website Visitors",
                        "subtype": "WEBSITE",
                        "approximate_count": 5000,
                        "data_source": {"type": "PIXEL"},
                    },
                ]
            }
        if endpoint == "/v21.0/px1":
            return {
                "id": "px1",
                "name": "Primary Pixel",
                "automatic_matching_fields": ["em", "ph"],
                "server_events_enabled": True,
                "first_party_cookie_status": "ENABLED",
            }
        if endpoint == "/v21.0/px2":
            return {
                "id": "px2",
                "name": "Clean Pixel",
                "automatic_matching_fields": [],
                "server_events_enabled": False,
                "first_party_cookie_status": "DISABLED",
            }
        if "agencies" in endpoint:
            return {"data": [{"id": "agency1", "name": "Agency Partner"}]}
        if "client_businesses" in endpoint:
            return {"data": []}
        if "offline_conversion_data_sets" in endpoint:
            return {"data": [{"id": "offline1", "name": "Offline Sales"}]}
        return {"data": []}

    async def close(self) -> None:
        pass


def _connection() -> ConnectionInfo:
    return ConnectionInfo(
        provider="meta",
        scopes=["ads_read", "business_management"],
        connected_at=datetime.now(timezone.utc),
        metadata={"business_id": "1069550845987816"},
    )


@pytest.fixture(autouse=True)
def reset_fake_client() -> None:
    FakeMetaClient.calls = []
    FakeMetaClient.audit_records = []
    FakeMetaClient.failures = {}


@pytest.mark.asyncio
async def test_meta_collects_required_metadata_and_classifies_audiences(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.meta.adapter.AuditedHTTPClient", FakeMetaClient)
    adapter = MetaAdapter()

    result = await adapter.collect(_connection(), audit_callback=dummy_audit, business_id="1069550845987816")

    assert not result.errors
    assert {item.obligation_id for item in result.evidence}.issuperset(set(adapter.obligations_answered))

    customer_ev = next(item for item in result.evidence if item.source == "meta:custom_audience:aud_customer")
    assert customer_ev.verdict == Verdict.OBSERVED
    assert customer_ev.structured_detail is not None
    assert customer_ev.structured_detail.metadata["audience"]["type"] == "customer_list"
    assert customer_ev.structured_detail.metadata["audience"]["approximate_size"] == 1200

    web_purpose_ev = next(item for item in result.evidence if item.source == "meta:custom_audience:aud_web:purpose")
    assert web_purpose_ev.structured_detail.metadata["audience"]["type"] == "website"

    assert any(item.obligation_id == "S9.sharing.serverSide" and item.verdict == Verdict.OBSERVED for item in result.evidence)
    assert any(item.obligation_id == "S9.sharing.advancedMatching" and item.verdict == Verdict.OBSERVED for item in result.evidence)
    assert any(item.obligation_id == "S9.sharing.documented" and item.verdict == Verdict.OBSERVED for item in result.evidence)
    assert any(item.source == "meta:offline_events:offline1" for item in result.evidence)

    endpoints = [call["endpoint"] for call in FakeMetaClient.calls]
    assert not any("/users" in endpoint for endpoint in endpoints)
    assert not any("/events" in endpoint for endpoint in endpoints)
    assert not any("/customaudiences/" in endpoint for endpoint in endpoints)
    assert len(FakeMetaClient.audit_records) == len(FakeMetaClient.calls)


@pytest.mark.asyncio
async def test_no_baseline_shortcut_when_no_meta_violations(monkeypatch: pytest.MonkeyPatch) -> None:
    class CleanMetaClient(FakeMetaClient):
        async def get(self, endpoint: str, *, params: dict[str, Any] | None = None, operation: str = "", **kwargs: Any) -> dict[str, Any]:
            self.__class__.calls.append({"endpoint": endpoint, "params": params or {}, "operation": operation})
            if self.audit_callback:
                self.__class__.audit_records.append({"operation": operation, "endpoint": endpoint})
                await self.audit_callback(provider=self.provider, operation=operation, endpoint=endpoint)
            if "owned_ad_accounts" in endpoint:
                return {"data": []}
            if "owned_pixels" in endpoint:
                return {"data": []}
            if "agencies" in endpoint or "client_businesses" in endpoint or "offline_conversion_data_sets" in endpoint:
                return {"data": []}
            return {"data": []}

    monkeypatch.setattr("app.integrations.meta.adapter.AuditedHTTPClient", CleanMetaClient)
    result = await MetaAdapter().collect(_connection(), audit_callback=dummy_audit, business_id="1069550845987816")

    assert not result.errors
    assert all(item.verdict == Verdict.NOT_OBSERVED for item in result.evidence)
    assert not any(item.source.endswith(":baseline") for item in result.evidence)
    assert not any(item.structured_detail and item.structured_detail.operation == "scan_baseline" for item in result.evidence)


@pytest.mark.asyncio
async def test_partial_failure_only_marks_affected_meta_obligations_cannot_determine(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeMetaClient.failures = {
        "customaudiences": ExternalAPIError("Missing Custom Audience permission", status_code=403, provider="meta")
    }
    monkeypatch.setattr("app.integrations.meta.adapter.AuditedHTTPClient", FakeMetaClient)

    result = await MetaAdapter().collect(_connection(), audit_callback=dummy_audit, business_id="1069550845987816")

    cannot = {item.obligation_id for item in result.evidence if item.verdict == Verdict.CANNOT_DETERMINE}
    assert {"S9.sharing.audienceUpload", "S7.purposeLimitation", "S8.dataMinimization"}.issubset(cannot)
    assert "S9.sharing.serverSide" not in cannot
    assert any(item.obligation_id == "S9.sharing.serverSide" and item.verdict == Verdict.OBSERVED for item in result.evidence)


@pytest.mark.asyncio
async def test_meta_forbidden_rules_allow_metadata_but_block_membership_events_and_users() -> None:
    client = AuditedHTTPClient(
        provider="meta",
        base_url="https://graph.facebook.com",
        forbidden_endpoints=MetaAdapter().forbidden_endpoints,
        audit_callback=dummy_audit,
    )

    client._check_forbidden("/v21.0/act_123/customaudiences")
    with pytest.raises(ForbiddenEndpointError):
        client._check_forbidden("/v21.0/act_123/customaudiences/aud_1/users")
    with pytest.raises(ForbiddenEndpointError):
        client._check_forbidden("/v21.0/123/events")
    with pytest.raises(ForbiddenEndpointError):
        client._check_forbidden("/v21.0/123/users")
    await client.close()


@pytest.mark.asyncio
async def test_no_pii_user_ids_or_event_payloads_reach_meta_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.integrations.meta.adapter.AuditedHTTPClient", FakeMetaClient)
    result = await MetaAdapter().collect(_connection(), audit_callback=dummy_audit, business_id="1069550845987816")

    rendered = " ".join(item.model_dump_json() for item in result.evidence).lower()
    assert "access_token" not in rendered
    assert "user_id" not in rendered
    assert "event_payload" not in rendered
    assert "members" not in rendered
    assert "users" not in rendered



