import uuid
import pytest
from datetime import datetime, timezone
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.client import Client
from app.db.models.client_user import ClientUser
from app.db.models.user import User
from app.db.models.connection import Connection, ConnectionStatus
from app.auth.dependencies import create_access_token
from app.db.session import async_session_factory
from app.main import app
from app.schemas.api import AssessmentConfig
from app.services.assessment_service import AssessmentService

@pytest.mark.asyncio
async def test_meta_connection_identity(monkeypatch):
    """Regression test: verify select-resource exactly targets connection_id and preserves other connections."""
    async with async_session_factory() as db_session, httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # Setup Client and User
        uid = str(uuid.uuid4())
        user = User(identity=f"tester_{uid}@example.com", google_sub=f"tester_{uid}")
        db_session.add(user)
        await db_session.flush()

        client_model = Client(name="Meta Identity Client")
        db_session.add(client_model)
        await db_session.flush()

        client_user = ClientUser(client_id=client_model.id, user_id=user.id, role="client_admin")
        db_session.add(client_user)
        await db_session.flush()

        auth_token = create_access_token(
            user_id=str(user.id),
            identity=user.identity,
            client_roles={str(client_model.id): "client_admin"}
        )
        headers = {"Authorization": f"Bearer {auth_token}"}
        c_id_str = str(client_model.id)

        # Create TWO active connected connections for the same client+provider
        conn_a = Connection(
            client_id=client_model.id, provider="meta", status=ConnectionStatus.CONNECTED,
            secret_ref="secret_A", scopes=[], provider_account_id="user_meta_id_A",
            connected_at=datetime(2023, 1, 1, tzinfo=timezone.utc)
        )
        conn_b = Connection(
            client_id=client_model.id, provider="meta", status=ConnectionStatus.CONNECTED,
            secret_ref="secret_B", scopes=[], provider_account_id="user_meta_id_B",
            connected_at=datetime(2023, 1, 2, tzinfo=timezone.utc)
        )
        db_session.add_all([conn_a, conn_b])
        await db_session.commit()
        await db_session.refresh(conn_a)
        await db_session.refresh(conn_b)

        conn_a_id = conn_a.id
        conn_b_id = conn_b.id

        # Target connection A specifically with select-resource
        business_x_id = "biz_X_123"
        business_x_name = "Business X"
        payload = {"provider_metadata": {"business_id": business_x_id}}

        resp = await client.post(
            f"/api/v1/integrations/connect/meta/select-resource?client_id={c_id_str}&resource_id={business_x_id}&resource_name=Business%20X&connection_id={conn_a_id}",
            json=payload,
            headers=headers
        )
        assert resp.status_code == 200, resp.text

        # Verify Database
        await db_session.refresh(conn_a)
        await db_session.refresh(conn_b)

        # Connection A should be updated
        assert conn_a.resource_id == business_x_id
        assert conn_a.resource_name == business_x_name
        assert conn_a.provider_metadata.get("business_id") == business_x_id
        assert conn_a.status == ConnectionStatus.CONNECTED

        # Connection B should be disconnected
        assert conn_b.resource_id is None
        assert not conn_b.provider_metadata
        assert conn_b.status == ConnectionStatus.DISCONNECTED

        # Call Status endpoint
        status_resp = await client.get(f"/api/v1/integrations/status?client_id={c_id_str}", headers=headers)
        assert status_resp.status_code == 200
        data = status_resp.json()
        
        meta_status = next((i for i in data.get("integrations", []) if i.get("provider") == "meta"), {})
        
        assert meta_status.get("connection_id") == str(conn_a_id)
        assert meta_status.get("resource_selected") is True
        assert meta_status.get("resource_id") == business_x_id
        assert meta_status.get("resource_name") == business_x_name
        
        class MockMetaClient:
            def __init__(self, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, exc_type, exc_val, exc_tb): pass
            async def close(self): pass
            async def get(self, url: str, **kwargs): return {}

        monkeypatch.setattr("app.api.v1.resource_fetcher.AuditedHTTPClient", MockMetaClient)
        monkeypatch.setattr("app.integrations.meta.adapter.AuditedHTTPClient", MockMetaClient)
        
        class MockSecretManager:
            async def get_secret(self, cid, ref):
                return '{"access_token": "mock"}'
        monkeypatch.setattr("app.services.assessment_service.get_secret_manager", lambda: MockSecretManager())

        # Start assessment
        config = AssessmentConfig(integrations=["meta"], ad_accounts=[])
        assessment_service = AssessmentService(db_session, client_model.id)
        
        assessment = await assessment_service.create_assessment(
            data=type("AssessmentCreate", (), {"config": config})(),
            created_by=user.id
        )

        from app.services.report_generator import ReportGeneratorService
        import json
        generator = ReportGeneratorService(db_session, client_model.id)
        report = await generator.generate_report(assessment.id)
        
        with open(report.artifact_ref, "r", encoding="utf-8") as f:
            report_data = json.loads(f.read())
            
        meta_report = report_data.get("individual_reports", {}).get("meta", {})
        meta_node = meta_report.get("connection_metadata", {})
        assert meta_node.get("resource_id") == business_x_id
        assert meta_node.get("resource_selected") is True
