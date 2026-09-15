import pytest
from datetime import datetime, timezone
from app.integrations.gtm.adapter import GTMAdapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict, Confidence

@pytest.mark.asyncio
async def test_gtm_20_tag_fixture(mocker):
    adapter = GTMAdapter()
    connection = ConnectionInfo(provider="gtm", scopes=["read"], connected_at=datetime.now(timezone.utc))
    
    mock_client_class = mocker.patch("app.integrations.gtm.adapter.AuditedHTTPClient")
    mock_instance = mock_client_class.return_value
    mock_instance.get = mocker.AsyncMock()
    mock_instance.close = mocker.AsyncMock()
    
    # Create 20 tags: 5 with consent configured, 15 without
    tags = []
    for i in range(5):
        tags.append({
            "name": f"Configured Tag {i}",
            "type": "html",
            "consentSettings": {"consentStatus": "needed"}
        })
    for i in range(15):
        tags.append({
            "name": f"Unconfigured Tag {i}",
            "type": "html",
            "consentSettings": {"consentStatus": "notSet"}
        })
        
    async def mock_get(endpoint, **kwargs):
        if "accounts" in endpoint and "containers" not in endpoint:
            return {"account": [{"accountId": "123"}]}
        elif "containers" in endpoint and "versions:live" not in endpoint and "version_headers" not in endpoint:
            return {"container": [{"containerId": "456", "name": "Test Container", "publicId": "GTM-XXXX"}]}
        elif "versions:live" in endpoint:
            return {"tag": tags, "trigger": [], "variable": []}
        elif "version_headers" in endpoint:
            return {"containerVersionHeader": []}
        return {}
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection)
    
    # Check evidence for S6.consentGating
    consent_evidence = next((e for e in result.evidence if e.obligation_id == "S6.consentGating"), None)
    
    assert consent_evidence is not None
    assert consent_evidence.verdict == Verdict.NOT_OBSERVED
    assert consent_evidence.confidence == Confidence.HIGH
    assert "15 of 20 tags have no consent settings configured" in consent_evidence.detail
    
    # Check evidence for S12.governance.tagInventory
    inventory_evidence = next((e for e in result.evidence if e.obligation_id == "S12.governance.tagInventory"), None)
    
    assert inventory_evidence is not None
    assert inventory_evidence.verdict == Verdict.OBSERVED
    assert inventory_evidence.raw_metadata["total_tags"] == 20
    assert inventory_evidence.raw_metadata["with_consent"] == 5
    assert inventory_evidence.raw_metadata["without_consent"] == 15
