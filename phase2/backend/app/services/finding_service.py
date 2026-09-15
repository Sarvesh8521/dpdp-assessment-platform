"""Finding Service.

Evaluates evidence according to domain rules to produce findings.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.models.evidence import Evidence
from app.db.models.finding import Finding, FindingSeverity, FindingStatus
from app.db.models.obligation import Obligation, ObligationSeverity
from app.db.repositories.base import TenantIsolatedRepository
from app.domain.evidence import Confidence, EvidenceItem, Verdict, apply_confidence_rules


class FindingService:
    def __init__(self, session: AsyncSession, client_id: uuid.UUID) -> None:
        self.session = session
        self.client_id = client_id
        self.evidence_repo = TenantIsolatedRepository(Evidence, session, client_id)
        self.finding_repo = TenantIsolatedRepository(Finding, session, client_id)

    async def generate_findings_for_assessment(self, assessment_id: uuid.UUID) -> list[Finding]:
        """Generate findings for an assessment based on all collected evidence."""
        
        # 1. Fetch all evidence for this assessment
        result = await self.session.execute(
            select(Evidence).where(
                Evidence.assessment_id == assessment_id,
                Evidence.client_id == self.client_id
            )
        )
        evidence_records = result.scalars().all()
        
        # 2. Group by obligation
        evidence_by_obligation: dict[uuid.UUID, list[EvidenceItem]] = {}
        db_evidence_by_obligation: dict[uuid.UUID, list[Evidence]] = {}
        
        for record in evidence_records:
            v_val = record.verdict.value if hasattr(record.verdict, "value") else str(record.verdict)
            c_val = record.confidence.value if hasattr(record.confidence, "value") else str(record.confidence)
            item = EvidenceItem(
                obligation_id=str(record.obligation_id),
                verdict=Verdict(v_val),
                confidence=Confidence(c_val),
                source=record.source,
                observed_at=record.observed_at,
                detail=record.detail,
                raw_metadata=record.sanitized_metadata
            )
            
            if record.obligation_id not in evidence_by_obligation:
                evidence_by_obligation[record.obligation_id] = []
                db_evidence_by_obligation[record.obligation_id] = []
                
            evidence_by_obligation[record.obligation_id].append(item)
            db_evidence_by_obligation[record.obligation_id].append(record)

        # Clear any existing findings for this assessment
        await self.session.execute(
            delete(Finding).where(
                Finding.assessment_id == assessment_id,
                Finding.client_id == self.client_id
            )
        )

        # 3. Apply confidence rules per obligation
        findings: list[Finding] = []
        
        for obligation_id, items in evidence_by_obligation.items():
            # Get the obligation to determine severity
            obs_result = await self.session.execute(
                select(Obligation).where(Obligation.id == obligation_id)
            )
            obligation = obs_result.scalar_one_or_none()
            if not obligation:
                continue
                
            # Domain logic: calculate final verdict and confidence
            verdict, confidence, human_review = apply_confidence_rules(items, str(obligation_id))
            
            # Create the finding
            db_evidence = db_evidence_by_obligation[obligation_id]
            sources = list(set([e.source for e in db_evidence]))
            evidence_ids = [str(e.id) for e in db_evidence]
            
            # Map obligation severity to finding severity
            severity_map = {
                ObligationSeverity.HIGH: FindingSeverity.HIGH,
                ObligationSeverity.MEDIUM: FindingSeverity.MEDIUM,
                ObligationSeverity.LOW: FindingSeverity.LOW,
            }
            
            # Detail synthesis: extract and present observation details from each source
            observations = []
            for db_ev in db_evidence:
                if db_ev.detail and db_ev.detail.strip():
                    src_prefix = db_ev.source.split(":")[0].replace("_", " ").title()
                    ev_text = db_ev.detail.strip()
                    if not ev_text.startswith("["):
                        ev_text = f"[{src_prefix}] {ev_text}"
                    observations.append(ev_text)

            if observations:
                detail = " ".join(observations)
            else:
                detail = f"Derived from {len(items)} evidence sources."

            if human_review:
                detail += " [Warning] Contradictory evidence detected between sources; human review required."
                
            finding = Finding(
                client_id=self.client_id,
                assessment_id=assessment_id,
                obligation_id=obligation_id,
                verdict=verdict,
                confidence=confidence,
                severity=severity_map.get(obligation.severity, FindingSeverity.MEDIUM),
                detail=detail,
                status=FindingStatus.UNDER_REVIEW if human_review else FindingStatus.OPEN,
                human_review_required=human_review,
                evidence_ids=evidence_ids,
                sources=sources,
            )
            self.session.add(finding)
            findings.append(finding)
            
        await self.session.commit()
        return findings
