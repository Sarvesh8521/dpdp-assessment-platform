import uuid
import pytest
from datetime import datetime, timezone
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.client import Client
from app.db.models.client_user import ClientUser
from app.db.models.user import User
from app.db.models.connection import Connection, ConnectionStatus
from app.api.v1.oauth_handlers import OAuthState
from app.db.models.assessment import Assessment
from app.services.assessment_service import AssessmentService
from app.schemas.api import AssessmentConfig

from app.auth.dependencies import create_access_token

from app.db.session import async_session_factory

from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_meta_end_to_end_flow(
    monkeypatch: Any
):
    """End-to-End Test for the Meta Business Selection and Assessment Flow."""
    async with async_session_factory() as db_session, AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as async_client:
        # 1. Setup User and Client
        uid = str(uuid.uuid4())
        user = User(
            identity=f"meta_tester_{uid}@example.com",
            google_sub=f"meta_tester_{uid}"
        )
        db_session.add(user)
        await db_session.flush()

        client_model = Client(name="Meta E2E Client")
        db_session.add(client_model)
        await db_session.flush()

        client_user = ClientUser(client_id=client_model.id, user_id=user.id, role="client_admin")
        db_session.add(client_user)
        await db_session.flush()

        auth_token = create_access_token(user_id=str(user.id), identity=f"meta_tester_{uid}@example.com", client_roles={str(client_model.id): "client_admin"})

        headers = {"Authorization": f"Bearer {auth_token}"}
        client_id = str(client_model.id)
        
        business_id = "123456789_business"
        ad_account_id = "987654321_ad_account"
        pixel_id = "555555555_pixel"

        class MockMetaClient:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
                
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
                
            async def close(self):
                pass
                
            async def get(self, url: str, **kwargs):
                print(f"Mocking GET: {url}")
                if "me/businesses" in url:
                    return {"data": [{"id": business_id, "name": "E2E Meta Business"}]}
                elif "owned_ad_accounts" in url:
                    assert business_id in url
                    return {"data": [{"id": f"act_{ad_account_id}", "account_id": ad_account_id}]}
                elif "owned_pixels" in url:
                    assert business_id in url
                    return {"data": [{"id": pixel_id}]}
                elif "customaudiences" in url:
                    assert ad_account_id in url
                    return {"data": [{
                        "id": "111_aud", "name": "Customer List 1", "subtype": "CUSTOM",
                        "approximate_count": 1000, "customer_file_source": "USER_PROVIDED_ONLY"
                    }]}
                elif pixel_id in url:
                    # Pixel config query
                    return {
                        "id": pixel_id, "name": "E2E Pixel",
                        "automatic_matching_fields": ["email", "phone"],
                        "server_events_enabled": True
                    }
                elif "agencies" in url:
                    return {"data": [{"id": "agency1", "name": "E2E Agency"}]}
                elif "client_businesses" in url:
                    return {"data": []}
                elif "offline_conversion_data_sets" in url:
                    return {"data": [{"id": "offline1", "name": "E2E Offline Events"}]}
                return {}

        monkeypatch.setattr("app.api.v1.resource_fetcher.AuditedHTTPClient", MockMetaClient)
        monkeypatch.setattr("app.integrations.meta.adapter.AuditedHTTPClient", MockMetaClient)

        # 1. MOCK OAUTH CALLBACK TO CREATE CONNECTION
        conn = Connection(
            client_id=client_model.id,
            provider="meta",
            status=ConnectionStatus.CONNECTED,
            secret_ref="mock_secret",
            scopes=["ads_read", "business_management"],
            provider_account_id="user_meta_id"
        )
        db_session.add(conn)
        await db_session.commit()
        await db_session.refresh(conn)
        
        class MockSecretManager:
            async def get_secret(self, cid, ref):
                return '{"access_token": "mock_meta_token"}'
        monkeypatch.setattr("app.services.assessment_service.get_secret_manager", lambda: MockSecretManager())
        monkeypatch.setattr("app.api.v1.integrations.get_secret_manager", lambda: MockSecretManager())

        # 2. DISCOVER BUSINESSES
        discover_resp = await async_client.get(
            f"/api/v1/integrations/meta/resources?client_id={client_id}",
            headers=headers
        )
        assert discover_resp.status_code == 200, discover_resp.text
        resources_data = discover_resp.json()
        resources = resources_data["resources"]
        assert len(resources) == 1
        assert resources[0]["id"] == business_id
        assert resources[0]["name"] == "E2E Meta Business"

        # 3. SELECT BUSINESS
        select_resp = await async_client.post(
            f"/api/v1/integrations/connect/meta/select-resource?client_id={client_id}&resource_id={business_id}&resource_name=E2E%20Meta%20Business&connection_id={conn.id}",
            headers=headers
        )
        assert select_resp.status_code == 200
        
        # 4. PERSIST BUSINESS
        await db_session.refresh(conn)
        assert conn.resource_id == business_id
        assert conn.resource_name == "E2E Meta Business"

        # 5. START ASSESSMENT
        config = AssessmentConfig(integrations=["meta"], ad_accounts=[])
        assessment_service = AssessmentService(db_session, client_model.id)
        
        # 6. RUN ASSESSMENT
        assessment = await assessment_service.create_assessment(
            data=type("AssessmentCreate", (), {"config": config})(),
            created_by=user.id
        )

        # 7. GENERATE JSON REPORT
        from app.services.report_generator import ReportGeneratorService
        import json
        generator = ReportGeneratorService(db_session, client_model.id)
        report = await generator.generate_report(assessment.id)
        
        # VALIDATIONS
        assert report is not None
        assert report.status == "completed"
        assert report.artifact_ref is not None

        # Read the generated JSON report
        with open(report.artifact_ref, "r", encoding="utf-8") as f:
            report_data = json.loads(f.read())

        # Get Meta individual report
        meta_report = report_data.get("individual_reports", {}).get("meta", {})
        assert meta_report, "Meta individual report not found"
        
        # Look for Meta connection metadata
        meta_node = meta_report.get("connection_metadata", {})
        assert meta_node.get("provider") == "meta", "Meta connection node not found in report"
        
        assert meta_node.get("resource_selected") is True
        assert meta_node.get("resource_id") == business_id
        
        # Validate findings
        findings = meta_report.get("findings", [])
        
        # We expect some real findings (from adapter), not just 'Cannot determine'
        assert len(findings) > 0, "No Meta findings generated"
        
        # Check that we are NOT getting the generic "not selected" finding
        synthetic = [f for f in findings if "no Business resource is selected" in str(f.get("detail", ""))]
        assert len(synthetic) == 0, "Found synthetic finding indicating resource not selected!"
        
        flat_sources = [s for f in findings for s in f.get("sources", [])]
        print("FLAT SOURCES:", flat_sources)
        
        assert not any("unassessed" in s for s in flat_sources)
        assert any("custom_audience" in s for s in flat_sources)
        assert any("advanced_matching" in s for s in flat_sources)
        assert any("conversions_api" in s for s in flat_sources)
        assert any("business_sharing" in s for s in flat_sources)
        assert any("offline_events" in s for s in flat_sources)
