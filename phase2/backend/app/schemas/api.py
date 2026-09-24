"""API response and request schemas.

Pydantic models for API input validation and output serialization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.assessment_run import RunStatus
from app.db.models.connection import ConnectionStatus

T = TypeVar("T")


class PaginatedResponse(BaseModel):
    """Standard paginated response."""
    items: list[Any]
    total: int
    page: int
    size: int
    has_more: bool = False


class ErrorResponse(BaseModel):
    """Standard error response."""
    detail: str
    code: str | None = None
    validation_errors: list[dict[str, Any]] | None = None


# ── Auth Schemas ──

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_label: str | None = None


class GoogleTokenRequest(BaseModel):
    access_token: str


class SendOtpRequest(BaseModel):
    email: str


class VerifyOtpLoginRequest(BaseModel):
    email: str
    otp: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordOtpRequest(BaseModel):
    email: str
    otp: str
    new_password: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str



# ── User & Client Schemas ──

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    identity: str
    status: str
    created_at: datetime


class ClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    status: str
    created_at: datetime


# ── Integration & Connection Schemas ──

class ConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    provider: str
    status: ConnectionStatus
    scopes: list[str] | None = None
    connected_at: datetime | None = None
    expires_at: datetime | None = None
    provider_metadata: dict[str, Any] | None = None
    resource_id: str | None = None
    resource_name: str | None = None


class ConnectRequest(BaseModel):
    """Connect request for non-OAuth providers (CMP, manual config).

    For Google OAuth integrations, the backend callback creates the connection
    automatically. This is used only for CMP API keys and Google Ads customer ID.
    """
    provider: str
    oauth_code: str | None = None  # Used for CMP API key
    resource: dict[str, Any] | None = None  # { id, customer_id, site_id, ... }
    provider_name: str | None = None  # For CMP sub-providers (onetrust, cookieyes, securiti)


class OAuthInitResponse(BaseModel):
    """Response containing the OAuth authorization URL."""
    auth_url: str
    state: str  # CSRF state parameter (for frontend reference only — validated server-side)
    provider: str


class ResourceItem(BaseModel):
    """A single selectable resource."""
    id: str
    name: str
    metadata: dict[str, Any] | None = None


class ResourceListResponse(BaseModel):
    """List of available resources for an integration."""
    provider: str
    resources: list[ResourceItem]


class IntegrationStatusItem(BaseModel):
    """Status of a single integration."""
    provider: str
    status: str  # not_connected, connecting, connected, error
    connection_id: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    resource_selected: bool = False
    requires_resource_selection: bool = True
    connected_at: datetime | None = None
    error: str | None = None
    provider_metadata: dict[str, Any] | None = None


class SelectResourcePayload(BaseModel):
    """Payload for resource selection with extra metadata."""
    provider_metadata: dict[str, Any] | None = None


class IntegrationStatusResponse(BaseModel):
    """Status of all integrations for a client."""
    integrations: list[IntegrationStatusItem]


# ── Assessment Schemas ──

class DeclaredScope(BaseModel):
    domains: list[str] = Field(default_factory=list)
    subdomains: list[str] = Field(default_factory=list)
    analytics_properties: list[str] = Field(default_factory=list)
    search_console_properties: list[str] = Field(default_factory=list)
    google_ads_accounts: list[str] = Field(default_factory=list)
    marketing_platforms: list[str] = Field(default_factory=list)
    repositories: list[str] = Field(default_factory=list)
    mobile_applications: list[str] = Field(default_factory=list)
    cmp_platforms: list[str] = Field(default_factory=list)

class AssessmentConfig(BaseModel):
    integrations: list[str]
    scope: DeclaredScope = Field(default_factory=DeclaredScope)
    # Legacy flat fields for backward compatibility with frontend
    domains: list[str] = Field(default_factory=list)
    properties: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    ad_accounts: list[str] = Field(default_factory=list)
    github_repos: list[str] = Field(default_factory=list)
    repositories: list[str] = Field(default_factory=list)
    mobile_apps: list[str] = Field(default_factory=list)
    declared_properties: list[str] | None = None
    cmp_provider: str | None = None
    is_demo: bool = False
    phase1_results: list[dict[str, Any]] | None = None


class AssessmentCreate(BaseModel):
    client_id: uuid.UUID
    config: AssessmentConfig


class AssessmentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    integration: str
    status: RunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_reason: str | None = None
    evidence_count: int = 0


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    client_id: uuid.UUID
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_by: uuid.UUID
    configuration: dict[str, Any] | None = None
    created_at: datetime | None = None
    runs: list[AssessmentRunResponse] = Field(default_factory=list)


# ── Finding Schemas ──

class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    assessment_id: uuid.UUID
    obligation_id: uuid.UUID
    verdict: str
    confidence: str
    severity: str
    detail: str
    status: str
    human_review_required: bool
    recommended_action: str | None = None
    sources: list[str] | None = None
    evidence_details: list[dict] | None = None
    created_at: datetime


# ── Override Schemas ──

class HumanOverrideRequest(BaseModel):
    new_verdict: str
    reason: str


class HumanOverrideResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    finding_id: uuid.UUID
    previous_verdict: str
    new_verdict: str
    reason: str
    user_id: uuid.UUID
    timestamp: datetime
