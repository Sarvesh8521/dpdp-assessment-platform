import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession
from app.services.finding_service import FindingService, _generate_reason
from app.domain.evidence import Verdict
from app.db.models.obligation import Obligation

def test_generate_reason_cannot_determine():
    """Test generating reason for CANNOT_DETERMINE verdict."""
    obligation = Obligation(title="Test Obligation")
    sd = {
        "observed_state": "Missing",
        "expected_state": "Configured",
        "resource_type": "Tag",
        "resource_name": "My Tag"
    }
    
    reason_str = _generate_reason(Verdict.CANNOT_DETERMINE, sd, obligation)
    
    assert "Observed:\nMissing" in reason_str
    assert "Expected:\nConfigured" in reason_str
    assert "Required provider information is not exposed by the available API" in reason_str


def test_generate_reason_observed_violation():
    """Test generating reason for OBSERVED verdict (violation)."""
    obligation = Obligation(title="Test Obligation")
    sd = {
        "observed_state": "Plaintext email found",
        "expected_state": "Hashed email",
        "resource_type": "Pixel",
        "resource_name": "Meta Pixel"
    }
    
    reason_str = _generate_reason(Verdict.OBSERVED, sd, obligation)
    
    assert "Observed:\nPlaintext email found" in reason_str
    assert "Expected:\nHashed email" in reason_str
    assert "The Pixel 'Meta Pixel' is configured with state 'Plaintext email found', which violates the obligation 'Test Obligation'." in reason_str


def test_generate_reason_not_observed_compliant():
    """Test generating reason for NOT_OBSERVED verdict (compliant)."""
    obligation = Obligation(title="Consent Mapped")
    sd = {
        "observed_state": "Has consentSettings",
        "expected_state": "Has consentSettings",
        "resource_type": "Tag",
        "resource_name": "GA4 Tag"
    }
    
    reason_str = _generate_reason(Verdict.NOT_OBSERVED, sd, obligation)
    
    assert "Observed:\nHas consentSettings" in reason_str
    assert "The Tag 'GA4 Tag' is configured with state 'Has consentSettings', which meets the obligation 'Consent Mapped'." in reason_str


def test_generate_reason_uses_explicit_reason_if_provided():
    """Test generating reason uses the explicit adapter reason if available."""
    obligation = Obligation(title="Consent Mapped")
    sd = {
        "observed_state": "Has consentSettings",
        "expected_state": "Has consentSettings",
        "resource_type": "Tag",
        "resource_name": "GA4 Tag",
        "reason": "Custom reason provided by adapter"
    }
    
    reason_str = _generate_reason(Verdict.NOT_OBSERVED, sd, obligation)
    
    assert "Reason:\n\"Custom reason provided by adapter\"" in reason_str

