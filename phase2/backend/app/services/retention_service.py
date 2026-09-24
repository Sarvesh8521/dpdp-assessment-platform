import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from google.cloud import storage

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.assessment import Assessment
from app.db.models.assessment_run import AssessmentRun
from app.db.models.audit_log import AuditLog
from app.db.models.evidence import Evidence
from app.db.models.finding import Finding
from app.db.models.report import Report

logger = get_logger(__name__)


class RetentionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def apply_retention_policy(self) -> dict[str, int]:
        """Apply the evidence retention policy to all assessments.

        Deletes:
        - Assessment, AssessmentRun, Evidence, Finding, Report records
        - Physical report artifacts from storage

        Retains:
        - AuditLog records (and generates new ones for the deletion)

        Returns a summary of deleted artifacts.
        """
        retention_days = self.settings.default_evidence_retention_days
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

        # 1. Identify expired assessments
        result = await self.session.execute(
            select(Assessment.id, Assessment.client_id).where(Assessment.completed_at < cutoff_date)
        )
        expired_assessments = result.all()

        if not expired_assessments:
            return {"assessments": 0, "runs": 0, "evidence": 0, "findings": 0, "reports": 0}

        assessment_ids = [row.id for row in expired_assessments]
        client_ids_map = {row.id: row.client_id for row in expired_assessments}

        stats = {"assessments": 0, "runs": 0, "evidence": 0, "findings": 0, "reports": 0}

        # 2. Delete physical artifacts (Reports)
        reports_result = await self.session.execute(
            select(Report).where(Report.assessment_id.in_(assessment_ids))
        )
        reports = reports_result.scalars().all()
        for report in reports:
            if report.artifact_ref:
                self._delete_physical_artifact(report.artifact_ref)
                stats["reports"] += 1

        # 3. Hard delete DB records in dependent order
        
        # Evidence
        result = await self.session.execute(delete(Evidence).where(Evidence.assessment_id.in_(assessment_ids)))
        stats["evidence"] = result.rowcount

        # Findings
        result = await self.session.execute(delete(Finding).where(Finding.assessment_id.in_(assessment_ids)))
        stats["findings"] = result.rowcount

        # Reports (DB records)
        await self.session.execute(delete(Report).where(Report.assessment_id.in_(assessment_ids)))
        
        # Assessment Runs
        result = await self.session.execute(delete(AssessmentRun).where(AssessmentRun.assessment_id.in_(assessment_ids)))
        stats["runs"] = result.rowcount

        # Assessments
        result = await self.session.execute(delete(Assessment).where(Assessment.id.in_(assessment_ids)))
        stats["assessments"] = result.rowcount

        # 4. Generate Audit Logs for deletion
        for assessment_id in assessment_ids:
            client_id = client_ids_map[assessment_id]
            log = AuditLog(
                client_id=client_id,
                assessment_id=assessment_id,
                actor="system_retention_job",
                integration="system",
                operation="purge_expired_assessment",
                endpoint="internal",
                status="success",
            )
            self.session.add(log)

        await self.session.commit()
        
        logger.info(
            "retention_policy_applied", 
            deleted_assessments=stats["assessments"],
            deleted_evidence=stats["evidence"]
        )

        return stats

    def _delete_physical_artifact(self, artifact_ref: str) -> None:
        """Delete a physical artifact from storage."""
        if self.settings.storage_backend == "local":
            try:
                # artifact_ref is usually an absolute path or relative to local_storage_path
                file_path = Path(artifact_ref)
                if not file_path.is_absolute():
                    file_path = Path(self.settings.local_storage_path) / file_path
                
                if file_path.exists():
                    file_path.unlink()
                    logger.debug("deleted_physical_artifact", path=str(file_path))
            except Exception as e:
                logger.error("failed_to_delete_physical_artifact", path=artifact_ref, error=str(e))
        elif self.settings.storage_backend == "gcs":
            try:
                client = storage.Client()
                bucket = client.bucket(self.settings.gcs_bucket_name)
                # Ensure we strip leading slash if any, as GCS objects shouldn't typically start with it
                blob_path = artifact_ref.lstrip("/")
                blob = bucket.blob(blob_path)
                if blob.exists():
                    blob.delete()
                    logger.debug("deleted_gcs_artifact", bucket=self.settings.gcs_bucket_name, path=blob_path)
                else:
                    logger.debug("gcs_artifact_not_found", bucket=self.settings.gcs_bucket_name, path=blob_path)
            except Exception as e:
                logger.error("failed_to_delete_gcs_artifact", path=artifact_ref, error=str(e))
