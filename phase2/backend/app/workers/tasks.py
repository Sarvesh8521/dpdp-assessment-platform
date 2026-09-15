"""Celery tasks for assessment execution."""

import asyncio
import uuid
from typing import Any

from celery.utils.log import get_task_logger

from app.workers.celery_app import celery_app
from app.db.session import async_session_factory
from app.db.models.assessment_run import AssessmentRun, RunStatus
from app.db.models.assessment import Assessment, AssessmentStatus
from app.db.models.connection import Connection, ConnectionStatus
from sqlalchemy import select
from app.security.secret_manager import get_secret_manager
from app.integrations.registry import get_adapter
from app.integrations.base.adapter import ConnectionInfo

logger = get_task_logger(__name__)

async def _run_integration_async(client_id: str, assessment_id: str, run_id: str, integration: str, config: dict[str, Any]) -> None:
    """Async execution of the integration."""
    # Note: Full logic to load connection, initialize adapter, collect evidence, 
    # run reconciliation, and update finding status goes here.
    logger.info(f"Running integration {integration} for run {run_id}")
    
    async with async_session_factory() as session:
        # 1. Update run status to RUNNING
        run = await session.get(AssessmentRun, uuid.UUID(run_id))
        if not run:
            logger.error(f"Run {run_id} not found")
            return
        
        run.status = RunStatus.RUNNING
        await session.commit()
        
        try:
            # 2. Find connection
            conn_res = await session.execute(
                select(Connection).where(
                    Connection.client_id == uuid.UUID(client_id),
                    Connection.provider == integration,
                    Connection.status == ConnectionStatus.CONNECTED
                )
            )
            connection = conn_res.scalar_one_or_none()
            
            if not connection or not connection.secret_ref:
                run.status = RunStatus.SKIPPED
                run.failure_reason = "No active connection or secret reference"
                await session.commit()
                return

            secret_manager = get_secret_manager()
            try:
                access_token = await secret_manager.get_secret(client_id, connection.secret_ref)
            except Exception as e:
                run.status = RunStatus.FAILED
                run.failure_reason = f"Failed to retrieve credentials: {e}"
                await session.commit()
                return

            adapter = get_adapter(integration)
            if not adapter:
                run.status = RunStatus.FAILED
                run.failure_reason = f"Adapter for {integration} not found"
                await session.commit()
                return

            # Construct ConnectionInfo
            connection_info = ConnectionInfo(
                provider=integration,
                scopes=connection.scopes or [],
                connected_at=connection.connected_at,
                metadata=connection.provider_metadata or {}
            )

            # Delegate to adapter.collect()
            adapter_res = await adapter.collect(
                connection=connection_info,
                assessment_id=assessment_id,
                client_id=client_id,
                access_token=access_token,
                **config
            )

            # Persist evidence
            if adapter_res.evidence:
                from app.db.models.evidence import Evidence
                from app.db.models.obligation import Obligation
                
                # Fetch obligation IDs by code
                obligation_codes = {e.obligation_id for e in adapter_res.evidence if '-' not in e.obligation_id}
                obligation_map = {}
                if obligation_codes:
                    obs = await session.execute(
                        select(Obligation).where(Obligation.code.in_(obligation_codes))
                    )
                    obligation_map = {o.code: o.id for o in obs.scalars().all()}

                for evidence in adapter_res.evidence:
                    obl_id: uuid.UUID | None = (
                        uuid.UUID(evidence.obligation_id)
                        if '-' in evidence.obligation_id
                        else obligation_map.get(evidence.obligation_id)
                    )
                    if not obl_id:
                        logger.warning(f"Skipping evidence: Unknown obligation code {evidence.obligation_id}")
                        continue

                    db_evidence = Evidence(
                        assessment_id=uuid.UUID(assessment_id),
                        client_id=uuid.UUID(client_id),
                        obligation_id=obl_id,
                        verdict=evidence.verdict.value if hasattr(evidence.verdict, 'value') else evidence.verdict,
                        confidence=evidence.confidence.value if hasattr(evidence.confidence, 'value') else evidence.confidence,
                        source=evidence.source,
                        detail=evidence.detail,
                        sanitized_metadata=evidence.raw_metadata,
                        observed_at=evidence.observed_at
                    )
                    session.add(db_evidence)
            
            run.evidence_count = len(adapter_res.evidence)
            
            if adapter_res.errors and not adapter_res.evidence:
                run.status = RunStatus.FAILED
                run.failure_reason = "; ".join(adapter_res.errors)
            else:
                run.status = RunStatus.COMPLETED
                if adapter_res.errors:
                    run.failure_reason = "Partial failure: " + "; ".join(adapter_res.errors)
                
            await session.commit()
            
        except Exception as e:
            logger.exception("Integration execution failed")
            run.status = RunStatus.FAILED
            run.failure_reason = str(e)
            await session.commit()
            
        finally:
            # Check if all runs are complete to update Assessment status
            runs_res = await session.execute(
                select(AssessmentRun).where(AssessmentRun.assessment_id == uuid.UUID(assessment_id))
            )
            runs = runs_res.scalars().all()
            all_done = all(r.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.SKIPPED) for r in runs)
            
            if all_done:
                assessment = await session.get(Assessment, uuid.UUID(assessment_id))
                if assessment:
                    assessment.status = AssessmentStatus.COMPLETED
                    await session.commit()

@celery_app.task(bind=True, max_retries=3)
def run_assessment_integration(self, client_id: str, assessment_id: str, run_id: str, integration: str, config: dict[str, Any]) -> None:
    """Execute a single integration for an assessment."""
    try:
        asyncio.run(_run_integration_async(client_id, assessment_id, run_id, integration, config))
    except Exception as exc:
        logger.exception("Task failed")
        self.retry(exc=exc, countdown=60)
