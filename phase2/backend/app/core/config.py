"""Core configuration module.

Environment-driven configuration using Pydantic Settings.
Validates all required settings at startup.
Supports: development, test, staging, production profiles.
"""

from __future__ import annotations

import enum
from functools import lru_cache
from typing import Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, enum.Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──
    app_name: str = "DPDP Assessment Platform"
    app_version: str = "2.0.0"
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    log_level: str = "INFO"

    # ── Server ──
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 1
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # ── Database ──
    database_url: str = "postgresql+asyncpg://dpdp:dpdp_secret@localhost:5432/dpdp_phase2"
    database_pool_size: int = 20
    database_max_overflow: int = 10
    database_pool_timeout: int = 30
    database_echo: bool = False

    # ── Redis ──
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── Auth / JWT ──
    secret_key: str = "CHANGE-ME-IN-PRODUCTION-use-openssl-rand-hex-64"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── Secret Management ──
    secret_backend: str = "local"  # "local" | "gcp"
    gcp_project_id: str = ""
    gcp_secret_prefix: str = "dpdp-phase2"
    local_secret_encryption_key: str = "CHANGE-ME-32-byte-key-for-local!"

    # ── OAuth Config ──
    google_client_id: str = ""
    google_client_secret: str = ""
    google_oauth_redirect_uri: str = "http://localhost:8001/api/v1/integrations/oauth/google/callback"
    meta_oauth_redirect_uri: str = "http://localhost:8001/api/v1/integrations/oauth/meta/callback"
    github_oauth_redirect_uri: str = "http://localhost:8001/api/v1/integrations/oauth/github/callback"

    # ── Frontend ──
    frontend_url: str = "http://localhost:5173"

    # ── Demo Mode ──
    demo_mode: bool = False

    # ── Meta OAuth ──
    meta_app_id: str = ""
    meta_app_secret: str = ""

    # ── GitHub App ──
    github_app_id: str = ""
    github_app_private_key: str = ""  # PEM-encoded private key
    github_app_install_url: str = ""  # e.g. https://github.com/apps/your-app/installations/new
    github_client_id: str = ""
    github_client_secret: str = ""

    # ── Resend Mail ──
    resend_api_key: str = ""
    mail_from: str = "DPDP Scanner <onboarding@resend.dev>"

    # ── Gmail SMTP ──
    mail_host: str = ""
    mail_port: int = 587
    mail_user: str = ""
    mail_password: str = ""

    # ── Storage ──
    storage_backend: str = "local"  # "local" | "gcs"
    gcs_bucket_name: str = ""
    local_storage_path: str = "./storage"

    # ── Engagement / Time-boxing ──
    default_token_lifetime_days: int = 90
    default_evidence_retention_days: int = 30

    # ── Rate Limiting ──
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # ── External API Defaults ──
    external_api_timeout_seconds: int = 30
    external_api_max_retries: int = 3
    external_api_backoff_base: float = 1.0
    external_api_backoff_max: float = 60.0

    @field_validator("secret_key")
    @classmethod
    def validate_secret_key(cls, v: str, info: Any) -> str:
        env = info.data.get("environment", Environment.DEVELOPMENT)
        if env in (Environment.STAGING, Environment.PRODUCTION):
            if v == "CHANGE-ME-IN-PRODUCTION-use-openssl-rand-hex-64":
                raise ValueError(
                    "secret_key must be changed from default in staging/production"
                )
            if len(v) < 32:
                raise ValueError("secret_key must be at least 32 characters")
        return v

    @field_validator("local_secret_encryption_key")
    @classmethod
    def validate_encryption_key(cls, v: str, info: Any) -> str:
        env = info.data.get("environment", Environment.DEVELOPMENT)
        if env in (Environment.STAGING, Environment.PRODUCTION):
            if "CHANGE-ME" in v:
                raise ValueError(
                    "local_secret_encryption_key must be changed in staging/production"
                )
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_test(self) -> bool:
        return self.environment == Environment.TEST


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
