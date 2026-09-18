import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.db.models.evidence import Evidence, Verdict, Confidence
from app.db.models.finding import Finding, FindingSeverity, FindingStatus
from app.db.models.obligation import Obligation, ObligationSeverity
from app.db.models.reconciliation_result import ReconciliationResult, ReconciliationOutcome
from app.services.finding_service import FindingService


@pytest.mark.asyncio
async def test_generate_findings_with_recon_contradiction():
    client_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    obligation_id = uuid.uuid4()
    
    session = AsyncMock()
    
    # 1. Mock select(Evidence)
    mock_evidence = Evidence(
        id=uuid.uuid4(),
        client_id=client_id,
        assessment_id=assessment_id,
        obligation_id=obligation_id,
        source="gtm:tag_analysis",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        detail="Original evidence detail.",
        sanitized_metadata={},
        structured_detail={"reason": "test reason"},
        observed_at=datetime.now(timezone.utc)
    )
    
    # 2. Mock select(ReconciliationResult)
    mock_recon_result = ReconciliationResult(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        rule_id="recon_ads_match_vs_consent",
        obligation_id=obligation_id,
        source_a="google_ads",
        source_b="cmp",
        outcome=ReconciliationOutcome.CONTRADICTION,
        confidence=Confidence.HIGH,
        detail="Customer Match lists exist, but NO matching advertising/marketing purpose was found in CMP.",
        human_review_required=True,
        reconciliation_metadata={}
    )

    # 3. Mock select(Obligation)
    mock_obligation = Obligation(
        id=obligation_id,
        severity=ObligationSeverity.MEDIUM
    )
    
    # Mock execute results based on the query type
    async def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        mock_result = MagicMock()
        if "delete" in stmt_str:
            return mock_result
        if "reconciliation_result" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [mock_recon_result]
            return mock_result
        if "evidence" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [mock_evidence]
            return mock_result
        if "obligation" in stmt_str:
            mock_result.scalar_one_or_none.return_value = mock_obligation
            return mock_result
            
        return mock_result
        
    session.execute.side_effect = mock_execute

    service = FindingService(session=session, client_id=client_id)
    findings = await service.generate_findings_for_assessment(assessment_id)
    
    assert len(findings) == 1
    f = findings[0]
    
    # Assertions based on requirements
    assert f.verdict == Verdict.NOT_OBSERVED  # Contradiction overwrites verdict
    assert "Original evidence detail" in f.detail
    assert "google_ads vs cmp" in f.detail.lower()
    assert "NO matching advertising" in f.detail
    assert f.human_review_required is True
    assert f.status == FindingStatus.UNDER_REVIEW
    assert "google_ads" in f.sources
    assert "cmp" in f.sources
    assert "gtm:tag_analysis" in f.sources

@pytest.mark.asyncio
async def test_generate_findings_with_recon_agreement_no_change():
    client_id = uuid.uuid4()
    assessment_id = uuid.uuid4()
    obligation_id = uuid.uuid4()
    
    session = AsyncMock()
    
    mock_evidence = Evidence(
        id=uuid.uuid4(),
        client_id=client_id,
        assessment_id=assessment_id,
        obligation_id=obligation_id,
        source="gtm:tag_analysis",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        detail="Original evidence detail.",
        sanitized_metadata={},
        structured_detail={"reason": "test reason"},
        observed_at=datetime.now(timezone.utc)
    )
    
    mock_recon_result = ReconciliationResult(
        id=uuid.uuid4(),
        assessment_id=assessment_id,
        rule_id="recon_ads_match_vs_consent",
        obligation_id=obligation_id,
        source_a="google_ads",
        source_b="cmp",
        outcome=ReconciliationOutcome.AGREEMENT,
        confidence=Confidence.HIGH,
        detail="Customer Match lists exist, and a matching purpose was found.",
        human_review_required=False,
        reconciliation_metadata={}
    )

    mock_obligation = Obligation(
        id=obligation_id,
        severity=ObligationSeverity.MEDIUM
    )
    
    async def mock_execute(stmt):
        stmt_str = str(stmt).lower()
        mock_result = MagicMock()
        if "delete" in stmt_str:
            return mock_result
        if "reconciliation_result" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [mock_recon_result]
            return mock_result
        if "evidence" in stmt_str:
            mock_result.scalars.return_value.all.return_value = [mock_evidence]
            return mock_result
        if "obligation" in stmt_str:
            mock_result.scalar_one_or_none.return_value = mock_obligation
            return mock_result
        return mock_result
        
    session.execute.side_effect = mock_execute

    service = FindingService(session=session, client_id=client_id)
    findings = await service.generate_findings_for_assessment(assessment_id)
    
    assert len(findings) == 1
    f = findings[0]
    
    assert f.verdict == Verdict.OBSERVED  # Unchanged
    assert "Original evidence detail" in f.detail
    assert "google_ads vs cmp" not in f.detail.lower() # Not added
    assert f.human_review_required is False
    assert f.status == FindingStatus.OPEN
