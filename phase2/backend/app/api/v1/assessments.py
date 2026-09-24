from datetime import datetime, timezone
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth.dependencies import AuthContext, get_current_user, apply_client_isolation
from app.db.session import get_db_session
from app.db.models.assessment import Assessment
from app.db.models.finding import Finding
from app.db.models.report import Report, ReportStatus
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
        apply_client_isolation(select(Assessment), Assessment, auth).options(selectinload(Assessment.runs))
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
        apply_client_isolation(select(Assessment).where(Assessment.id == assessment_id), Assessment, auth).options(selectinload(Assessment.runs))
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
        apply_client_isolation(select(Finding).where(Finding.assessment_id == assessment_id), Finding, auth)
        .options(selectinload(Finding.overrides))
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
            "compliance_implication": obl.description if obl and obl.description else "Pending legal review to determine compliance implications.",
            "status": f.status.value if hasattr(f.status, 'value') else str(f.status),
            "human_review_required": f.human_review_required,
            "sources": f.sources or [],
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "override_reason": f.overrides[-1].reason if f.overrides else None,
            "overridden_by": str(f.overrides[-1].user_id) if f.overrides else None,
            "original_verdict": f.overrides[-1].previous_verdict if f.overrides else None,
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
    from fastapi.responses import JSONResponse
    from pathlib import Path

    result = await session.execute(
        apply_client_isolation(select(Assessment).where(Assessment.id == assessment_id), Assessment, auth)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    auth.require_client_access(assessment.client_id)

    # 1. Fetch Report
    report_res = await session.execute(
        select(Report).where(
            Report.assessment_id == assessment_id,
            Report.status == ReportStatus.COMPLETED
        ).order_by(Report.version.desc())
    )
    report = report_res.scalars().first()

    # 2. Check if valid artifact_ref exists on disk
    if report and report.artifact_ref:
        report_path = Path(report.artifact_ref)
    else:
        report_path = None

    if not report_path or not report_path.exists():
        from app.services.report_generator import ReportGeneratorService
        rep_service = ReportGeneratorService(session, assessment.client_id)
        await rep_service.generate_report(assessment_id)
        
        # Refetch report after generation
        report_res = await session.execute(
            select(Report).where(
                Report.assessment_id == assessment_id,
                Report.status == ReportStatus.COMPLETED
            ).order_by(Report.version.desc())
        )
        report = report_res.scalars().first()
        if not report or not report.artifact_ref:
            raise HTTPException(status_code=500, detail="Failed to generate report artifact")
        
        report_path = Path(report.artifact_ref)
        if not report_path.exists():
            raise HTTPException(status_code=500, detail="Generated report artifact not found on disk")

    try:
        report_data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read report data from disk")

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

        return JSONResponse(content=int_rep)
    
    # Combined overall report
    combined_payload = dict(report_data.get("combined_report", report_data))
    if "individual_reports" not in combined_payload:
        combined_payload["individual_reports"] = report_data.get("individual_reports", {})
    
    return JSONResponse(content=combined_payload)


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
        apply_client_isolation(select(Assessment).where(Assessment.id == assessment_id), Assessment, auth)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    auth.require_client_access(assessment.client_id)

    # 1. Fetch Report
    report_res = await session.execute(
        select(Report).where(
            Report.assessment_id == assessment_id,
            Report.format == "pdf",
            Report.status == ReportStatus.COMPLETED
        ).order_by(Report.version.desc())
    )
    report = report_res.scalars().first()

    if report and report.artifact_ref:
        report_path = Path(report.artifact_ref)
    else:
        report_path = None

    if not report_path or not report_path.exists():
        from app.services.report_generator import ReportGeneratorService
        rep_service = ReportGeneratorService(session, assessment.client_id)
        await rep_service.generate_report(assessment_id)
        
        report_res = await session.execute(
            select(Report).where(
                Report.assessment_id == assessment_id,
                Report.format == "pdf",
                Report.status == ReportStatus.COMPLETED
            ).order_by(Report.version.desc())
        )
        report = report_res.scalars().first()
        if not report or not report.artifact_ref:
            raise HTTPException(status_code=500, detail="Failed to generate PDF report artifact")
        
        report_path = Path(report.artifact_ref)
        if not report_path.exists():
            raise HTTPException(status_code=500, detail="Generated PDF artifact not found on disk")

    from fastapi.responses import FileResponse
    return FileResponse(
        path=report_path,
        media_type="application/pdf",
        filename=f"Assessment_{assessment_id}.pdf"
    )




@router.get("/{assessment_id}/report/download")
async def download_assessment_report(
    assessment_id: uuid.UUID,
    integration: str | None = Query(None),
    auth: AuthContext = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    """Download the assessment report file directly from disk."""
    from fastapi.responses import FileResponse
    from pathlib import Path

    result = await session.execute(
        apply_client_isolation(select(Assessment).where(Assessment.id == assessment_id), Assessment, auth)
    )
    assessment = result.scalar_one_or_none()
    if not assessment:
        raise HTTPException(status_code=404, detail="Assessment not found")
    auth.require_client_access(assessment.client_id)

    report_res = await session.execute(
        select(Report).where(
            Report.assessment_id == assessment_id,
            Report.status == ReportStatus.COMPLETED
        ).order_by(Report.version.desc())
    )
    report = report_res.scalar_one_or_none()

    if report and report.artifact_ref:
        report_path = Path(report.artifact_ref)
    else:
        report_path = None

    if not report_path or not report_path.exists():
        from app.services.report_generator import ReportGeneratorService
        rep_service = ReportGeneratorService(session, assessment.client_id)
        await rep_service.generate_report(assessment_id)
        
        report_res = await session.execute(
            select(Report).where(
                Report.assessment_id == assessment_id,
                Report.status == ReportStatus.COMPLETED
            ).order_by(Report.version.desc())
        )
        report = report_res.scalar_one_or_none()
        if not report or not report.artifact_ref:
            raise HTTPException(status_code=500, detail="Failed to generate report artifact")
        
        report_path = Path(report.artifact_ref)
        if not report_path.exists():
            raise HTTPException(status_code=500, detail="Generated report artifact not found on disk")

    # If integration is specified, find the specific generated file
    if integration and integration.lower() != "combined":
        norm_key = integration.lower().strip()
        integration_file = report_path.parent / f"{assessment_id}_{norm_key}.json"
        
        if not integration_file.exists():
            raise HTTPException(status_code=404, detail=f"Integration report artifact not found on disk: {integration_file.name}")
            
        target_path = integration_file
        filename = f"dpdp_report_{assessment_id}_{norm_key}.json"
    else:
        target_path = report_path
        filename = f"dpdp_report_{assessment_id}_combined.json"

    return FileResponse(
        path=target_path,
        media_type="application/json",
        filename=filename
    )



