from app.security.pii_sanitizer import (
    sanitize_dict,
    validate_no_pii_in_evidence,
    AdapterSanitizer,
)

def test_pii_field_names_stripping():
    # Mix of safe and unsafe fields
    raw_data = {
        "id": "12345",
        "email": "user@example.com",
        "name": "John Doe",
        "phone": "555-1234",
        "configuration": {"active": True},
        "ssn": "000-00-0000"
    }
    
    # Default stripping
    sanitized = sanitize_dict(raw_data)
    
    assert "id" in sanitized
    assert "configuration" in sanitized
    assert "email" not in sanitized
    assert "name" not in sanitized
    assert "phone" not in sanitized
    assert "ssn" not in sanitized

def test_nested_pii_stripping():
    raw_data = {
        "id": "123",
        "nested": {
            "metadata": {
                "ip_address": "192.168.1.1",
                "safe_val": "ok"
            }
        },
        "list_data": [
            {"user_id": "u1", "role": "admin"},
            {"user_id": "u2", "role": "user"}
        ]
    }
    
    sanitized = sanitize_dict(raw_data)
    assert "ip_address" not in sanitized["nested"]["metadata"]
    assert "safe_val" in sanitized["nested"]["metadata"]
    assert "user_id" not in sanitized["list_data"][0]
    assert "role" in sanitized["list_data"][0]

def test_validate_no_pii_in_evidence():
    safe_data = {"id": "1", "status": "active"}
    is_safe, violations = validate_no_pii_in_evidence(safe_data)
    assert is_safe
    assert not violations
    
    unsafe_data = {"id": "1", "user_email": "test@test.com"}
    is_safe, violations = validate_no_pii_in_evidence(unsafe_data)
    assert not is_safe
    assert any("user_email" in v for v in violations)

def test_adapter_sanitizer_validation():
    sanitizer = AdapterSanitizer(
        allowed_fields=frozenset({"id", "status"}),
        forbidden_fields=frozenset({"secret_key"}),
    )
    
    data = {"id": "123", "status": "ok", "secret_key": "xyz", "email": "a@b.com"}
    
    # Validate should find forbidden field and PII field that isn't allowed
    is_safe, violations = sanitizer.validate(data)
    assert not is_safe
    assert any("secret_key" in v for v in violations)
    assert any("email" in v for v in violations)
    
    # Sanitize should remove them
    sanitized = sanitizer.sanitize(data)
    assert "id" in sanitized
    assert "secret_key" not in sanitized
    assert "email" not in sanitized
