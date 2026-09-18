"""Evidence domain contract.

PHASE2_SPEC Section 3 & 6 — Evidence Contract:
  - obligationId, verdict, confidence, source, observedAt, detail
  - Verdict: Observed | Not observed | Cannot determine
  - Confidence: high | medium | low
  - LOW confidence NEVER produces 'Not observed'
  - Two agreeing sources → upgrade to HIGH
  - Conflicting sources → human review, never auto-resolved
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Verdict(str, enum.Enum):
    OBSERVED = "Observed"
    NOT_OBSERVED = "Not observed"
    CANNOT_DETERMINE = "Cannot determine"


class Confidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"



# Metadata keys that must NEVER appear in evidence (PII / secrets)
_FORBIDDEN_METADATA_KEYS = frozenset({
    "email", "phone", "secret", "token", "password", "aadhaar",
    "ssn", "credential", "api_key", "apikey", "private_key",
})


class StructuredEvidence(BaseModel):
    """Detailed metadata representing the 'what, where, why' of a single evidence finding.

    Required fields (always available from every adapter):
        source, integration, operation, resource_type, observed_at, detail

    Optional fields (provider-dependent):
        finding_id — set downstream by FindingService after finding creation
        resource_id, resource_name, location, observed_state, expected_state,
        reason, metadata
    """
    # ── Always-required fields ──
    source: str
    integration: str
    operation: str = Field(..., description="Provider API call or operation that produced this evidence")
    resource_type: str = Field(..., description="Type of the resource being assessed")
    observed_at: datetime = Field(..., description="Timestamp when the observation was made")
    detail: str = Field(..., description="Human-readable detail, no personal data")

    # ── Traceability ──
    finding_id: str | None = Field(
        default=None,
        description="Set by FindingService after the finding is created, completing the evidence chain",
    )

    # ── Provider-dependent fields ──
    resource_id: str | None = None
    resource_name: str | None = None
    location: str = Field(default="Unavailable", description="Precise path/location where this was observed")
    observed_state: str | None = None
    expected_state: str | None = None
    reason: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="after")
    def reject_pii_metadata_keys(self) -> "StructuredEvidence":
        """Hard rule: metadata must NEVER contain keys that suggest PII or secrets."""
        if self.metadata:
            for key in self.metadata:
                if key.lower() in _FORBIDDEN_METADATA_KEYS:
                    raise ValueError(
                        f"Metadata key '{key}' is forbidden — evidence must never store PII or secrets"
                    )
        return self



class EvidenceItem(BaseModel):
    """Evidence collected by an integration adapter.

    This is the contract between adapters and the evidence store.
    Every adapter MUST produce evidence in this format.
    """

    obligation_id: str = Field(
        ...,
        description="Must come from the centralized obligation register",
    )
    verdict: Verdict
    confidence: Confidence
    source: str = Field(
        ...,
        description="Which adapter and API call produced this evidence",
    )
    observed_at: datetime
    detail: str = Field(
        ...,
        description="Human-readable, no personal data",
    )
    raw_metadata: dict[str, Any] | None = Field(
        default=None,
        description="Configuration metadata only, never PII",
    )
    structured_detail: StructuredEvidence | None = Field(
        default=None,
        description="Structured contextual metadata for the finding",
    )

    @model_validator(mode="after")
    def validate_confidence_verdict_rules(self) -> "EvidenceItem":
        """Enforce: LOW confidence can NEVER result in 'Not observed'."""
        if self.confidence == Confidence.LOW and self.verdict == Verdict.NOT_OBSERVED:
            self.verdict = Verdict.CANNOT_DETERMINE
        return self


def apply_confidence_rules(
    evidence_items: list[EvidenceItem],
    obligation_id: str,
) -> tuple[Verdict, Confidence, bool]:
    """Apply confidence rules to determine final verdict for an obligation.

    Rules from PHASE2_SPEC Section 6:
    1. LOW confidence → Cannot determine (never Not observed)
    2. Two agreeing sources → upgrade to HIGH
    3. Conflicting sources → human review required

    Returns:
        (final_verdict, final_confidence, human_review_required)
    """
    if not evidence_items:
        return Verdict.CANNOT_DETERMINE, Confidence.LOW, False

    # Filter to evidence for this obligation
    relevant = [e for e in evidence_items if e.obligation_id == obligation_id]
    if not relevant:
        return Verdict.CANNOT_DETERMINE, Confidence.LOW, False

    # Single source
    if len(relevant) == 1:
        item = relevant[0]
        return item.verdict, item.confidence, False

    # Multiple sources — check for agreement vs contradiction
    verdicts = {e.verdict for e in relevant}
    unique_sources = {e.source.split(":")[0] for e in relevant}

    if len(verdicts) == 1:
        # All sources agree
        agreed_verdict = relevant[0].verdict

        if len(unique_sources) >= 2:
            # Two independent agreeing sources → HIGH confidence
            return agreed_verdict, Confidence.HIGH, False
        else:
            # Same source, multiple evidence — use highest confidence
            best_confidence = _highest_confidence(relevant)
            return agreed_verdict, best_confidence, False

    # Sources disagree — flag for human review, NEVER auto-resolve
    return Verdict.CANNOT_DETERMINE, Confidence.LOW, True


def _highest_confidence(items: list[EvidenceItem]) -> Confidence:
    """Get the highest confidence level from a list of evidence items."""
    order = {Confidence.HIGH: 3, Confidence.MEDIUM: 2, Confidence.LOW: 1}
    best = max(items, key=lambda e: order.get(e.confidence, 0))
    return best.confidence
