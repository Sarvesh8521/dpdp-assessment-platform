import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.assessment import Assessment
from app.db.models.assessment_run import AssessmentRun
from app.db.models.audit_log import AuditLog
from app.db.models.evidence import Evidence
from app.db.models.finding import Finding
from app.db.models.report import Report
from app.services.retention_service import RetentionService


@pytest.fixture
def mock_session():
    session = AsyncMock(spec=AsyncSession)
    return session


@pytest.fixture
def mock_settings():
    class MockSettings:
        default_evidence_retention_days = 30
        storage_backend = "local"
        local_storage_path = "/tmp/storage"
        
    return MockSettings()


@pytest.mark.asyncio
async def test_apply_retention_policy_no_expired(mock_session, mock_settings):
    with patch("app.services.retention_service.get_settings", return_value=mock_settings):
        service = RetentionService(mock_session)
        
        # Mock no expired assessments
        from unittest.mock import MagicMock
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute.return_value = mock_result
        
        stats = await service.apply_retention_policy()
        
        assert stats["assessments"] == 0
        assert stats["runs"] == 0
        assert stats["evidence"] == 0
        assert stats["findings"] == 0
        assert stats["reports"] == 0


@pytest.mark.asyncio
async def test_apply_retention_policy_with_expired(mock_session, mock_settings):
    with patch("app.services.retention_service.get_settings", return_value=mock_settings):
        service = RetentionService(mock_session)
        
        client_id = uuid.uuid4()
        assessment_id = uuid.uuid4()
        
        # 1. Mock expired assessment
        class MockRow:
            def __init__(self, id, client_id):
                self.id = id
                self.client_id = client_id
        
        mock_assessments_result = MagicMock()
        mock_assessments_result.all.return_value = [MockRow(id=assessment_id, client_id=client_id)]
        
        # 2. Mock reports
        mock_report = Report(
            id=uuid.uuid4(),
            assessment_id=assessment_id,
            artifact_ref="test_report.pdf"
        )
        mock_reports_result = MagicMock()
        mock_reports_result.scalars().all.return_value = [mock_report]
        
        # 3. Mock deletes
        mock_delete_result = MagicMock()
        mock_delete_result.rowcount = 1
        
        # Configure side effect for session.execute
        # It's called 6 times:
        # 1. select assessments
        # 2. select reports
        # 3. delete evidence
        # 4. delete findings
        # 5. delete reports
        # 6. delete runs
        # 7. delete assessments
        mock_session.execute.side_effect = [
            mock_assessments_result,
            mock_reports_result,
            mock_delete_result, # Evidence
            mock_delete_result, # Findings
            mock_delete_result, # Reports
            mock_delete_result, # Runs
            mock_delete_result  # Assessments
        ]
        
        with patch.object(service, '_delete_physical_artifact') as mock_delete_physical:
            stats = await service.apply_retention_policy()
            
            # Assert physical deletion was called
            mock_delete_physical.assert_called_once_with("test_report.pdf")
            
            # Assert stats are accurate
            assert stats["assessments"] == 1
            assert stats["runs"] == 1
            assert stats["evidence"] == 1
            assert stats["findings"] == 1
            assert stats["reports"] == 1
            
            # Assert AuditLog was generated
            mock_session.add.assert_called_once()
            added_log = mock_session.add.call_args[0][0]
            assert isinstance(added_log, AuditLog)
            assert added_log.operation == "purge_expired_assessment"
            assert added_log.assessment_id == assessment_id
            
            mock_session.commit.assert_called_once()


@pytest.mark.asyncio
async def test_delete_physical_artifact(mock_session, mock_settings):
    with patch("app.services.retention_service.get_settings", return_value=mock_settings):
        service = RetentionService(mock_session)
        
        with patch("pathlib.Path.exists") as mock_exists, patch("pathlib.Path.unlink") as mock_unlink:
            mock_exists.return_value = True
            
            service._delete_physical_artifact("test.pdf")
            
            mock_exists.assert_called_once()
            mock_unlink.assert_called_once()


@pytest.mark.asyncio
async def test_delete_physical_artifact_gcs(mock_session):
    class MockGCSSettings:
        default_evidence_retention_days = 30
        storage_backend = "gcs"
        gcs_bucket_name = "test-bucket"

    mock_settings = MockGCSSettings()

    with patch("app.services.retention_service.get_settings", return_value=mock_settings):
        service = RetentionService(mock_session)
        
        with patch("app.services.retention_service.storage.Client") as mock_storage_client:
            mock_client_instance = mock_storage_client.return_value
            mock_bucket = mock_client_instance.bucket.return_value
            mock_blob = mock_bucket.blob.return_value
            mock_blob.exists.return_value = True

            service._delete_physical_artifact("reports/test.pdf")

            mock_storage_client.assert_called_once()
            mock_client_instance.bucket.assert_called_once_with("test-bucket")
            mock_bucket.blob.assert_called_once_with("reports/test.pdf")
            mock_blob.delete.assert_called_once()

def test_run_daily_retention_purge():
    # Test the Celery task wrapper
    from app.workers.tasks import run_daily_retention_purge
    
    with patch("app.services.retention_service.RetentionService") as MockService:
        # Mock the service and its async method
        mock_instance = MockService.return_value
        mock_instance.apply_retention_policy = AsyncMock(return_value={"assessments": 1})
        
        # Call the synchronous task which uses asyncio.run internally
        stats = run_daily_retention_purge()
        assert stats == {"assessments": 1}
        mock_instance.apply_retention_policy.assert_called_once()
