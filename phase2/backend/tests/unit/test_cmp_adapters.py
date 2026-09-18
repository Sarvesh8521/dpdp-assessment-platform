import pytest
from datetime import datetime, timezone
import uuid
from app.integrations.cmp.adapter import CookieYesAdapter, SecuritiAdapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict


@pytest.fixture
def dummy_connection():
    return ConnectionInfo(
        provider="dummy",
        scopes=["read"],
        connected_at=datetime.now(timezone.utc)
    )


@pytest.mark.asyncio
async def test_cookieyes_adapter_reports_cannot_determine(dummy_connection):
    adapter = CookieYesAdapter()
    
    # Test health check is false
    health = await adapter.health_check(dummy_connection)
    assert health.healthy is False
    
    # Test collect explicitly reports Cannot determine
    result = await adapter.collect(dummy_connection)
    
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    
    assert evidence.verdict == Verdict.CANNOT_DETERMINE
    assert "CookieYes API is not currently supported" in evidence.detail
    assert "CookieYes" in evidence.detail
    
    assert evidence.structured_detail is not None
    assert evidence.structured_detail.integration == "cookieyes"


@pytest.mark.asyncio
async def test_securiti_adapter_reports_cannot_determine(dummy_connection):
    adapter = SecuritiAdapter()
    
    # Test health check is false
    health = await adapter.health_check(dummy_connection)
    assert health.healthy is False
    
    # Test collect explicitly reports Cannot determine
    result = await adapter.collect(dummy_connection)
    
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    
    assert evidence.verdict == Verdict.CANNOT_DETERMINE
    assert "Securiti API is not currently supported" in evidence.detail
    assert "Securiti" in evidence.detail

    assert evidence.structured_detail is not None
    assert evidence.structured_detail.integration == "securiti"
