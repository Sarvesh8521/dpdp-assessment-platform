import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

from app.workers.tasks import _run_integration_async
from app.db.models.assessment_run import RunStatus
from app.integrations.base.adapter import AdapterResult, ConnectionInfo
from app.domain.evidence import EvidenceItem, Verdict, Confidence


@pytest.mark.asyncio
async def test_run_integration_async(mocker):
    client_id = str(uuid.uuid4())
    assessment_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    config = {"some_key": "some_value"}

    # Mock DB session factory
    mock_session = AsyncMock()
    
    # Mock return values for session.get
    from app.db.models.assessment_run import AssessmentRun
    mock_run = AssessmentRun(
        id=uuid.UUID(run_id),
        assessment_id=uuid.UUID(assessment_id),
        integration="dummy_integration",
        status=RunStatus.PENDING
    )
    
    async def mock_get(model, pk):
        if model == AssessmentRun:
            return mock_run
        return None
    mock_session.get.side_effect = mock_get
    
    # Mock return values for session.execute
    mock_result_conn = MagicMock()
    from app.db.models.connection import Connection, ConnectionStatus
    mock_conn = Connection(
        id=uuid.uuid4(),
        client_id=uuid.UUID(client_id),
        provider="dummy_integration",
        status=ConnectionStatus.CONNECTED,
        secret_ref="fake_secret_ref",
        scopes=["read"]
    )
    mock_result_conn.scalar_one_or_none.return_value = mock_conn
    
    mock_result_runs = MagicMock()
    mock_result_runs.scalars().all.return_value = [mock_run]
    
    async def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        if "connection" in stmt_str:
            return mock_result_conn
        if "assessment_run" in stmt_str:
            return mock_result_runs
        return MagicMock()
    mock_session.execute.side_effect = mock_execute

    mock_session_factory = mocker.patch("app.workers.tasks.async_session_factory")
    mock_session_context = AsyncMock()
    mock_session_context.__aenter__.return_value = mock_session
    mock_session_factory.return_value = mock_session_context

    # Mock Secret Manager
    mock_sm = AsyncMock()
    mock_sm.get_secret.return_value = "fake_decrypted_token"
    mock_get_secret_manager = mocker.patch("app.workers.tasks.get_secret_manager", return_value=mock_sm)

    # Mock Adapter
    mock_adapter = AsyncMock()
    mock_adapter.required_scopes = ["read"]
    evidence_item = EvidenceItem(
        obligation_id="S4.consent.collection",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="dummy_integration:test",
        observed_at=datetime.now(timezone.utc),
        detail="Test Detail"
    )
    mock_adapter.collect.return_value = AdapterResult(
        evidence=[evidence_item],
        errors=[],
        warnings=[]
    )
    mock_get_adapter = mocker.patch("app.workers.tasks.get_adapter", return_value=mock_adapter)

    # Mock Obligation mapping query
    real_obl_id = uuid.uuid4()
    mock_result_obs = MagicMock()
    from app.db.models.obligation import Obligation, ObligationCategory, ObligationSeverity
    mock_ob = Obligation(
        id=real_obl_id,
        code="S4.consent.collection",
        title="Consent Collection",
        category=ObligationCategory.CONSENT,
        severity=ObligationSeverity.MEDIUM
    )
    mock_result_obs.scalars().all.return_value = [mock_ob]

    async def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        if "connection" in stmt_str:
            return mock_result_conn
        if "assessment_run" in stmt_str:
            return mock_result_runs
        if "obligation" in stmt_str:
            return mock_result_obs
        return MagicMock()
    mock_session.execute.side_effect = mock_execute

    # Execute
    await _run_integration_async(client_id, assessment_id, run_id, "dummy_integration", config)

    # Assertions
    mock_get_secret_manager.assert_called_once()
    mock_sm.get_secret.assert_called_once_with(client_id, "fake_secret_ref")

    mock_get_adapter.assert_called_once_with("dummy_integration")

    # Verify adapter.collect called with correct args
    mock_adapter.collect.assert_called_once()
    call_kwargs = mock_adapter.collect.call_args.kwargs
    
    assert isinstance(call_kwargs["connection"], ConnectionInfo)
    assert call_kwargs["connection"].provider == "dummy_integration"
    assert call_kwargs["assessment_id"] == assessment_id
    assert call_kwargs["client_id"] == client_id
    assert call_kwargs["access_token"] == "fake_decrypted_token"
    assert call_kwargs["some_key"] == "some_value"

    # Verify run status and evidence add
    assert mock_run.status == RunStatus.COMPLETED
    assert mock_run.evidence_count == 1
    
    # Verify obligation mapping
    mock_session.add.assert_called()
    added_evidence = mock_session.add.call_args[0][0]
    assert added_evidence.obligation_id == real_obl_id
