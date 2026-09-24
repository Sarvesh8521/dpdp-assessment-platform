"""PII Sanitizer module.

Central PII detection and removal layer. All adapter responses MUST pass
through this sanitizer before reaching persistence.

HARD RULE (PHASE2_SPEC Section 1, Rule 1):
  Never store personal data. The tool reads configuration and metadata,
  never customer records. If an API returns personal data, discard it
  in the adapter layer before it reaches storage.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── PII Field Detection ──
# Fields whose VALUES are personal data and must never be stored
PII_FIELD_NAMES = frozenset({
    # Names
    "name", "first_name", "last_name", "full_name", "display_name",
    "given_name", "family_name", "middle_name", "user_name", "username",
    # Contact
    "email", "email_address", "e_mail", "mail",
    "phone", "phone_number", "telephone", "mobile", "cell",
    "address", "street", "city", "zip", "postal_code", "zip_code",
    # Identity
    "ssn", "social_security", "aadhaar", "pan", "passport", "passport_number",
    "national_id", "tax_id", "voter_id", "driving_license",
    # Financial
    "credit_card", "card_number", "cvv", "bank_account", "iban", "swift",
    "account_number",
    # Digital identifiers
    "ip_address", "ip", "device_id", "advertising_id", "idfa", "gaid",
    "mac_address", "imei",
    # User-level data
    "user_id", "customer_id", "subscriber_id", "member_id",
    "date_of_birth", "dob", "birth_date", "age",
    "gender", "sex", "ethnicity", "race", "religion",
    # Consent records (individual)
    "consent_record", "consent_string", "tc_string",
    "individual_consent", "user_consent",
    # Event payloads
    "event_payload", "user_event", "user_activity",
    "behavior", "behaviour", "browsing_history",
    # Audience membership
    "audience_members", "member_list", "user_list_members",
    "list_members", "membership",
    # Credentials
    "password", "secret", "token", "access_token", "refresh_token",
    "api_key", "private_key", "client_secret", "credential",
    "authorization", "bearer_token",
})

# Fields whose NAMES suggest PII but need flagging rather than removal
PII_SUSPECT_PATTERNS = [
    re.compile(r"(?:^|_)email(?:$|_)", re.IGNORECASE),
    re.compile(r"(?:^|_)phone(?:$|_)", re.IGNORECASE),
    re.compile(r"(?:^|_)name(?:$|_)", re.IGNORECASE),
    re.compile(r"(?:^|_)user(?:_id|Id)(?:$|_)", re.IGNORECASE),
    re.compile(r"(?:^|_)customer(?:_id|Id)(?:$|_)", re.IGNORECASE),
    re.compile(r"(?:^|_)ip(?:_address|Address)?(?:$|_)", re.IGNORECASE),
]

# Fields that are SAFE and should never be stripped (configuration/metadata)
SAFE_FIELD_PATTERNS = frozenset({
    "id", "type", "kind", "status", "state", "enabled", "disabled",
    "created_at", "updated_at", "modified_at", "published_at",
    "version", "revision", "count", "size", "approximate_size",
    "configuration", "config", "settings", "setting",
    "scope", "scopes", "permission", "permissions",
    "category", "categories", "tag", "tags",
    "trigger", "triggers", "variable", "variables",
    "container", "workspace", "property", "stream",
    "retention", "duration", "lifetime", "expiration",
    "linked", "connection", "integration",
    "consent_settings", "consent_mode", "consent_type",
    "firing_rule", "blocking_rule",
    "vendor", "provider", "platform",
    "subtype", "origin", "sharing",
    "measurement", "metric", "dimension",
    "sitemap", "verified", "verification",
    "description", "title", "label",
    "hash", "fingerprint", "checksum",
    "account_id", "container_id", "property_id",
    "workspace_id", "tag_id", "trigger_id",
    "resource_name", "container_name", "account_name", "property_name", 
    "workspace_name", "client_name", "display_name", "name",
})


def is_pii_field(field_name: str) -> bool:
    """Check if a field name indicates PII content."""
    normalized = field_name.lower().strip()
    if normalized in PII_FIELD_NAMES:
        return True
    for pattern in PII_SUSPECT_PATTERNS:
        if pattern.search(normalized):
            # Check if it's a safe field that happens to match
            if normalized not in SAFE_FIELD_PATTERNS:
                return True
    return False


def sanitize_value(value: Any, field_name: str = "") -> Any:
    """Sanitize a single value, removing PII."""
    if value is None:
        return None
    if isinstance(value, dict):
        return sanitize_dict(value)
    if isinstance(value, list):
        return [sanitize_value(item, field_name) for item in value]
    if isinstance(value, str):
        return sanitize_string(value)
    return value

def sanitize_string(text: str | None) -> str | None:
    """Redact emails and phone numbers from a string."""
    if not text:
        return text
    
    # Redact Emails
    email_pattern = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
    text = email_pattern.sub('[REDACTED_EMAIL]', text)
    
    # Redact common phone patterns (basic heuristic)
    # Matches +1-234-567-8900, (123) 456-7890, 123-456-7890
    phone_pattern = re.compile(r'(?:(?:\+?1\s*(?:[.-]\s*)?)?(?:\(\s*([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9])\s*\)|([2-9]1[02-9]|[2-9][02-8]1|[2-9][02-8][02-9]))\s*(?:[.-]\s*)?)?([2-9]1[02-9]|[2-9][02-9]1|[2-9][02-9]{2})\s*(?:[.-]\s*)?([0-9]{4})(?:\s*(?:#|x\.?|ext\.?|extension)\s*(\d+))?')
    
    # Simpler phone pattern to avoid catastrophic backtracking and match common ones
    # (Matches 10+ digits with optional separators)
    simple_phone_pattern = re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b')
    text = simple_phone_pattern.sub('[REDACTED_PHONE]', text)
    
    return text


def sanitize_dict(
    data: dict[str, Any],
    *,
    allowed_fields: frozenset[str] | None = None,
    forbidden_fields: frozenset[str] | None = None,
) -> dict[str, Any]:
    """Sanitize a dictionary by removing PII fields.

    Args:
        data: The dictionary to sanitize.
        allowed_fields: If provided, ONLY these fields are kept (allowlist mode).
        forbidden_fields: If provided, these fields are always removed in addition
                         to standard PII detection.

    Returns:
        Sanitized dictionary with PII removed.
    """
    if not isinstance(data, dict):
        return data

    result: dict[str, Any] = {}
    removed_fields: list[str] = []

    for key, value in data.items():
        # Explicit forbidden list takes priority
        if forbidden_fields and key.lower() in forbidden_fields:
            removed_fields.append(key)
            continue

        # Allowlist mode: only keep explicitly allowed fields
        if allowed_fields is not None:
            if key.lower() not in allowed_fields:
                removed_fields.append(key)
                continue
            result[key] = sanitize_value(value, key)
            continue

        # Default mode: remove PII fields
        if is_pii_field(key):
            removed_fields.append(key)
            continue

        result[key] = sanitize_value(value, key)

    if removed_fields:
        logger.info(
            "pii_fields_removed",
            removed_fields=removed_fields,
            field_count=len(removed_fields),
        )

    return result


def hash_content(content: str) -> str:
    """Create a SHA-256 hash of content for safe storage.

    Used for custom HTML tag bodies and other content that should not
    be stored verbatim but needs identification.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def sanitize_html_tag_body(html_body: str) -> dict[str, Any]:
    """Sanitize a custom HTML tag body for storage.

    Per PHASE2_SPEC Section 4.1: Do not store custom HTML tag bodies
    verbatim. Store a hash and classification instead.
    """
    return {
        "hash": hash_content(html_body),
        "length": len(html_body),
        "contains_script": "<script" in html_body.lower(),
        "contains_iframe": "<iframe" in html_body.lower(),
        "contains_img": "<img" in html_body.lower(),
        "classification": _classify_html_tag(html_body),
    }


def _classify_html_tag(html_body: str) -> str:
    """Classify an HTML tag body by its likely purpose."""
    body_lower = html_body.lower()

    if any(tracker in body_lower for tracker in [
        "fbq(", "facebook", "meta pixel", "_fbq",
    ]):
        return "meta_tracking"
    if any(tracker in body_lower for tracker in [
        "gtag(", "ga(", "google-analytics", "analytics.js",
    ]):
        return "google_analytics"
    if any(tracker in body_lower for tracker in [
        "adsbygoogle", "google_ads", "googleads", "conversion",
    ]):
        return "google_ads"
    if any(tracker in body_lower for tracker in [
        "linkedin", "snap", "tiktok", "twitter", "pinterest",
    ]):
        return "social_tracking"
    if any(tracker in body_lower for tracker in [
        "hotjar", "clarity", "mouseflow", "fullstory", "session",
    ]):
        return "session_recording"
    if any(tracker in body_lower for tracker in [
        "onetrust", "cookieyes", "securiti", "consent",
    ]):
        return "consent_management"

    return "custom_html"


def validate_no_pii_in_evidence(evidence_data: dict[str, Any]) -> tuple[bool, list[str]]:
    """Validate that evidence data contains no PII.

    Returns:
        Tuple of (is_safe, list of violations found).
    """
    violations: list[str] = []
    _check_dict_for_pii(evidence_data, "", violations)
    return len(violations) == 0, violations


def _check_dict_for_pii(
    data: Any, path: str, violations: list[str]
) -> None:
    """Recursively check a data structure for PII fields."""
    if isinstance(data, dict):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            if is_pii_field(key):
                violations.append(f"PII field detected: {current_path}")
            _check_dict_for_pii(value, current_path, violations)
    elif isinstance(data, list):
        for i, item in enumerate(data):
            _check_dict_for_pii(item, f"{path}[{i}]", violations)


def create_adapter_sanitizer(
    allowed_fields: frozenset[str],
    forbidden_fields: frozenset[str],
) -> "AdapterSanitizer":
    """Create a sanitizer configured for a specific adapter.

    Each adapter defines its safe and forbidden fields explicitly.
    """
    return AdapterSanitizer(
        allowed_fields=allowed_fields,
        forbidden_fields=forbidden_fields,
    )


class AdapterSanitizer:
    """Adapter-specific sanitizer with explicit field definitions.

    Each adapter MUST define:
    - allowed_fields: Fields that are safe to persist
    - forbidden_fields: Fields that must NEVER be persisted
    """

    def __init__(
        self,
        allowed_fields: frozenset[str],
        forbidden_fields: frozenset[str],
    ) -> None:
        self.allowed_fields = allowed_fields
        self.forbidden_fields = forbidden_fields

    def sanitize(self, data: dict[str, Any]) -> dict[str, Any]:
        """Sanitize data using this adapter's field rules."""
        result = sanitize_dict(
            data,
            allowed_fields=self.allowed_fields,
            forbidden_fields=self.forbidden_fields,
        )

        # Final validation
        is_safe, violations = validate_no_pii_in_evidence(result)
        if not is_safe:
            logger.error(
                "pii_leak_prevented",
                violations=violations,
            )
            # Re-sanitize more aggressively
            for violation in violations:
                field = violation.split(": ")[-1].split(".")[0]
                result.pop(field, None)

        return result

    def validate(self, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate data against this adapter's rules without modifying."""
        violations: list[str] = []
        for key in data:
            if key.lower() in self.forbidden_fields:
                violations.append(f"Forbidden field present: {key}")
            if is_pii_field(key) and key.lower() not in self.allowed_fields:
                violations.append(f"PII field present: {key}")
        return len(violations) == 0, violations
