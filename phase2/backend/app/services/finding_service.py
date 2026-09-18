"""Finding Service.

Evaluates evidence according to domain rules to produce findings.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from app.db.models.assessment import Assessment
from app.db.models.evidence import Evidence
from app.db.models.finding import Finding, FindingSeverity, FindingStatus
from app.db.models.obligation import Obligation, ObligationSeverity
from app.db.models.reconciliation_result import ReconciliationResult, ReconciliationOutcome
from app.db.repositories.base import TenantIsolatedRepository
from app.domain.evidence import Confidence, EvidenceItem, Verdict, apply_confidence_rules


def _generate_reason(verdict: Verdict | str, structured_detail: dict | None, obligation: Obligation) -> str:
    """Generate the structured reason string combining observed/expected state and obligation context."""
    sd = structured_detail or {}
    obs = sd.get("observed_state") or "Unknown"
    exp = sd.get("expected_state") or "Unknown"
    rtype = sd.get("resource_type", "resource")
    rname = sd.get("resource_name") or sd.get("resource_id") or "Unknown"
    
    reason_text = sd.get("reason")
    if not reason_text:
        verdict_str = verdict.value if hasattr(verdict, "value") else str(verdict)
        if verdict_str == Verdict.CANNOT_DETERMINE.value:
            reason_text = "Required provider information is not exposed by the available API, so the system cannot determine whether the condition exists."
        elif verdict_str == Verdict.OBSERVED.value:
            reason_text = f"The {rtype} '{rname}' is configured with state '{obs}', which violates the obligation '{obligation.title}'."
        else:
            reason_text = f"The {rtype} '{rname}' is configured with state '{obs}', which meets the obligation '{obligation.title}'."

    reason_text = str(reason_text).strip()
    if not reason_text.startswith('"'):
        reason_text = f'"{reason_text}"'

    return f"Observed:\n{obs}\n\nExpected:\n{exp}\n\nReason:\n{reason_text}"

def _generate_recommended_action(verdict: Verdict | str, sources: list[str]) -> str | None:
    """Generate a concrete recommended action based on the verdict and integration."""
    v_val = verdict.value if hasattr(verdict, "value") else str(verdict)
    
    if v_val == Verdict.CANNOT_DETERMINE.value:
        return "Provide the necessary client authorization and/or resource selection to allow the integration to collect required evidence."
        
    if v_val == Verdict.OBSERVED.value:
        # Determine primary integration from sources
        integration = sources[0].split(":")[0].lower() if sources else ""
        if integration == "gtm":
            return "Configure the identified tag with the required consent settings."
        elif integration == "ga4":
            return "Review the identified retention configuration and document/revise it as appropriate."
        elif integration in ("google_ads", "googleads"):
            return "Review the identified audience/list against the applicable consent/purpose basis."
        elif integration == "meta":
            return "Review the identified audience-sharing or Advanced Matching configuration."
        elif integration == "github":
            return "Gate SDK initialization behind the consent decision."
        elif integration in ("search_console", "google_search_console"):
            return "Add or intentionally exclude the discovered property from scope."
        elif integration in ("cmp", "onetrust"):
            return "Correct the identified purpose/category mapping."
        else:
            return "Review the identified configuration against applicable compliance requirements."
            
    return None


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
                
            evidence_details = []
            finding_reasons = []
            missing_structured_evidence = False
            for db_ev in db_evidence:
                ev_reason = _generate_reason(db_ev.verdict, db_ev.structured_detail, obligation)
                db_ev.reason = ev_reason
                finding_reasons.append(f"[{db_ev.source.split(':')[0].title()}] {ev_reason}")
                
                if db_ev.structured_detail:
                    # Update structured_detail in-memory so it goes into finding.evidence_details
                    db_ev.structured_detail["reason"] = ev_reason
                    evidence_details.append(db_ev.structured_detail)
                else:
                    missing_structured_evidence = True
                    # Synthesize fallback evidence for missing/incomplete data
                    # Ensure no empty evidence is generated for a finding
                    evidence_details.append({
                        "integration": db_ev.source.split(':')[0],
                        "operation": db_ev.source,
                        "resource_type": "Unknown",
                        "resource_id": "Unknown",
                        "resource_name": "Unknown",
                        "observed_state": "Unknown",
                        "expected_state": "Unknown",
                        "detail": db_ev.detail or "No detail provided.",
                        "reason": ev_reason
                    })
            
            finding_reason = "\n\n---\n\n".join(finding_reasons)
                
            # Enforce evidence completeness validation
            if missing_structured_evidence and verdict in (Verdict.OBSERVED, Verdict.NOT_OBSERVED):
                verdict = Verdict.CANNOT_DETERMINE
                detail = "Missing structured evidence details. Cannot validate finding."
                finding_reason = "No valid structured evidence provided by adapter. Downgraded to Cannot determine."
                
            finding = Finding(
                client_id=self.client_id,
                assessment_id=assessment_id,
                obligation_id=obligation_id,
                verdict=verdict,
                confidence=confidence,
                severity=severity_map.get(obligation.severity, FindingSeverity.MEDIUM),
                detail=detail,
                reason=finding_reason,
                status=FindingStatus.UNDER_REVIEW if human_review else FindingStatus.OPEN,
                human_review_required=human_review,
                recommended_action=_generate_recommended_action(verdict, sources),
                evidence_ids=evidence_ids,
                sources=sources,
                evidence_details=evidence_details if evidence_details else None,
                obligation_code=obligation.code,
                obligation_version=getattr(obligation, "version", None),
            )
            self.session.add(finding)

            # Stamp finding_id into every evidence_details entry for full traceability
            if finding.evidence_details:
                for ed in finding.evidence_details:
                    if isinstance(ed, dict):
                        ed["finding_id"] = str(finding.id)

            findings.append(finding)
            
        # 4. Process Reconciliation Results
        recon_result = await self.session.execute(
            select(ReconciliationResult)
            .join(Assessment, Assessment.id == ReconciliationResult.assessment_id)
            .where(
                ReconciliationResult.assessment_id == assessment_id,
                Assessment.client_id == self.client_id
            )
        )
        recon_records = recon_result.scalars().all()

        findings_by_obl: dict[uuid.UUID, Finding] = {f.obligation_id: f for f in findings}

        for rr in recon_records:
            if rr.outcome not in (ReconciliationOutcome.CONTRADICTION, ReconciliationOutcome.INSUFFICIENT_DATA):
                continue

            detail_text = f"[{rr.source_a.title()} vs {rr.source_b.title()} Reconciliation] {rr.detail}"

            recon_metadata = rr.reconciliation_metadata or {}
            
            synthetic_recon_evidence = {
                "source": "reconciliation",
                "integration": "reconciliation_engine",
                "operation": rr.rule_id,
                "resource_type": "cross_source_check",
                "observed_state": f"Source A ({rr.source_a}): {recon_metadata.get('evidence_a_detail', '')} | Source B ({rr.source_b}): {recon_metadata.get('evidence_b_detail', '')}",
                "expected_state": "Agreement between sources",
                "detail": detail_text,
                "reason": f"Reconciliation resulted in {rr.outcome.value}" if hasattr(rr.outcome, 'value') else f"Reconciliation resulted in {rr.outcome}",
                "metadata": {
                    "source_a": rr.source_a,
                    "source_b": rr.source_b,
                    "evidence_a_detail": recon_metadata.get('evidence_a_detail', ''),
                    "evidence_b_detail": recon_metadata.get('evidence_b_detail', ''),
                }
            }
            
            # Since reconciliation is dynamic, we create a reason string directly
            recon_reason = (
                f"Observed:\n{synthetic_recon_evidence['observed_state']}\n\n"
                f"Expected:\n{synthetic_recon_evidence['expected_state']}\n\n"
                f"Reason:\n\"{synthetic_recon_evidence['reason']}\""
            )
            synthetic_recon_evidence["reason"] = recon_reason

            if rr.obligation_id in findings_by_obl:
                # Update existing finding
                f = findings_by_obl[rr.obligation_id]
                f.detail = f"{f.detail} {detail_text}"
                f.human_review_required = f.human_review_required or rr.human_review_required
                if f.human_review_required:
                    f.status = FindingStatus.UNDER_REVIEW
                
                # Add sources
                sources = set(f.sources) if f.sources else set()
                sources.add(rr.source_a)
                sources.add(rr.source_b)
                f.sources = list(sources)
                
                # Overwrite verdict
                if rr.outcome == ReconciliationOutcome.INSUFFICIENT_DATA:
                    f.verdict = Verdict.CANNOT_DETERMINE
                else:
                    f.verdict = Verdict.NOT_OBSERVED
                    
                f.confidence = Confidence(rr.confidence.value)
                f.recommended_action = _generate_recommended_action(f.verdict, f.sources)
                
                if f.reason:
                    f.reason = f"{f.reason}\n\n---\n\n[Reconciliation] {recon_reason}"
                else:
                    f.reason = f"[Reconciliation] {recon_reason}"
                
                # Append synthetic evidence detail
                if not f.evidence_details:
                    f.evidence_details = []
                f.evidence_details.append(synthetic_recon_evidence)
            else:
                # Create a new finding if one doesn't exist
                obs_result = await self.session.execute(
                    select(Obligation).where(Obligation.id == rr.obligation_id)
                )
                obligation = obs_result.scalar_one_or_none()
                if not obligation:
                    continue

                severity_map = {
                    ObligationSeverity.HIGH: FindingSeverity.HIGH,
                    ObligationSeverity.MEDIUM: FindingSeverity.MEDIUM,
                    ObligationSeverity.LOW: FindingSeverity.LOW,
                }
                
                f_verdict = Verdict.CANNOT_DETERMINE if rr.outcome == ReconciliationOutcome.INSUFFICIENT_DATA else Verdict.NOT_OBSERVED
                f = Finding(
                    client_id=self.client_id,
                    assessment_id=assessment_id,
                    obligation_id=rr.obligation_id,
                    verdict=f_verdict,
                    confidence=Confidence(rr.confidence.value),
                    severity=severity_map.get(obligation.severity, FindingSeverity.MEDIUM),
                    detail=detail_text,
                    reason=f"[Reconciliation] {recon_reason}",
                    status=FindingStatus.UNDER_REVIEW if rr.human_review_required else FindingStatus.OPEN,
                    human_review_required=rr.human_review_required,
                    evidence_ids=[],
                    sources=[rr.source_a, rr.source_b],
                    evidence_details=[synthetic_recon_evidence],
                    recommended_action=_generate_recommended_action(f_verdict, [rr.source_a, rr.source_b]),
                    obligation_code=obligation.code,
                    obligation_version=getattr(obligation, "version", None),
                )
                self.session.add(f)
                findings.append(f)
                findings_by_obl[rr.obligation_id] = f

        await self.session.commit()
        return findings
