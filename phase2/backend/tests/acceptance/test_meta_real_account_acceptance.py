from __future__ import annotations

import os
from typing import Any

import pytest

from app.integrations.base.adapter import ConnectionInfo
from app.integrations.meta.adapter import MetaAdapter


@pytest.mark.real_account
@pytest.mark.asyncio
async def test_meta_real_account_acceptance_business_mode() -> None:
    token = os.environ.get("META_REAL_ACCOUNT_ACCESS_TOKEN")
    if not token:
        pytest.skip("Set META_REAL_ACCOUNT_ACCESS_TOKEN to run the Meta real-account acceptance test.")

    business_id = os.environ.get("META_REAL_ACCOUNT_BUSINESS_ID", "1069550845987816")
    audit_records: list[dict[str, Any]] = []

    async def audit_callback(**kwargs: Any) -> None:
        audit_records.append(kwargs)

    adapter = MetaAdapter()
    connection = ConnectionInfo(
        provider="meta",
        scopes=adapter.required_scopes,
        metadata={"business_id": business_id},
    )

    result = await adapter.collect(
        connection,
        audit_callback=audit_callback,
        access_token=token,
        business_id=business_id,
        client_id="real-account-acceptance",
        assessment_id="real-account-acceptance",
    )

    operations = {record.get("operation") for record in audit_records}
    endpoints = [str(record.get("endpoint") or "") for record in audit_records]

    assert business_id == "1069550845987816"
    assert "get_business_ad_accounts" in operations
    assert "list_custom_audiences" in operations or any("owned_ad_accounts" in e for e in endpoints)
    assert "get_business_pixels" in operations
    assert "get_pixel_config" in operations or any("owned_pixels" in e for e in endpoints)
    assert "get_business_agencies" in operations
    assert "get_offline_event_sets" in operations

    assert not any("/users" in endpoint for endpoint in endpoints)
    assert not any("/events" in endpoint for endpoint in endpoints)
    assert not any("/customaudiences/" in endpoint for endpoint in endpoints)

    rendered = " ".join(item.model_dump_json() for item in result.evidence).lower()
    assert "access_token" not in rendered
    assert "user_id" not in rendered
    assert "event_payload" not in rendered
    assert "members" not in rendered

    audience_metadata = [
        item for item in result.evidence
        if item.structured_detail
        and item.structured_detail.operation == "list_custom_audiences"
    ]
    for item in audience_metadata:
        metadata = item.structured_detail.metadata or {}
        audience = metadata.get("audience") or {}
        assert "subtype" in audience
        assert "type" in audience
        assert audience.get("type") in {
            "customer_list",
            "website",
            "app",
            "engagement",
            "offline_conversion",
            "lookalike",
            "unknown",
        }

    assert any(item.structured_detail and item.structured_detail.operation in {"get_business_agencies", "get_client_businesses", "meta_configuration_evaluation"} for item in result.evidence)
    assert any(item.obligation_id == "S9.sharing.serverSide" for item in result.evidence)
    assert any(item.obligation_id == "S9.sharing.advancedMatching" for item in result.evidence)
    assert any(item.structured_detail and item.structured_detail.operation == "get_offline_event_sets" for item in result.evidence)


@pytest.mark.real_account
@pytest.mark.asyncio
async def test_meta_real_account_acceptance_ad_account_mode() -> None:
    token = os.environ.get("META_REAL_ACCOUNT_ACCESS_TOKEN")
    if not token:
        pytest.skip("Set META_REAL_ACCOUNT_ACCESS_TOKEN to run the Meta real-account acceptance test.")

    business_id = os.environ.get("META_REAL_ACCOUNT_BUSINESS_ID", "1069550845987816")
    ad_account_id = os.environ.get("META_REAL_ACCOUNT_AD_ACCOUNT_ID", "act_5360338760756778")
    audit_records: list[dict[str, Any]] = []

    async def audit_callback(**kwargs: Any) -> None:
        audit_records.append(kwargs)

    adapter = MetaAdapter()
    connection = ConnectionInfo(
        provider="meta",
        scopes=adapter.required_scopes,
        metadata={"business_id": business_id, "ad_account_id": ad_account_id},
    )

    result = await adapter.collect(
        connection,
        audit_callback=audit_callback,
        access_token=token,
        business_id=business_id,
        ad_account_id=ad_account_id,
        client_id="real-account-acceptance",
        assessment_id="real-account-acceptance",
    )

    operations = {record.get("operation") for record in audit_records}

    # Specifically assert that Business-wide ad accounts were NOT fetched
    assert "get_business_ad_accounts" not in operations
    
    # Assert that all other Business and Ad Account scoped queries ran normally
    assert "list_custom_audiences" in operations
    assert "get_business_pixels" in operations
    assert "get_pixel_config" in operations
    assert "get_business_agencies" in operations
    assert "get_offline_event_sets" in operations
