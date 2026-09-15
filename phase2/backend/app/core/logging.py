"""Structured logging module.

Provides structured JSON logging with request correlation IDs.
NEVER logs: OAuth tokens, Authorization headers, credentials, raw PII.
Designed for OpenTelemetry compatibility.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog

from app.core.config import get_settings

# Context variables for correlation
request_id_var: ContextVar[str] = ContextVar("request_id", default="")
client_id_var: ContextVar[str] = ContextVar("client_id", default="")
assessment_id_var: ContextVar[str] = ContextVar("assessment_id", default="")
integration_var: ContextVar[str] = ContextVar("integration", default="")

# Fields that must NEVER appear in logs
FORBIDDEN_LOG_FIELDS = frozenset({
    "authorization",
    "auth_header",
    "access_token",
    "refresh_token",
    "token",
    "secret",
    "password",
    "credential",
    "api_key",
    "private_key",
    "client_secret",
    "oauth_token",
    "bearer",
    "cookie",
    "session_id",
    "ssn",
    "social_security",
    "credit_card",
    "card_number",
})

# PII fields that must never be logged
PII_LOG_FIELDS = frozenset({
    "email",
    "email_address",
    "phone",
    "phone_number",
    "name",
    "first_name",
    "last_name",
    "full_name",
    "address",
    "date_of_birth",
    "dob",
    "aadhaar",
    "pan",
    "passport",
    "user_id_external",
    "customer_id",
    "ip_address",
})


def _sanitize_log_event(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Remove any forbidden or PII fields from log events."""
    keys_to_redact = []
    for key in event_dict:
        key_lower = key.lower()
        if key_lower in FORBIDDEN_LOG_FIELDS or key_lower in PII_LOG_FIELDS:
            keys_to_redact.append(key)

    for key in keys_to_redact:
        event_dict[key] = "[REDACTED]"

    # Sanitize string values that look like they contain tokens
    for key, value in event_dict.items():
        if isinstance(value, str) and key not in keys_to_redact:
            if value.startswith("Bearer ") or value.startswith("Basic "):
                event_dict[key] = "[REDACTED_AUTH]"

    return event_dict


def _add_context_vars(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Add context variables (request_id, client_id, etc.) to log events."""
    rid = request_id_var.get("")
    if rid:
        event_dict["request_id"] = rid

    cid = client_id_var.get("")
    if cid:
        event_dict["client_id"] = cid

    aid = assessment_id_var.get("")
    if aid:
        event_dict["assessment_id"] = aid

    integ = integration_var.get("")
    if integ:
        event_dict["integration"] = integ

    return event_dict


def setup_logging() -> None:
    """Configure structured logging for the application."""
    settings = get_settings()

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.UnicodeDecoder(),
            _add_context_vars,  # type: ignore[list-item]
            _sanitize_log_event,  # type: ignore[list-item]
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer() if settings.is_production else structlog.dev.ConsoleRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # Suppress noisy third-party loggers
    for logger_name in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str = __name__) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


def generate_request_id() -> str:
    """Generate a unique request ID for correlation."""
    return str(uuid.uuid4())
