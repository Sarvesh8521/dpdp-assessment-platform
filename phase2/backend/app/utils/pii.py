from typing import Any

# High-risk exact keys that often contain PII
PII_EXACT_KEYS = {
    "email", "phone", "phonenumber", "firstname", "lastname", 
    "userid", "user_id", "ip", "address", "ssn", "aadhaar", 
    "customer_id", "customerid"
}

# High-risk list/object structures that should not be persisted entirely
PII_OBJECT_KEYS = {
    "members", "users", "events", "payloads", 
    "consent_records", "receipts", "event_payloads"
}

def sanitize_payload(data: Any) -> Any:
    """
    Recursively sanitize dictionaries and lists.
    - Replaces values for known PII keys with '[REDACTED]'.
    - Replaces values for high-risk arrays/objects with '[REDACTED_OBJECT]'.
    """
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            k_lower = str(k).lower().replace("-", "_")
            if k_lower in PII_EXACT_KEYS:
                sanitized[k] = "[REDACTED]"
            elif k_lower in PII_OBJECT_KEYS:
                sanitized[k] = "[REDACTED_OBJECT]"
            else:
                sanitized[k] = sanitize_payload(v)
        return sanitized
    elif isinstance(data, list):
        return [sanitize_payload(item) for item in data]
    else:
        return data
