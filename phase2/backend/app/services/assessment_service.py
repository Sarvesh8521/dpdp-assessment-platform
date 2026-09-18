"""Assessment Service.

Orchestrates assessment creation and execution.
Runs integrations synchronously in-process (no Celery required).

COMPLIANCE & SECURITY:
- Retrieves real tokens from SecretManager (never hardcoded mock/dummy tokens)
- Automatically refreshes expired OAuth access tokens
- Degrades gracefully to CANNOT_DETERMINE when an integration is not connected
- Never persists raw PII or secret credentials
- Tenant-isolated per client_id
"""

from __future__ import annotations

import json

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models.assessment import Assessment, AssessmentStatus
from app.db.models.assessment_run import AssessmentRun, RunStatus
from app.db.models.connection import Connection, ConnectionStatus
from app.db.models.client import Client
from app.db.models.evidence import Evidence
from app.db.models.obligation import Obligation, ObligationCategory, ObligationSeverity
from app.db.models.reconciliation_result import ReconciliationResult
from app.db.repositories.base import TenantIsolatedRepository
from app.domain.evidence import Confidence, EvidenceItem, Verdict
from app.domain.obligations import OBLIGATIONS
from app.integrations.base.adapter import ConnectionInfo
from app.api.v1.integrations import make_audit_callback
from app.integrations.registry import get_adapter
from app.schemas.api import AssessmentCreate
from app.security.secret_manager import get_secret_manager
from app.services.finding_service import FindingService
from app.services.reconciliation.engine import run_reconciliation
from app.services.report_generator import ReportGeneratorService

logger = get_logger(__name__)


class AssessmentService:
    def __init__(self, session: AsyncSession, client_id: uuid.UUID) -> None:
        self.session = session
        self.client_id = client_id
        self.repo = TenantIsolatedRepository(Assessment, session, client_id)
        self.run_repo = TenantIsolatedRepository(AssessmentRun, session, client_id)

    async def _ensure_obligations_seeded(self) -> dict[str, uuid.UUID]:
        """Ensure all DPDP obligations exist in the database and return code -> id map.

        Version-aware: if the register version has changed, existing obligations
        are updated in-place (title, description, severity, source_reference, version)
        while preserving their stable UUID.
        """
        from app.domain.obligations import REGISTER_VERSION

        obl_result = await self.session.execute(select(Obligation))
        existing = {o.code: o for o in obl_result.scalars().all()}
        obligations: dict[str, uuid.UUID] = {}

        for ob in OBLIGATIONS:
            code = ob["code"]
            if code in existing:
                db_obl = existing[code]
                obligations[code] = db_obl.id
                # Update if register version changed
                if db_obl.version != REGISTER_VERSION:
                    db_obl.title = ob["title"]
                    db_obl.description = ob.get("description", "")
                    db_obl.category = ObligationCategory(ob["category"])
                    db_obl.severity = ObligationSeverity(ob["severity"])
                    db_obl.dpdp_section = ob.get("dpdp_section", "")
                    db_obl.source_reference = ob.get("source_reference")
                    db_obl.version = REGISTER_VERSION
                    db_obl.active = True
                    logger.info("obligation_updated", code=code, version=REGISTER_VERSION)
            else:
                new_obl = Obligation(
                    id=uuid.uuid4(),
                    code=code,
                    title=ob["title"],
                    description=ob.get("description", ""),
                    category=ObligationCategory(ob["category"]),
                    severity=ObligationSeverity(ob["severity"]),
                    active=True,
                    dpdp_section=ob.get("dpdp_section", ""),
                    version=REGISTER_VERSION,
                    source_reference=ob.get("source_reference"),
                )
                self.session.add(new_obl)
                obligations[code] = new_obl.id

        await self.session.flush()
        return obligations

    async def _resolve_access_token(self, db_conn: Connection | None, provider: str) -> tuple[str, str | None]:
        """Retrieve real access token from SecretManager and refresh if needed.

        Returns (access_token, error_message).
        """
        if not db_conn or not db_conn.secret_ref:
            return "", "Integration connection not found or has no credentials"

        secret_manager = get_secret_manager()
        try:
            secret_str = await secret_manager.get_secret(str(self.client_id), db_conn.secret_ref)
        except Exception as e:
            logger.warning("secret_manager_get_failed", client_id=str(self.client_id), error=str(e))
            return "", f"Failed to retrieve stored credentials: {e}"

        try:
            token_data = json.loads(secret_str)
            access_token = token_data.get("access_token", "")
        except (json.JSONDecodeError, TypeError):
            # Plain string token (e.g. API key for CMP)
            access_token = secret_str
            token_data = {"access_token": secret_str}

        # Check if Google token needs refresh
        refresh_token = token_data.get("refresh_token") if isinstance(token_data, dict) else None
        if refresh_token and provider.lower() in [
            "gtm", "ga4", "google_ads", "search_console",
            "google_tag_manager", "google_analytics_4", "google_search_console"
        ]:
            is_expired = False
            obtained_at_str = token_data.get("obtained_at")
            expires_in = token_data.get("expires_in", 3600)
            if obtained_at_str:
                try:
                    obtained_at = datetime.fromisoformat(obtained_at_str)
                    if datetime.now(timezone.utc) >= obtained_at + timedelta(seconds=expires_in - 120):
                        is_expired = True
                except Exception:
                    pass

            if is_expired:
                try:
                    from app.api.v1.oauth_handlers import GoogleOAuthHandler
                    handler = GoogleOAuthHandler()
                    new_tokens = await handler.refresh_access_token(
                        client_id=str(self.client_id),
                        provider=provider,
                        refresh_token=refresh_token
                    )
                    if new_tokens.get("access_token"):
                        access_token = new_tokens["access_token"]
                        token_data["access_token"] = access_token
                        token_data["obtained_at"] = datetime.now(timezone.utc).isoformat()
                        if "expires_in" in new_tokens:
                            token_data["expires_in"] = new_tokens["expires_in"]
                            # Cap expires_at in DB
                            client_model = await self.session.get(Client, self.client_id)
                            new_expiry = datetime.now(timezone.utc) + timedelta(seconds=new_tokens["expires_in"])
                            if client_model and client_model.engagement_end:
                                new_expiry = min(new_expiry, client_model.engagement_end)
                            if db_conn:
                                db_conn.expires_at = new_expiry
                        logger.info("google_token_auto_refreshed", provider=provider)
                except Exception as ref_err:
                    logger.warning("google_token_refresh_failed", error=str(ref_err))
                    return "", "Authentication failed (token expired and could not be refreshed). Re-authentication required."

        return access_token, None

    async def create_assessment(self, data: AssessmentCreate, created_by: uuid.UUID) -> Assessment:
        """Create a new assessment and run all selected integrations."""
        settings = get_settings()

        assessment = Assessment(
            client_id=self.client_id,
            status=AssessmentStatus.PENDING,
            created_by=created_by,
            configuration=data.config.model_dump(),
            selected_integrations=data.config.integrations,
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(assessment)
        await self.session.flush()

        # Seed obligations if any are missing
        obligations = await self._ensure_obligations_seeded()

        evidence_by_source: dict[str, list[EvidenceItem]] = {}
        all_db_evidence: list[tuple[Evidence, str]] = []
        is_demo = assessment.configuration.get("is_demo", False)

        # 1. Collect evidence for each integration
        for integration in data.config.integrations:
            run = AssessmentRun(
                assessment_id=assessment.id,
                integration=integration,
                status=RunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            self.session.add(run)
            await self.session.flush()

            try:
                adapter = get_adapter(integration)
                if not adapter:
                    run.status = RunStatus.FAILED
                    run.failure_reason = f"Adapter '{integration}' not found in registry"
                    run.completed_at = datetime.now(timezone.utc)
                    continue

                # ── Demo Mode Execution ──
                if is_demo:
                    from app.integrations.fixtures import FIXTURES_BY_INTEGRATION
                    fixture_items = FIXTURES_BY_INTEGRATION.get(integration) or FIXTURES_BY_INTEGRATION.get(adapter.id, [])
                    evidence_by_source[adapter.id] = fixture_items
                    run.evidence_count = len(fixture_items)
                    run.status = RunStatus.COMPLETED
                    run.completed_at = datetime.now(timezone.utc)
                    for item in fixture_items:
                        ev = Evidence(
                            client_id=self.client_id,
                            assessment_id=assessment.id,
                            verdict=item.verdict,
                            confidence=item.confidence,
                            source=item.source,
                            observed_at=item.observed_at,
                            detail=item.detail,
                            sanitized_metadata=item.raw_metadata,
                            structured_detail=item.structured_detail.model_dump() if item.structured_detail else None,
                        )
                        all_db_evidence.append((ev, item.obligation_id))
                    continue

                # Find active Connection record for this provider
                possible_names = [adapter.id, adapter.provider, integration, integration.lower()]
                conn_result = await self.session.execute(
                    select(Connection).where(
                        Connection.client_id == self.client_id,
                        Connection.provider.in_(possible_names),
                        Connection.status == ConnectionStatus.CONNECTED,
                    )
                )
                db_conn = conn_result.scalars().first()

                # ---- TIME-BOXED ACCESS CHECK ----
                if db_conn:
                    client_model = await self.session.get(Client, self.client_id)
                    now = datetime.now(timezone.utc)
                    if client_model and client_model.engagement_end and now > client_model.engagement_end:
                        db_conn.status = ConnectionStatus.EXPIRED
                        await self.session.commit()
                        logger.info("engagement_expired_blocking_access", provider=adapter.provider)
                        
                        degraded_evidence = [
                            EvidenceItem(
                                obligation_id=obl_code,
                                verdict=Verdict.CANNOT_DETERMINE,
                                confidence=Confidence.LOW,
                                source=f"{adapter.id}:expired",
                                observed_at=now,
                                detail="Access expired — engagement period ended",
                                raw_metadata={"error": "engagement_expired"}
                            )
                            for obl_code in obl_codes
                        ]
                        for ev_item in degraded_evidence:
                            ev = Evidence(
                                assessment_id=self.assessment_id,
                                client_id=self.client_id,
                                provider=adapter.provider,
                                verdict=ev_item.verdict,
                                confidence=ev_item.confidence,
                                source=ev_item.source,
                                observed_at=ev_item.observed_at,
                                detail=ev_item.detail,
                                sanitized_metadata=ev_item.raw_metadata,
                                structured_detail=ev_item.structured_detail.model_dump(mode="json") if getattr(ev_item, 'structured_detail', None) else None,
                            )
                            all_db_evidence.append((ev, ev_item.obligation_id))
                        continue
                # ----------------------------------
                
                # If the integration is 'cmp', the generic get_adapter("cmp") returns OneTrust.
                # However, the user might have connected CookieYes or Securiti.
                # We dynamically update the adapter if we detect a different CMP provider in the DB.
                if integration.lower() == "cmp" and db_conn and db_conn.resource_name:
                    from app.integrations.cmp.adapter import get_cmp_adapter
                    cmp_provider = db_conn.resource_name.lower()
                    if real_adapter := get_cmp_adapter(cmp_provider):
                        adapter = real_adapter

                conn_metadata = dict(db_conn.provider_metadata or {}) if db_conn else {}
                if db_conn and db_conn.resource_id:
                    conn_metadata["resource_id"] = db_conn.resource_id
                    conn_metadata["resource_name"] = db_conn.resource_name

                conn_info = ConnectionInfo(
                    provider=adapter.provider,
                    account_id=db_conn.provider_account_id if db_conn else None,
                    scopes=[str(s) for s in (db_conn.scopes or [])] if db_conn else [],
                    connected_at=db_conn.connected_at if db_conn else None,
                    metadata=conn_metadata,
                )

                # Resolve token securely
                access_token, token_err = await self._resolve_access_token(db_conn, adapter.provider)

                # Graceful degradation if integration is connected but no resource is selected
                NO_RESOURCE_REQUIRED = {"cmp", "meta"}
                if db_conn and access_token and not db_conn.resource_id and integration.lower() not in NO_RESOURCE_REQUIRED and not is_demo:
                    logger.info("integration_no_resource_degrading", provider=adapter.provider)
                    now = datetime.now(timezone.utc)
                    degraded_evidence = [
                        EvidenceItem(
                            obligation_id=obl_code,
                            verdict=Verdict.CANNOT_DETERMINE,
                            confidence=Confidence.LOW,
                            source=f"{adapter.id}:no_resource",
                            observed_at=now,
                            detail="Integration is connected but no assessment resource has been selected.",
                        )
                        for obl_code in adapter.obligations_answered
                    ]
                    evidence_by_source[adapter.id] = degraded_evidence
                    run.evidence_count = len(degraded_evidence)
                    run.status = RunStatus.COMPLETED
                    run.failure_reason = "No assessment resource selected; degraded to Cannot Determine"
                    run.completed_at = datetime.now(timezone.utc)

                    for item in degraded_evidence:
                        ev = Evidence(
                            client_id=self.client_id,
                            assessment_id=assessment.id,
                            verdict=item.verdict,
                            confidence=item.confidence,
                            source=item.source,
                            observed_at=item.observed_at,
                            detail=item.detail,
                            sanitized_metadata=item.raw_metadata,
                            structured_detail=item.structured_detail.model_dump(mode="json") if getattr(item, 'structured_detail', None) else None,
                        )
                        all_db_evidence.append((ev, item.obligation_id))
                    continue

                # Graceful degradation if integration is not connected and not in demo mode
                if (not db_conn or not access_token) and not is_demo:
                    logger.info("integration_not_connected_degrading", provider=adapter.provider)
                    now = datetime.now(timezone.utc)
                    degraded_evidence = [
                        EvidenceItem(
                            obligation_id=obl_code,
                            verdict=Verdict.CANNOT_DETERMINE,
                            confidence=Confidence.LOW,
                            source=f"{adapter.id}:not_connected",
                            observed_at=now,
                            detail=f"Integration '{adapter.provider}' is not connected. "
                                   f"Cannot determine compliance for obligation {obl_code}.",
                        )
                        for obl_code in adapter.obligations_answered
                    ]
                    evidence_by_source[adapter.id] = degraded_evidence
                    run.evidence_count = len(degraded_evidence)
                    run.status = RunStatus.COMPLETED
                    run.failure_reason = "Integration not connected; degraded to Cannot Determine"
                    run.completed_at = datetime.now(timezone.utc)

                    for item in degraded_evidence:
                        ev = Evidence(
                            client_id=self.client_id,
                            assessment_id=assessment.id,
                            verdict=item.verdict,
                            confidence=item.confidence,
                            source=item.source,
                            observed_at=item.observed_at,
                            detail=item.detail,
                            sanitized_metadata=item.raw_metadata,
                            structured_detail=item.structured_detail.model_dump(mode="json") if getattr(item, 'structured_detail', None) else None,
                        )
                        all_db_evidence.append((ev, item.obligation_id))
                    continue

                # Build adapter-specific arguments
                kwargs: dict[str, Any] = {
                    "assessment_id": str(assessment.id),
                    "client_id": str(self.client_id),
                    "access_token": access_token,
                }

                # Google Ads customer ID from config or connection
                if integration.lower() == "google_ads":
                    ad_account = (
                        (data.config.ad_accounts and data.config.ad_accounts[0])
                        or (db_conn and db_conn.resource_id)
                    )
                    if ad_account:
                        kwargs["customer_id"] = ad_account

                # Meta ad account from config or connection
                if integration.lower() == "meta":
                    ad_account = (
                        (data.config.ad_accounts and data.config.ad_accounts[0])
                        or (db_conn and db_conn.resource_id)
                    )
                    if ad_account:
                        kwargs["ad_account_id"] = ad_account

                # GitHub repositories
                if integration.lower() == "github":
                    # resource_id stores the selected repo as owner/repo (full_name)
                    # resource_name may also have it. Fall back to config.
                    repo_from_conn = (
                        (db_conn and db_conn.resource_id)
                        or (db_conn and db_conn.resource_name)
                    )
                    repos = (
                        data.config.github_repos
                        or ([repo_from_conn] if repo_from_conn else [])
                        or data.config.repositories
                    )
                    if repos:
                        kwargs["repositories"] = repos

                    # Use installation token for GitHub App scanning
                    if db_conn and db_conn.provider_metadata:
                        inst_id = db_conn.provider_metadata.get("installation_id", "")
                        if inst_id:
                            try:
                                from app.api.v1.oauth_handlers import GitHubAppHandler
                                gh_handler = GitHubAppHandler()
                                inst_token_data = await gh_handler.get_installation_token(inst_id)
                                inst_token = inst_token_data.get("token", "")
                                if inst_token:
                                    kwargs["access_token"] = inst_token
                            except Exception as e:
                                logger.warning(
                                    "github_installation_token_for_scan_failed",
                                    error=str(e),
                                )

                # Search Console property URL
                if integration.lower() in ("search_console", "google_search_console"):
                    sc_prop = (
                        (data.config.properties and data.config.properties[0])
                        or (db_conn and db_conn.resource_id)
                    )
                    if sc_prop:
                        kwargs["property_url"] = sc_prop

                # Execute collection via adapter
                result = await adapter.collect(conn_info, audit_callback=make_audit_callback(self.session, self.client_id), **kwargs)

                evidence_by_source[adapter.id] = result.evidence
                run.evidence_count = len(result.evidence)

                for item in result.evidence:
                    ev = Evidence(
                        client_id=self.client_id,
                        assessment_id=assessment.id,
                        verdict=item.verdict,
                        confidence=item.confidence,
                        source=item.source,
                        observed_at=item.observed_at,
                        detail=item.detail,
                        sanitized_metadata=item.raw_metadata,
                        structured_detail=item.structured_detail.model_dump(mode="json") if item.structured_detail else None,
                    )
                    all_db_evidence.append((ev, item.obligation_id))

                run.status = RunStatus.COMPLETED if not result.errors else RunStatus.FAILED
                if result.errors:
                    run.failure_reason = "; ".join(result.errors)
                run.completed_at = datetime.now(timezone.utc)

            except Exception as e:
                logger.error("assessment_run_failed", integration=integration, error=str(e))
                run.status = RunStatus.FAILED
                run.failure_reason = str(e)
                run.completed_at = datetime.now(timezone.utc)
                
                # Graceful degradation on adapter failure
                now = datetime.now(timezone.utc)
                degraded_evidence = [
                    EvidenceItem(
                        obligation_id=obl_code,
                        verdict=Verdict.CANNOT_DETERMINE,
                        confidence=Confidence.LOW,
                        source=f"{adapter.id}:error",
                        observed_at=now,
                        detail=f"Failed to collect evidence from provider: {e}",
                    )
                    for obl_code in adapter.obligations_answered
                ]
                if adapter.id not in evidence_by_source:
                    evidence_by_source[adapter.id] = []
                evidence_by_source[adapter.id].extend(degraded_evidence)
                
                for item in degraded_evidence:
                    ev = Evidence(
                        client_id=self.client_id,
                        assessment_id=assessment.id,
                        verdict=item.verdict,
                        confidence=item.confidence,
                        source=item.source,
                        observed_at=item.observed_at,
                        detail=item.detail,
                        sanitized_metadata=item.raw_metadata,
                        structured_detail=item.structured_detail.model_dump(mode="json") if getattr(item, 'structured_detail', None) else None,
                    )
                    all_db_evidence.append((ev, item.obligation_id))

        # Ingest Phase 1 scanner evidence for cross-source reconciliation
        if "phase1_scanner" not in evidence_by_source and is_demo:
            from app.integrations.fixtures import FIXTURES_BY_INTEGRATION
            p1_items = FIXTURES_BY_INTEGRATION.get("phase1_scanner", [])
            evidence_by_source["phase1_scanner"] = p1_items
            for item in p1_items:
                ev = Evidence(
                    client_id=self.client_id,
                    assessment_id=assessment.id,
                    verdict=item.verdict,
                    confidence=item.confidence,
                    source=item.source,
                    observed_at=item.observed_at,
                    detail=item.detail,
                    sanitized_metadata=item.raw_metadata,
                    structured_detail=item.structured_detail.model_dump(mode="json") if item.structured_detail else None,
                )
                all_db_evidence.append((ev, item.obligation_id))

        # Ingest declared assessment scope as scope_sheet evidence for cross-source reconciliation
        now_ts = datetime.now(timezone.utc)
        scope_evidence: list[EvidenceItem] = []
        # Map legacy fields into DeclaredScope if they exist but scope fields are empty
        scope = data.config.scope
        
        # properties
        all_props = list(set(scope.search_console_properties + scope.analytics_properties + scope.domains + scope.subdomains + data.config.properties + data.config.domains + (data.config.declared_properties or [])))
        if all_props:
            scope.search_console_properties = list(set(scope.search_console_properties + all_props))
            
        # platforms
        all_platforms = list(set(scope.marketing_platforms + scope.cmp_platforms + data.config.platforms + data.config.integrations))
        if all_platforms:
            scope.marketing_platforms = list(set(scope.marketing_platforms + all_platforms))
            
        # ad accounts
        all_ad_accounts = list(set(scope.google_ads_accounts + data.config.ad_accounts))
        if all_ad_accounts:
            scope.google_ads_accounts = list(set(scope.google_ads_accounts + all_ad_accounts))
            
        # repositories
        all_repos = list(set(scope.repositories + data.config.github_repos + data.config.repositories))
        if all_repos:
            scope.repositories = list(set(scope.repositories + all_repos))

        if scope.search_console_properties:
            scope_evidence.append(EvidenceItem(
                obligation_id="S10.scope.properties",
                verdict=Verdict.OBSERVED,
                confidence=Confidence.HIGH,
                source="scope_sheet:search_console_properties",
                observed_at=now_ts,
                detail=f"Declared properties in scope: {', '.join(scope.search_console_properties)}",
                raw_metadata={"search_console_properties": scope.search_console_properties},
            ))
            
        if scope.marketing_platforms:
            scope_evidence.append(EvidenceItem(
                obligation_id="S10.scope.platforms",
                verdict=Verdict.OBSERVED,
                confidence=Confidence.HIGH,
                source="scope_sheet:marketing_platforms",
                observed_at=now_ts,
                detail=f"Declared marketing platforms in scope: {', '.join(scope.marketing_platforms)}",
                raw_metadata={"marketing_platforms": scope.marketing_platforms},
            ))
            
        if scope.google_ads_accounts:
            scope_evidence.append(EvidenceItem(
                obligation_id="S9.sharing.documented",
                verdict=Verdict.OBSERVED,
                confidence=Confidence.HIGH,
                source="scope_sheet:google_ads_accounts",
                observed_at=now_ts,
                detail=f"Declared ad accounts in scope: {', '.join(scope.google_ads_accounts)}",
                raw_metadata={"google_ads_accounts": scope.google_ads_accounts},
            ))
            
        if scope.repositories:
            scope_evidence.append(EvidenceItem(
                obligation_id="S10.scope.sdks",
                verdict=Verdict.OBSERVED,
                confidence=Confidence.HIGH,
                source="scope_sheet:repositories",
                observed_at=now_ts,
                detail=f"Declared repositories in scope: {', '.join(scope.repositories)}",
                raw_metadata={"repositories": scope.repositories},
            ))
        evidence_by_source["scope_sheet"] = scope_evidence

        # Save all evidence records with proper foreign key obligation_id
        for ev, obl_code in all_db_evidence:
            if obl_code in obligations:
                ev.obligation_id = obligations[obl_code]
                self.session.add(ev)
            else:
                logger.error("unknown_obligation_code", obligation_code=obl_code)
                raise ValueError(f"Unknown obligation code: '{obl_code}' cannot be mapped to database obligations")

        # 2. Run Cross-Source Reconciliation Engine
        from app.db.models.reconciliation_result import ReconciliationOutcome
        from app.db.models.evidence import Confidence as DBConfidence
        recon_findings = run_reconciliation(evidence_by_source)
        for rf in recon_findings:
            obl_id = obligations.get(rf.obligation_id)
            if not obl_id:
                continue
            outcome_val = rf.outcome.value if hasattr(rf.outcome, "value") else str(rf.outcome)
            conf_val = rf.confidence.value if hasattr(rf.confidence, "value") else str(rf.confidence)
            rr = ReconciliationResult(
                assessment_id=assessment.id,
                rule_id=rf.rule_id,
                obligation_id=obl_id,
                source_a=rf.source_a,
                source_b=rf.source_b,
                outcome=ReconciliationOutcome(outcome_val.lower()),
                confidence=DBConfidence(conf_val.lower()),
                detail=rf.detail,
                human_review_required=rf.human_review_required,
                reconciliation_metadata={
                    "evidence_a_detail": rf.evidence_a_detail,
                    "evidence_b_detail": rf.evidence_b_detail,
                },
            )
            self.session.add(rr)
        await self.session.flush()

        # 3. Generate Findings from Evidence
        finding_service = FindingService(self.session, self.client_id)
        await finding_service.generate_findings_for_assessment(assessment.id)

        # 4. Generate Final Compliance Report
        report_service = ReportGeneratorService(self.session, self.client_id)
        await report_service.generate_report(assessment.id)

        # Mark assessment complete
        assessment.status = AssessmentStatus.COMPLETED
        assessment.completed_at = datetime.now(timezone.utc)

        await self.session.commit()
        await self.session.refresh(assessment, ["runs"])
        return assessment

    async def get_assessment(self, assessment_id: uuid.UUID) -> Assessment | None:
        return await self.repo.get_by_id(assessment_id)
