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

from sqlalchemy.orm import selectinload

from sqlalchemy.ext.asyncio import AsyncSession



from app.core.logging import get_logger

from app.db.models.assessment import Assessment, AssessmentStatus

from app.db.models.connection import Connection, ConnectionStatus

from app.db.models.finding import Finding, FindingStatus

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

}





def normalize_integration_key(key: str) -> str:
    """Normalize integration identifier to standard short code."""
    k = key.lower().strip()
    if k in ("meta", "meta_marketing", "facebook", "facebook_ads"):
        return "meta"
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


def _source_matches_integration(source: str, integration_key: str) -> bool:
    prefix = normalize_integration_key(str(source or "").split(":")[0])
    return prefix == integration_key


def _evidence_detail_matches_integration(detail: dict[str, Any], integration_key: str) -> bool:
    integration = normalize_integration_key(str(detail.get("integration") or ""))
    if integration == integration_key:
        return True
    source = str(detail.get("source") or "")
    if source and _source_matches_integration(source, integration_key):
        return True
    operation = str(detail.get("operation") or "")
    return bool(operation and _source_matches_integration(operation, integration_key))


def _verdict_from_provider_details(details: list[dict[str, Any]], fallback: str) -> str:
    verdicts = [str(d.get("verdict") or "").lower() for d in details if d.get("verdict")]
    if not verdicts:
        return fallback
    if any(v == "observed" for v in verdicts):
        return "Observed"
    if any(v in ("cannot determine", "cannot_determine") for v in verdicts):
        return "Cannot determine"
    if any(v in ("not observed", "not_observed") for v in verdicts):
        return "Not observed"
    return fallback


def _build_assessment_outcome(observed: int, not_observed: int, cannot_determine: int) -> str:
    """Build a human-readable assessment outcome summary.



    Rules:

    - Observed > 0 → issues found

    - Cannot determine > 0 (no Observed) → evidence incomplete

    - Otherwise → no issues found

    """

    if observed > 0:

        parts = [f"Issues requiring attention were identified ({observed} observed)."]

        if cannot_determine > 0:

            parts.append(

                f"Additionally, evidence was incomplete for {cannot_determine} obligation(s) "

                "that require further investigation."

            )

        return " ".join(parts)

    if cannot_determine > 0:

        return (

            f"No issues were directly observed, but evidence was incomplete for "

            f"{cannot_determine} obligation(s). These require further investigation "

            f"before the assessment can be considered complete."

        )

    return "No issues found across all assessed obligations."



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

            select(Finding)

            .options(selectinload(Finding.overrides))

            .where(

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

            ).order_by(

                Connection.resource_id.isnot(None).desc(),

                Connection.connected_at.desc().nulls_last()

            )

        )

        connections = conn_result.scalars().all()

        conn_map: dict[str, dict[str, Any]] = {}

        

        # Fallback from assessment config if available

        config_data = assessment.configuration or {}

        ad_accounts = config_data.get("ad_accounts", [])

        

        for c in connections:

            norm_k = normalize_integration_key(c.provider)

            if norm_k in conn_map:

                continue

            

            # Fallback from assessment config for Meta or Google Ads if resource_id is None

            res_id = c.resource_id

            if not res_id and norm_k in ("meta", "google_ads") and ad_accounts:

                res_id = ad_accounts[0]

            

            # Determine resource_name with explicit fallback

            res_name = c.resource_name or res_id

            has_resource = bool(res_id)

            

            conn_map[norm_k] = {

                "provider": c.provider,

                "provider_account_id": c.provider_account_id or "Connected account",

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

                "status": f.status.value if hasattr(f.status, "value") else str(f.status),

                "is_overridden": f.status == FindingStatus.OVERRIDDEN,

                "human_review_required": f.human_review_required,

                "detail": f.detail,

                "reason": f.reason,

                "compliance_implication": obl.description if obl and obl.description else "Pending legal review to determine compliance implications.",

                "recommended_action": getattr(f, "recommended_action", None),

                "sources": f.sources or [],

                "evidence_details": f.evidence_details or [],

            })



        configured_ints = [normalize_integration_key(i) for i in (assessment.selected_integrations or [])]

        recon_data: list[dict[str, Any]] = []

        for r in reconciliations:

            obl = obl_map.get(r.obligation_id)

            r_outcome = r.outcome.value if hasattr(r.outcome, "value") else str(r.outcome)

            r_confidence = r.confidence.value if hasattr(r.confidence, "value") else str(r.confidence)

            

            meta = r.reconciliation_metadata or {}

            

            recon_data.append({

                "rule_id": r.rule_id,

                "source_a": r.source_a,

                "source_b": r.source_b,

                "result": r_outcome,

                "outcome": r_outcome,

                "confidence": r_confidence,

                "human_review_required": r.human_review_required,

                "detail": r.detail,

                "obligation_code": obl.code if obl else "",

                "evidence_a_detail": meta.get("evidence_a_detail", ""),

                "evidence_b_detail": meta.get("evidence_b_detail", ""),

                "evidence_a_id": str(r.evidence_a_id) if r.evidence_a_id else None,

                "evidence_b_id": str(r.evidence_b_id) if r.evidence_b_id else None,

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
            # Match findings that belong to this integration based solely on its sources
            int_findings = [
                f for f in findings_data
                if any(normalize_integration_key(s.split(":")[0]) == int_key for s in f.get("sources", []))
            ]


            int_title = INTEGRATION_TITLES.get(int_key, int_key.replace("_", " ").title())

            report_title = f"{int_title} Assessment Report"



            int_high = len([f for f in int_findings if f.get("severity", "").lower() == "high"])

            int_med = len([f for f in int_findings if f.get("severity", "").lower() == "medium"])

            int_low = len([f for f in int_findings if f.get("severity", "").lower() == "low"])

            int_obs = len([f for f in int_findings if f.get("verdict", "").lower() == "observed"])

            int_not_obs = len([f for f in int_findings if f.get("verdict", "").lower() in ("not_observed", "not observed")])

            int_cannot = len([f for f in int_findings if f.get("verdict", "").lower() in ("cannot_determine", "cannot determine")])

            int_review = len([f for f in int_findings if f.get("human_review_required")])

            is_connected = False
            conn_meta = conn_map.get(int_key)
            if conn_meta and conn_meta.get("connected_at"):
                is_connected = True

            if not is_connected:
                outcome_msg = "Integration not connected; no provider evidence collected."
            else:
                outcome_msg = _build_assessment_outcome(int_obs, int_not_obs, int_cannot)



            int_overridden = len([f for f in int_findings if f.get("is_overridden")])

            int_actual_len = len(int_findings)

            int_override_rate = (int_overridden / int_actual_len) if int_actual_len > 0 else 0.0



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

                    "override_metrics": {

                        "total_actual_findings": int_actual_len,

                        "overridden_count": int_overridden,

                        "override_rate": round(int_override_rate, 4),

                        "health_warning": int_override_rate > 0.10,

                    },

                    "high_severity": int_high,

                    "medium_severity": int_med,

                    "low_severity": int_low,

                    "verdicts": {

                        "observed": int_obs,

                        "not_observed": int_not_obs,

                        "cannot_determine": int_cannot,

                    },

                    "requires_human_review": int_review,

                    "assessment_outcome": outcome_msg,

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



        actual_findings = findings_data

        total_actual = len(actual_findings)

        total_overridden = len([f for f in actual_findings if f.get("is_overridden")])

        overall_override_rate = (total_overridden / total_actual) if total_actual > 0 else 0.0



        # Obligation level metrics

        obligation_metrics = {}

        for f in actual_findings:

            obl_code = f.get("obligation_code")

            if not obl_code or obl_code == "UNKNOWN":

                continue

            if obl_code not in obligation_metrics:

                obligation_metrics[obl_code] = {"total": 0, "overridden": 0}

            obligation_metrics[obl_code]["total"] += 1

            if f.get("is_overridden"):

                obligation_metrics[obl_code]["overridden"] += 1

                

        for k, v in obligation_metrics.items():

            v["override_rate"] = round(v["overridden"] / v["total"], 4) if v["total"] > 0 else 0.0

            v["health_warning"] = v["override_rate"] > 0.10



        overall_summary = {

            "total_findings": total_findings,

            "override_metrics": {

                "total_actual_findings": total_actual,

                "overridden_count": total_overridden,

                "override_rate": round(overall_override_rate, 4),

                "health_warning": overall_override_rate > 0.10,

                "by_obligation": obligation_metrics

            },

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

            "assessment_outcome": _build_assessment_outcome(observed_count, not_observed_count, cannot_determine_count),

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

