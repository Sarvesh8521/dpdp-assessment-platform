import pytest

async def dummy_audit(*args, **kwargs): pass

from datetime import datetime, timezone
from app.integrations.gtm.adapter import GTMAdapter
from app.integrations.base.adapter import ConnectionInfo
from app.domain.evidence import Verdict, Confidence

from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
@patch("app.integrations.gtm.adapter.AuditedHTTPClient")
async def test_gtm_20_tag_fixture(mock_client_class):
    adapter = GTMAdapter()
    connection = ConnectionInfo(provider="gtm", scopes=["read"], connected_at=datetime.now(timezone.utc))
    
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()
    
    # Create 20 tags: 5 with consent configured, 15 without
    tags = []
    for i in range(5):
        tags.append({
            "tagId": f"t_conf_{i}",
            "name": f"Configured Tag {i}",
            "type": "html",
            "consentSettings": {"consentStatus": "needed"},
            "firingTriggerId": ["trig1"]
        })
    for i in range(15):
        tags.append({
            "tagId": f"t_unconf_{i}",
            "name": f"Unconfigured Tag {i}",
            "type": "html",
            "consentSettings": {"consentStatus": "notSet"},
            "firingTriggerId": ["trig2"]
        })
        
    async def mock_get(endpoint, **kwargs):
        if "accounts" in endpoint and "containers" not in endpoint:
            return {"account": [{"accountId": "123"}]}
        elif "containers" in endpoint and "versions:live" not in endpoint and "version_headers" not in endpoint:
            return {"container": [{"containerId": "456", "name": "Test Container", "publicId": "GTM-XXXX"}]}
        elif "versions:live" in endpoint:
            return {
                "tag": tags, 
                "trigger": [
                    {"triggerId": "trig1", "name": "All Pages"},
                    {"triggerId": "trig2", "name": "Window Loaded"}
                ], 
                "variable": []
            }
        elif "version_headers" in endpoint:
            return {"containerVersionHeader": [
                {
                    "containerVersionId": "v1",
                    "name": "Initial Publish",
                    "numTags": 10,
                    "numTriggers": 2
                },
                {
                    "containerVersionId": "v2",
                    "name": "Consent Update",
                    "numTags": 20,
                    "numTriggers": 2
                }
            ]}
        return {}
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection, dummy_audit)
    
    # Check evidence for S6.consentGating (Tags)
    consent_findings = [e for e in result.evidence if e.obligation_id == "S6.consentGating"]
    
    # Exactly 20 tags
    assert len(consent_findings) == 20
    
    configured_findings = [f for f in consent_findings if f.verdict == Verdict.NOT_OBSERVED]
    unconfigured_findings = [f for f in consent_findings if f.verdict == Verdict.OBSERVED]
    
    assert len(configured_findings) == 5
    assert len(unconfigured_findings) == 15
    
    # Verify the GTM finding structure is respected
    sample_finding = consent_findings[0]
    assert sample_finding.structured_detail.resource_type == "tag"
    assert "container" in sample_finding.structured_detail.metadata
    assert "tag" in sample_finding.structured_detail.metadata
    assert "trigger" in sample_finding.structured_detail.metadata
    assert "configuration" in sample_finding.structured_detail.metadata
    assert sample_finding.structured_detail.operation == "get_live_version"
    
    # Check evidence for version history
    version_findings = [e for e in result.evidence if e.structured_detail and e.structured_detail.resource_type == "version"]
    assert len(version_findings) == 2
    
    v2_finding = next(v for v in version_findings if v.structured_detail.resource_id == "v2")
    assert v2_finding.structured_detail.metadata["change_metadata"]["numTags"] == 20
    
    # Check evidence for S12.governance.tagInventory
    inventory_evidence = next((e for e in result.evidence if e.source == "gtm:tag_inventory"), None)
    
    assert inventory_evidence is not None
    assert inventory_evidence is not None
    assert inventory_evidence.verdict == Verdict.NOT_OBSERVED
    assert inventory_evidence.raw_metadata["total_tags"] == 20
    assert inventory_evidence.raw_metadata["with_consent"] == 5
    assert inventory_evidence.raw_metadata["without_consent"] == 15

@pytest.mark.asyncio
@patch("app.integrations.gtm.adapter.AuditedHTTPClient")
async def test_gtm_empty_container(mock_client_class):
    adapter = GTMAdapter()
    connection = ConnectionInfo(provider="gtm", scopes=["read"], connected_at=datetime.now(timezone.utc))
    
    mock_instance = mock_client_class.return_value
    mock_instance.get = AsyncMock()
    mock_instance.close = AsyncMock()
    
    async def mock_get(endpoint, **kwargs):
        if "accounts" in endpoint and "containers" not in endpoint:
            return {"account": [{"accountId": "123"}]}
        elif "containers" in endpoint and "versions:live" not in endpoint and "version_headers" not in endpoint:
            return {"container": [{"containerId": "456", "name": "Test Container", "publicId": "GTM-XXXX"}]}
        elif "versions:live" in endpoint:
            return {
                "tag": [], 
                "trigger": [], 
                "variable": []
            }
        elif "version_headers" in endpoint:
            return {"containerVersionHeader": []}
        return {}
        
    mock_instance.get.side_effect = mock_get
    
    result = await adapter.collect(connection, dummy_audit)
    
    # S6.consentGating should have NO evidence because there are no tags
    consent_findings = [e for e in result.evidence if e.obligation_id == "S6.consentGating"]
    assert len(consent_findings) == 0
    
    # S12.governance.tagInventory should explicitly emit NOT_OBSERVED because a zero-tag inventory is still valid
    inventory_evidence = next((e for e in result.evidence if e.source == "gtm:tag_inventory"), None)
    
    assert inventory_evidence is not None
    assert inventory_evidence.verdict == Verdict.NOT_OBSERVED
    assert inventory_evidence.raw_metadata["total_tags"] == 0

