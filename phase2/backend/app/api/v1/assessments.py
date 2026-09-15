from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth.dependencies import AuthContext, get_current_user
from app.db.session import get_db_session
from app.db.models.assessment import Assessment
from app.db.models.finding import Finding
from app.db.models.obligation import Obligation
from app.schemas.api import AssessmentCreate, AssessmentResponse
from app.services.assessment_service import AssessmentService

router = APIRouter(prefix="/assessments", tags=["assessments"])


@router.post("", response_model=AssessmentResponse, status_code=status.HTTP_201_CREATED)
async def create_assessment(
    data: AssessmentCreate,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Create a new assessment and dispatch it for execution."""
    auth.require_client_access(data.client_id)
    
    service = AssessmentService(session, data.client_id)
    assessment = await service.create_assessment(data, auth.user_id)
    return assessment


@router.get("")
async def list_assessments(
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """List all assessments for the authenticated user's clients."""
    client_ids = auth.get_accessible_client_ids()
    if not client_ids:
        return {"items": [], "total": 0, "page": 1, "size": 50, "has_more": False}
        
    result = await session.execute(
        select(Assessment)
        .where(Assessment.client_id.in_([uuid.UUID(c) for c in client_ids]))
        .options(selectinload(Assessment.runs))
        .order_by(Assessment.created_at.desc())
    )
    assessments = result.scalars().all()
    
    # Serialize manually to match the frontend contract
    items = []
    for a in assessments:
        items.append({
            "id": str(a.id),
            "client_id": str(a.client_id),
            "status": a.status.value if hasattr(a.status, 'value') else str(a.status),
            "started_at": a.started_at.isoformat() if a.started_at else None,
            "completed_at": a.completed_at.isoformat() if a.completed_at else None,
            "created_by": str(a.created_by),
            "configuration": a.configuration,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "name": f"Assessment - {a.created_at.strftime('%Y-%m-%d %H:%M') if a.created_at else 'N/A'}",
            "runs": [
                {
                    "id": str(r.id),
                    "integration": r.integration,
                    "status": r.status.value if hasattr(r.status, 'value') else str(r.status),
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                    "failure_reason": r.failure_reason,
                    "evidence_count": r.evidence_count,
                } for r in a.runs
            ]
        })
    
    return {"items": items, "total": len(items), "page": 1, "size": 50, "has_more": False}


@router.get("/{assessment_id}", response_model=AssessmentResponse)
async def get_assessment(
    assessment_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Get assessment details."""
    result = await session.execute(
        select(Assessment)
        .where(Assessment.id == assessment_id)
        .options(selectinload(Assessment.runs))
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
        
    auth.require_client_access(assessment.client_id)
    return assessment


@router.get("/{assessment_id}/findings")
async def get_assessment_findings(
    assessment_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Get findings for a specific assessment with resolved obligation metadata."""
    result = await session.execute(
        select(Finding)
        .where(Finding.assessment_id == assessment_id)
    )
    findings = result.scalars().all()

    obl_result = await session.execute(select(Obligation))
    obl_map = {o.id: o for o in obl_result.scalars().all()}
    
    items = []
    for f in findings:
        obl = obl_map.get(f.obligation_id)
        items.append({
            "id": str(f.id),
            "assessment_id": str(f.assessment_id),
            "obligation_id": obl.code if obl else str(f.obligation_id),
            "obligation_uuid": str(f.obligation_id),
            "obligation_code": obl.code if obl else "UNKNOWN",
            "obligation_title": obl.title if obl else "Unknown Obligation",
            "dpdp_section": obl.dpdp_section if obl else "",
            "verdict": f.verdict.value if hasattr(f.verdict, "value") else str(f.verdict),
            "confidence": f.confidence.value if hasattr(f.confidence, "value") else str(f.confidence),
            "severity": f.severity.value if hasattr(f.severity, 'value') else str(f.severity),
            "detail": f.detail,
            "status": f.status.value if hasattr(f.status, 'value') else str(f.status),
            "human_review_required": f.human_review_required,
            "sources": f.sources or [],
            "created_at": f.created_at.isoformat() if f.created_at else None,
        })
        
    return {"items": items, "total": len(items)}


@router.get("/{assessment_id}/report/json")
async def get_assessment_report_json(
    assessment_id: uuid.UUID,
    integration: str | None = Query(None, description="Optional integration ID for individual report (e.g. gtm, ga4, google_ads, search_console)"),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Export assessment report as JSON (individual integration report or overall combined report)."""
    from fastapi.responses import Response
    from pathlib import Path

    result = await session.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    auth.require_client_access(assessment.client_id)

    report_path = Path("reports") / str(assessment.client_id) / f"{assessment_id}.json"
    report_data: dict[str, Any] = {}
    if report_path.exists():
        try:
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not report_data:
        from app.services.report_generator import ReportGeneratorService
        rep_service = ReportGeneratorService(session, assessment.client_id)
        await rep_service.generate_report(assessment_id)
        if report_path.exists():
            report_data = json.loads(report_path.read_text(encoding="utf-8"))

    # If an individual integration is requested
    if integration and integration.lower() != "combined":
        norm_key = integration.lower().strip()
        individual_reports = report_data.get("individual_reports", {})
        
        # Match normalized key
        int_rep = individual_reports.get(norm_key)
        if not int_rep:
            for k, rep in individual_reports.items():
                if k.lower() == norm_key or norm_key in k.lower():
                    int_rep = rep
                    break

        if not int_rep:
            raise HTTPException(
                status_code=404,
                detail=f"Report for integration '{integration}' not found in this assessment"
            )

        content = json.dumps(int_rep, indent=2, default=str)
        filename = f"dpdp_report_{assessment_id}_{norm_key}.json"
    else:
        # Combined overall report
        combined_payload = dict(report_data.get("combined_report", report_data))
        if "individual_reports" not in combined_payload:
            combined_payload["individual_reports"] = report_data.get("individual_reports", {})
        content = json.dumps(combined_payload, indent=2, default=str)
        filename = f"dpdp_report_{assessment_id}_combined.json"

    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/{assessment_id}/report/pdf")
async def get_assessment_report_pdf(
    assessment_id: uuid.UUID,
    integration: str | None = Query(None, description="Optional integration ID for individual report"),
    autoprint: bool = Query(False),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Export assessment findings as a styled printable HTML/PDF report."""
    from fastapi.responses import HTMLResponse
    from pathlib import Path

    result = await session.execute(
        select(Assessment).where(Assessment.id == assessment_id)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    auth.require_client_access(assessment.client_id)

    # Load report payload
    report_path = Path("reports") / str(assessment.client_id) / f"{assessment_id}.json"
    report_data: dict[str, Any] = {}
    if report_path.exists():
        try:
            report_data = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if not report_data:
        from app.services.report_generator import ReportGeneratorService
        rep_service = ReportGeneratorService(session, assessment.client_id)
        await rep_service.generate_report(assessment_id)
        if report_path.exists():
            report_data = json.loads(report_path.read_text(encoding="utf-8"))

    ind_reports = report_data.get("individual_reports", {})
    norm_int = integration.lower().strip() if integration and integration.lower() != "combined" else None

    # Base HTML template styling
    base_css = """
      body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; padding: 40px; color: #1e293b; background: #fff; line-height: 1.5; }
      .header-container { border-bottom: 3px solid #3b82f6; padding-bottom: 16px; margin-bottom: 24px; }
      h1 { color: #0f172a; margin: 0 0 8px 0; font-size: 1.8rem; }
      h2 { color: #1e3a8a; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-top: 36px; margin-bottom: 16px; font-size: 1.35rem; }
      .meta { color: #64748b; font-size: 0.9rem; margin-bottom: 24px; }
      .summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 12px; margin-bottom: 28px; }
      .summary-box { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px; text-align: center; }
      .summary-num { font-size: 1.5rem; font-weight: 700; color: #0f172a; }
      .summary-label { font-size: 0.75rem; text-transform: uppercase; color: #64748b; margin-top: 4px; }
      .card { border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 16px; page-break-inside: avoid; background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.03); }
      .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
      .badge { display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; }
      .badge-high { background: #fee2e2; color: #b91c1c; }
      .badge-medium { background: #fef3c7; color: #b45309; }
      .badge-low { background: #f1f5f9; color: #475569; }
      .verdict-observed { color: #059669; font-weight: bold; }
      .verdict-not-observed { color: #dc2626; font-weight: bold; }
      .verdict-cannot-determine { color: #64748b; font-weight: bold; }
      .detail-text { color: #334155; margin: 8px 0; font-size: 0.95rem; line-height: 1.6; }
      .footer-meta { font-size: 0.85rem; color: #64748b; margin-top: 10px; border-top: 1px dashed #e2e8f0; padding-top: 8px; }
      .recon-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 0.9rem; }
      .recon-table th, .recon-table td { border: 1px solid #e2e8f0; padding: 10px; text-align: left; }
      .recon-table th { background: #f8fafc; font-weight: 600; }
      .integration-divider { margin-top: 40px; border-top: 2px dashed #cbd5e1; padding-top: 20px; page-break-before: always; }
      @media print {
        body { padding: 0; }
        .no-print { display: none !important; }
        .card { box-shadow: none; border-color: #cbd5e1; }
      }
    """

    print_btn = """
    <div class="no-print" style="margin-bottom: 24px; display: flex; justify-content: flex-end; gap: 12px;">
      <button onclick="window.print()" style="background: #2563eb; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-weight: 600; font-size: 0.9rem;">Print / Save as PDF</button>
    </div>
    """

    # RENDER INDIVIDUAL REPORT
    if norm_int:
        int_rep = ind_reports.get(norm_int)
        if not int_rep:
            for k, rep in ind_reports.items():
                if k.lower() == norm_int or norm_int in k.lower():
                    int_rep = rep
                    break

        if not int_rep:
            raise HTTPException(status_code=404, detail=f"Report for integration '{integration}' not found")

        title = int_rep.get("title", f"{integration.title()} Assessment Report")
        int_name = int_rep.get("integration_name", integration.title())
        summ = int_rep.get("summary", {})
        findings = int_rep.get("findings", [])
        conn_meta = int_rep.get("connection_metadata", {})

        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title} - {assessment_id}</title>
  <style>{base_css}</style>
</head>
<body>
  {print_btn}
  <div class="header-container">
    <h1>{title}</h1>
    <div class="meta">
      <strong>Integration:</strong> {int_name} | 
      <strong>Assessment ID:</strong> {assessment_id} | 
      <strong>Client ID:</strong> {assessment.client_id} | 
      <strong>Resource:</strong> {conn_meta.get('resource_name') or conn_meta.get('resource_id') or 'Connected Account'} | 
      <strong>Date:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
    </div>
  </div>

  <div class="summary-grid">
    <div class="summary-box">
      <div class="summary-num">{summ.get('total_findings', len(findings))}</div>
      <div class="summary-label">Total Findings</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #059669;">{summ.get('verdicts', {}).get('observed', 0)}</div>
      <div class="summary-label">Observed</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #dc2626;">{summ.get('verdicts', {}).get('not_observed', 0)}</div>
      <div class="summary-label">Not Observed</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #64748b;">{summ.get('verdicts', {}).get('cannot_determine', 0)}</div>
      <div class="summary-label">Cannot Determine</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #b91c1c;">{summ.get('high_severity', 0)}</div>
      <div class="summary-label">High Severity</div>
    </div>
  </div>

  <h2>Findings for {int_name} ({len(findings)} items)</h2>
"""
        for item in findings:
            verdict = str(item.get("verdict", ""))
            v_class = verdict.lower().replace(" ", "-").replace("_", "-")
            sev = str(item.get("severity", "medium")).lower()
            html += f"""
  <div class="card">
    <div class="card-header">
      <div>
        <strong>{item.get('obligation_code', '')}: {item.get('obligation_title', '')}</strong>
        {f"<span style='color: #64748b; font-size: 0.8rem; margin-left: 8px;'>({item.get('dpdp_section', '')})</span>" if item.get('dpdp_section') else ""}
      </div>
      <span class="badge badge-{sev}">{sev}</span>
    </div>
    <div class="detail-text">{item.get('detail', '')}</div>
    <div class="footer-meta">
      <strong>Verdict:</strong> <span class="verdict-{v_class}">{verdict}</span> | 
      <strong>Confidence:</strong> {item.get('confidence', '')} | 
      <strong>Sources:</strong> {', '.join(item.get('sources', []))}
    </div>
  </div>"""

        if not findings:
            html += "<p style='color: #64748b;'>No findings recorded for this integration.</p>"

        html += f"""
  <script>
    if ({'true' if autoprint else 'false'} || window.location.search.includes('autoprint=true')) {{
      window.onload = () => window.print();
    }}
  </script>
</body>
</html>"""
        return HTMLResponse(content=html)

    # RENDER COMBINED OVERALL REPORT
    overall_summ = report_data.get("summary", {})
    reconciliations = report_data.get("reconciliations", [])
    configured_ints = report_data.get("selected_integrations", [])

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>DPDP Comprehensive Assessment Report - {assessment_id}</title>
  <style>{base_css}</style>
</head>
<body>
  {print_btn}
  <div class="header-container">
    <h1>Digital Personal Data Protection (DPDP) - Comprehensive Assessment Report</h1>
    <div class="meta">
      <strong>Assessment ID:</strong> {assessment_id} | 
      <strong>Client ID:</strong> {assessment.client_id} | 
      <strong>Generated:</strong> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
    </div>
  </div>

  <h2>Executive Compliance Summary</h2>
  <div class="summary-grid">
    <div class="summary-box">
      <div class="summary-num">{overall_summ.get('total_findings', 0)}</div>
      <div class="summary-label">Total Findings</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #059669;">{overall_summ.get('verdicts', {}).get('observed', 0)}</div>
      <div class="summary-label">Observed</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #dc2626;">{overall_summ.get('verdicts', {}).get('not_observed', 0)}</div>
      <div class="summary-label">Not Observed</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #64748b;">{overall_summ.get('verdicts', {}).get('cannot_determine', 0)}</div>
      <div class="summary-label">Cannot Determine</div>
    </div>
    <div class="summary-box">
      <div class="summary-num" style="color: #b91c1c;">{overall_summ.get('high_severity', 0)}</div>
      <div class="summary-label">High Severity</div>
    </div>
    <div class="summary-box">
      <div class="summary-num">{len(reconciliations)}</div>
      <div class="summary-label">Reconciliations</div>
    </div>
  </div>
"""
    # Render individual reports under clear headings
    section_index = 1
    for k, int_rep in ind_reports.items():
        title = int_rep.get("title", f"{k.title()} Report")
        int_name = int_rep.get("integration_name", k.title())
        int_findings = int_rep.get("findings", [])
        conn_meta = int_rep.get("connection_metadata", {})
        res_label = conn_meta.get('resource_name') or conn_meta.get('resource_id') or 'Connected Resource'

        html += f"""
  <div class="integration-divider">
    <h2>{section_index}. {int_name} — Individual Assessment Report</h2>
    <div class="meta">
      <strong>Target Resource:</strong> {res_label} | 
      <strong>Total Findings:</strong> {len(int_findings)}
    </div>
  """
        section_index += 1

        for item in int_findings:
            verdict = str(item.get("verdict", ""))
            v_class = verdict.lower().replace(" ", "-").replace("_", "-")
            sev = str(item.get("severity", "medium")).lower()
            html += f"""
    <div class="card">
      <div class="card-header">
        <div>
          <strong>{item.get('obligation_code', '')}: {item.get('obligation_title', '')}</strong>
          {f"<span style='color: #64748b; font-size: 0.8rem; margin-left: 8px;'>({item.get('dpdp_section', '')})</span>" if item.get('dpdp_section') else ""}
        </div>
        <span class="badge badge-{sev}">{sev}</span>
      </div>
      <div class="detail-text">{item.get('detail', '')}</div>
      <div class="footer-meta">
        <strong>Verdict:</strong> <span class="verdict-{v_class}">{verdict}</span> | 
        <strong>Confidence:</strong> {item.get('confidence', '')} | 
        <strong>Sources:</strong> {', '.join(item.get('sources', []))}
      </div>
    </div>"""

        if not int_findings:
            html += "<p style='color: #64748b;'>No findings recorded for this integration.</p>"

        html += "</div>"

    # Cross-Source Reconciliation Section
    if reconciliations:
        html += f"""
  <div class="integration-divider">
    <h2>{section_index}. Cross-Source Reconciliation & Conflict Analysis</h2>
    <table class="recon-table">
      <thead>
        <tr>
          <th>Rule ID</th>
          <th>Source A</th>
          <th>Source B</th>
          <th>Outcome</th>
          <th>Observation Detail</th>
        </tr>
      </thead>
      <tbody>
"""
        for r in reconciliations:
            out_class = "verdict-not-observed" if r.get("outcome", "").lower() == "contradiction" else "verdict-observed"
            html += f"""
        <tr>
          <td><strong>{r.get('rule_id', '')}</strong></td>
          <td>{r.get('source_a', '')}</td>
          <td>{r.get('source_b', '')}</td>
          <td><span class="{out_class}">{r.get('outcome', '')}</span></td>
          <td>{r.get('detail', '')}</td>
        </tr>
"""
        html += """
      </tbody>
    </table>
  </div>
"""

    html += f"""
  <script>
    if ({'true' if autoprint else 'false'} || window.location.search.includes('autoprint=true')) {{
      window.onload = () => window.print();
    }}
  </script>
</body>
</html>"""

    return HTMLResponse(content=html)


@router.get("/{assessment_id}/report/download")
async def download_assessment_report(
    assessment_id: uuid.UUID,
    integration: str | None = Query(None),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Download the assessment report (delegates to get_assessment_report_json)."""
    return await get_assessment_report_json(assessment_id, integration, auth, session)



