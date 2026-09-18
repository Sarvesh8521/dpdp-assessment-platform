"""Report Generator Service.

Compiles assessment findings, reconciliations, and context into structured compliance reports:
1. Individual integration reports (e.g. GTM, GA4, Search Console, Google Ads) with the integration name as heading/title.
2. Overall combined report containing data from all connected integrations, clearly identified under each integration's title/heading, plus cross-source reconciliation.
3. Backward-compatible root summary & findings for legacy consumers.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.models.assessment import Assessment, AssessmentStatus
from app.db.models.connection import Connection, ConnectionStatus
from app.db.models.finding import Finding
from app.db.models.obligation import Obligation
from app.db.models.reconciliation_result import ReconciliationResult
from app.db.models.report import Report, ReportStatus
from app.domain.obligations import OBLIGATION_INTEGRATION_MAP
from app.services.pdf_renderer import PDFReportRenderer

logger = get_logger(__name__)

INTEGRATION_TITLES: dict[str, str] = {
    "gtm": "Google Tag Manager",
    "google_tag_manager": "Google Tag Manager",
    "ga4": "Google Analytics 4",
    "google_analytics_4": "Google Analytics 4",
    "google_ads": "Google Ads",
    "search_console": "Google Search Console",
    "google_search_console": "Google Search Console",
    "meta": "Meta Ads & Pixels",
    "github": "GitHub Repositories",
    "cmp": "Consent Management Platform (CMP)",
    "onetrust": "OneTrust CMP",
    "phase1_scanner": "Phase 1 Web Scanner",
    "scope_sheet": "Declared Scope Sheet",
}


def normalize_integration_key(key: str) -> str:
    """Normalize integration identifier to standard short code."""
    k = key.lower().strip()
    if k in ("google_tag_manager", "gtm"):
        return "gtm"
    if k in ("google_analytics_4", "ga4"):
        return "ga4"
    if k in ("google_search_console", "search_console"):
        return "search_console"
    if k in ("google_ads", "googleads"):
        return "google_ads"
    if k in ("onetrust", "cmp"):
        return "cmp"
    return k


class ReportGeneratorService:
    def __init__(self, session: AsyncSession, client_id: uuid.UUID) -> None:
        self.session = session
        self.client_id = client_id

    async def generate_report(self, assessment_id: uuid.UUID) -> Report:
        """Generate and persist the final compliance report for an assessment."""
        # 1. Fetch assessment
        assessment = await self.session.get(Assessment, assessment_id)
        if not assessment or assessment.client_id != self.client_id:
            raise ValueError("Assessment not found or inaccessible")

        # 2. Fetch findings
        findings_result = await self.session.execute(
            select(Finding).where(
                Finding.assessment_id == assessment_id,
                Finding.client_id == self.client_id
            )
        )
        findings = findings_result.scalars().all()

        # 3. Fetch reconciliations
        recon_result = await self.session.execute(
            select(ReconciliationResult)
            .join(Assessment, Assessment.id == ReconciliationResult.assessment_id)
            .where(
                ReconciliationResult.assessment_id == assessment_id,
                Assessment.client_id == self.client_id
            )
        )
        reconciliations = recon_result.scalars().all()

        # 4. Fetch obligations for readable codes and titles
        obl_result = await self.session.execute(select(Obligation))
        obl_map = {o.id: o for o in obl_result.scalars().all()}

        # 5. Fetch active connections for resource metadata
        conn_result = await self.session.execute(
            select(Connection).where(
                Connection.client_id == self.client_id,
                Connection.status == ConnectionStatus.CONNECTED,
            )
        )
        connections = conn_result.scalars().all()
        conn_map: dict[str, dict[str, Any]] = {}
        
        # Fallback from assessment config if available
        config_data = assessment.configuration or {}
        ad_accounts = config_data.get("ad_accounts", [])
        
        for c in connections:
            norm_k = normalize_integration_key(c.provider)
            
            # Fallback from assessment config for Meta or Google Ads if resource_id is None
            res_id = c.resource_id
            if not res_id and norm_k in ("meta", "google_ads") and ad_accounts:
                res_id = ad_accounts[0]
            
            # Determine resource_name with explicit fallback
            res_name = c.resource_name or res_id
            has_resource = bool(res_id)
            
            conn_map[norm_k] = {
                "provider": c.provider,
                "provider_account_id": c.provider_account_id,
                "resource_id": res_id if has_resource else "(not selected)",
                "resource_name": res_name if has_resource else "(not selected)",
                "resource_selected": has_resource,
                "connected_at": c.connected_at.isoformat() if c.connected_at else None,
                "scopes": c.scopes or [],
            }

        # 6. Compile structured finding items
        findings_data: list[dict[str, Any]] = []
        for f in findings:
            obl = obl_map.get(f.obligation_id)
            findings_data.append({
                "finding_id": str(f.id),
                "obligation_id": obl.code if obl else str(f.obligation_id),
                "obligation_uuid": str(f.obligation_id),
                "obligation_code": obl.code if obl else "UNKNOWN",
                "obligation_title": obl.title if obl else "Unknown Obligation",
                "dpdp_section": obl.dpdp_section if obl else "",
                "verdict": f.verdict.value if hasattr(f.verdict, "value") else str(f.verdict),
                "confidence": f.confidence.value if hasattr(f.confidence, "value") else str(f.confidence),
                "severity": f.severity.value if hasattr(f.severity, "value") else str(f.severity),
                "human_review_required": f.human_review_required,
                "detail": f.detail,
                "reason": f.reason,
                "compliance_implication": obl.description if obl and obl.description else "Pending legal review to determine compliance implications.",
                "recommended_action": getattr(f, "recommended_action", None),
                "sources": f.sources or [],
                "evidence_details": f.evidence_details or [],
            })

        configured_ints = [normalize_integration_key(i) for i in (assessment.selected_integrations or [])]

        expected_obls = set()
        for obl_code, mapped_ints in OBLIGATION_INTEGRATION_MAP.items():
            for m_int in mapped_ints:
                if normalize_integration_key(m_int) in configured_ints:
                    expected_obls.add(obl_code)
                    break
                    
        assessed_obls = {f["obligation_code"] for f in findings_data}
        
        for obl in obl_map.values():
            if obl.code in expected_obls and obl.code not in assessed_obls:
                missing_ints = [normalize_integration_key(i) for i in OBLIGATION_INTEGRATION_MAP.get(obl.code, []) if normalize_integration_key(i) in configured_ints]
                source_tags = [f"{m}:unassessed" for m in missing_ints] if missing_ints else ["unassessed"]
                
                findings_data.append({
                    "finding_id": f"synth-{obl.id}",
                    "obligation_id": str(obl.id),
                    "obligation_uuid": str(obl.id),
                    "obligation_code": obl.code,
                    "obligation_title": obl.title,
                    "dpdp_section": obl.dpdp_section or "",
                    "verdict": "Cannot determine",
                    "confidence": "Low",
                    "severity": obl.severity.value if hasattr(obl.severity, 'value') else str(obl.severity),
                    "human_review_required": False,
                    "detail": "Integration not connected or required data could not be accessed.",
                    "reason": "Required provider evidence unavailable. Connect the integration or verify resource access to resolve this.",
                    "compliance_implication": obl.description if obl.description else "Pending legal review to determine compliance implications.",
                    "recommended_action": "Provide the necessary client authorization and/or resource selection to allow the integration to collect required evidence.",
                    "sources": source_tags,
                    "evidence_details": [{
                        "integration": "unknown" if not missing_ints else missing_ints[0],
                        "operation": "unassessed",
                        "resource_type": "Unknown",
                        "resource_id": "Unknown",
                        "resource_name": "Unknown",
                        "observed_state": "Unknown",
                        "expected_state": "Unknown",
                        "detail": "Integration not connected or required data could not be accessed.",
                        "reason": "Required provider evidence unavailable. Connect the integration or verify resource access to resolve this."
                    }],
                })

        recon_data: list[dict[str, Any]] = []
        for r in reconciliations:
            obl = obl_map.get(r.obligation_id)
            r_outcome = r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome)
            recon_data.append({
                "rule_id": r.rule_id,
                "source_a": r.source_a,
                "source_b": r.source_b,
                "result": r_outcome,
                "outcome": r_outcome,
                "detail": r.detail,
                "obligation_code": obl.code if obl else "",
            })

        # 7. Build Individual Integration Reports
        # Determine all relevant integrations (from assessment configuration + findings)
        detected_ints = set(configured_ints)
        for f in findings_data:
            for s in f.get("sources", []):
                src_prefix = normalize_integration_key(s.split(":")[0])
                if src_prefix in INTEGRATION_TITLES:
                    detected_ints.add(src_prefix)

        all_integration_keys = sorted(list(detected_ints))
        individual_reports: dict[str, dict[str, Any]] = {}

        now_iso = datetime.now(timezone.utc).isoformat()

        for int_key in all_integration_keys:
            # Match findings that belong to this integration
            int_findings = []
            for f in findings_data:
                f_ints = {normalize_integration_key(s.split(":")[0]) for s in f.get("sources", [])}
                # Exclude findings with multiple integration sources from individual reports
                if len(f_ints) > 1:
                    continue
                if int_key in f_ints:
                    int_findings.append(f)

            int_title = INTEGRATION_TITLES.get(int_key, int_key.replace("_", " ").title())
            report_title = f"{int_title} Assessment Report"

            int_high = len([f for f in int_findings if f.get("severity", "").lower() == "high"])
            int_med = len([f for f in int_findings if f.get("severity", "").lower() == "medium"])
            int_low = len([f for f in int_findings if f.get("severity", "").lower() == "low"])
            int_obs = len([f for f in int_findings if f.get("verdict", "").lower() == "observed"])
            int_not_obs = len([f for f in int_findings if f.get("verdict", "").lower() in ("not_observed", "not observed")])
            int_cannot = len([f for f in int_findings if f.get("verdict", "").lower() in ("cannot_determine", "cannot determine")])
            int_review = len([f for f in int_findings if f.get("human_review_required")])

            individual_reports[int_key] = {
                "integration": int_key,
                "title": report_title,
                "integration_name": int_title,
                "assessment_id": str(assessment_id),
                "client_id": str(self.client_id),
                "generated_at": now_iso,
                "connection_metadata": conn_map.get(int_key, {
                    "provider": int_key,
                    "resource_name": None,
                    "connected_at": None,
                }),
                "summary": {
                    "total_findings": len(int_findings),
                    "high_severity": int_high,
                    "medium_severity": int_med,
                    "low_severity": int_low,
                    "verdicts": {
                        "observed": int_obs,
                        "not_observed": int_not_obs,
                        "cannot_determine": int_cannot,
                    },
                    "requires_human_review": int_review,
                },
                "findings": int_findings,
                "issues": [f for f in int_findings if str(f.get("verdict", "")).lower() == "observed"],
                "evaluated_obligations": [f for f in int_findings if str(f.get("verdict", "")).lower() != "observed"],
            }

        # 8. Overall Summary Statistics
        total_findings = len(findings_data)
        high_sev = len([f for f in findings_data if str(f.get("severity", "")).lower() == "high"])
        medium_sev = len([f for f in findings_data if str(f.get("severity", "")).lower() == "medium"])
        low_sev = len([f for f in findings_data if str(f.get("severity", "")).lower() == "low"])
        requires_review = len([f for f in findings_data if f.get("human_review_required")])
        observed_count = len([f for f in findings_data if str(f.get("verdict", "")).lower() == "observed"])
        not_observed_count = len([f for f in findings_data if str(f.get("verdict", "")).lower() in ("not_observed", "not observed")])
        cannot_determine_count = len([f for f in findings_data if str(f.get("verdict", "")).lower() in ("cannot_determine", "cannot determine")])

        overall_summary = {
            "total_findings": total_findings,
            "high_severity": high_sev,
            "medium_severity": medium_sev,
            "low_severity": low_sev,
            "requires_human_review": requires_review,
            "verdicts": {
                "observed": observed_count,
                "not_observed": not_observed_count,
                "cannot_determine": cannot_determine_count,
            },
            "reconciliations_count": len(reconciliations),
            "reconciliation_contradictions": len([
                r for r in reconciliations
                if (r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome)).lower() == "contradiction"
            ]),
            "integrations_assessed": [INTEGRATION_TITLES.get(k, k) for k in all_integration_keys],
        }

        # 9. Build Overall Combined Report
        combined_report = {
            "title": "Digital Personal Data Protection (DPDP) - Comprehensive Assessment Report",
            "assessment_id": str(assessment_id),
            "client_id": str(self.client_id),
            "generated_at": now_iso,
            "status": "COMPLETED",
            "summary": overall_summary,
            "selected_integrations": assessment.selected_integrations or [],
            "individual_reports": individual_reports,
            "integration_reports": [individual_reports[k] for k in all_integration_keys if k in individual_reports],
            "cross_source_reconciliation": recon_data,
        }

        # 10. Compile Full Multi-Report Payload
        report_data = {
            "assessment_id": str(assessment_id),
            "client_id": str(self.client_id),
            "generated_at": now_iso,
            "status": "COMPLETED",
            "summary": overall_summary,
            "selected_integrations": assessment.selected_integrations or [],
            "combined_report": combined_report,
            "individual_reports": individual_reports,
            "findings": findings_data,
            "reconciliations": recon_data,
        }

        # 11. Persist Artifacts to Disk
        reports_dir = Path("reports") / str(self.client_id)
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        # Primary combined report
        report_file = reports_dir / f"{assessment_id}.json"
        report_file.write_text(json.dumps(report_data, indent=2, default=str), encoding="utf-8")

        # Also write standalone JSON file for each integration report
        for int_key, int_rep in individual_reports.items():
            ind_file = reports_dir / f"{assessment_id}_{int_key}.json"
            ind_file.write_text(json.dumps(int_rep, indent=2, default=str), encoding="utf-8")

        artifact_ref = str(report_file)

        # 12. Render PDF Report
        pdf_file = reports_dir / f"{assessment_id}.pdf"
        pdf_renderer = PDFReportRenderer()
        
        # Add connections to report_data for the PDF
        report_data["connections"] = conn_map
        pdf_renderer.generate(report_data, str(pdf_file))

        # 13. Create or update report record for JSON
        existing_report_res = await self.session.execute(
            select(Report)
            .join(Assessment, Assessment.id == Report.assessment_id)
            .where(
                Report.assessment_id == assessment_id,
                Report.format == "json",
                Assessment.client_id == self.client_id
            )
        )
        report = existing_report_res.scalar_one_or_none()
        if report:
            report.status = ReportStatus.COMPLETED
            report.artifact_ref = artifact_ref
            report.version += 1
        else:
            report = Report(
                assessment_id=assessment_id,
                version=1,
                status=ReportStatus.COMPLETED,
                artifact_ref=artifact_ref,
                format="json"
            )
            self.session.add(report)

        # 14. Create or update report record for PDF
        existing_pdf_res = await self.session.execute(
            select(Report)
            .join(Assessment, Assessment.id == Report.assessment_id)
            .where(
                Report.assessment_id == assessment_id,
                Report.format == "pdf",
                Assessment.client_id == self.client_id
            )
        )
        pdf_report = existing_pdf_res.scalar_one_or_none()
        if pdf_report:
            pdf_report.status = ReportStatus.COMPLETED
            pdf_report.artifact_ref = str(pdf_file)
            pdf_report.version += 1
        else:
            pdf_report = Report(
                assessment_id=assessment_id,
                version=1,
                status=ReportStatus.COMPLETED,
                artifact_ref=str(pdf_file),
                format="pdf"
            )
            self.session.add(pdf_report)

        assessment.status = AssessmentStatus.COMPLETED
        await self.session.commit()
        logger.info("report_generated_successfully", assessment_id=str(assessment_id), path=artifact_ref)
        return report
