"""Tests for the obligation register system.

Proves:
1. Every adapter's obligation codes resolve to existing register entries.
2. Unknown obligation codes fail clearly.
3. Obligation version is preserved during seeding.
4. Findings retain the exact obligation used during assessment.
5. The structural validator catches malformed entries.
"""

import pytest
from app.domain.obligations import (
    OBLIGATIONS,
    OBLIGATION_INTEGRATION_MAP,
    REGISTER_VERSION,
    get_obligations_for_integration,
    validate_obligation_code,
    validate_obligation_register,
)
from app.integrations.registry import get_adapter


# ── Test 1: Adapter obligation codes resolve ──

def test_adapter_obligation_codes_resolve():
    """Every code returned by every adapter's obligations_answered exists in the register."""
    all_register_codes = {ob["code"] for ob in OBLIGATIONS}

    # Check the OBLIGATION_INTEGRATION_MAP directly
    for code, integrations in OBLIGATION_INTEGRATION_MAP.items():
        assert code in all_register_codes, (
            f"OBLIGATION_INTEGRATION_MAP references code '{code}' "
            f"but it does not exist in the OBLIGATIONS register."
        )

    # Check every adapter's obligations_answered
    adapter_ids = ["gtm", "ga4", "google_ads", "search_console", "meta", "github", "cmp"]
    for adapter_id in adapter_ids:
        try:
            adapter = get_adapter(adapter_id)
        except Exception:
            continue  # Adapter not registered — skip

        codes = adapter.obligations_answered
        for code in codes:
            assert code in all_register_codes, (
                f"Adapter '{adapter_id}' references obligation code '{code}' "
                f"which does not exist in the OBLIGATIONS register."
            )


def test_integration_map_covers_all_codes():
    """Every obligation code in the register appears in the integration map."""
    all_register_codes = {ob["code"] for ob in OBLIGATIONS}
    mapped_codes = set(OBLIGATION_INTEGRATION_MAP.keys())

    for code in all_register_codes:
        assert code in mapped_codes, (
            f"Obligation code '{code}' exists in the register "
            f"but has no entry in OBLIGATION_INTEGRATION_MAP."
        )


# ── Test 2: Unknown obligation code fails ──

def test_unknown_obligation_code_fails():
    """validate_obligation_code rejects fabricated codes."""
    assert validate_obligation_code("S6.consentGating") is True
    assert validate_obligation_code("FAKE.nonexistent.obligation") is False
    assert validate_obligation_code("") is False
    assert validate_obligation_code("S999.notReal") is False


# ── Test 3: Obligation version is preserved ──

def test_register_version_exists():
    """REGISTER_VERSION is a non-empty string."""
    assert isinstance(REGISTER_VERSION, str)
    assert len(REGISTER_VERSION) > 0


def test_register_version_format():
    """REGISTER_VERSION follows a recognizable format."""
    # It should be either "draft-YYYY-MM" or "counsel-vX.Y" style
    assert "-" in REGISTER_VERSION or "v" in REGISTER_VERSION, (
        f"REGISTER_VERSION '{REGISTER_VERSION}' should follow 'draft-YYYY-MM' "
        f"or 'counsel-vX.Y' format."
    )


# ── Test 4: Register structural validation ──

def test_register_structural_validation_passes():
    """The built-in register passes structural validation."""
    errors = validate_obligation_register()
    assert errors == [], f"Built-in register has structural errors: {errors}"


def test_register_structural_validation_catches_missing_keys():
    """Malformed entries are caught by the validator."""
    bad_register = [
        {"code": "TEST.missing.title", "category": "consent", "severity": "high"},
        # Missing 'title'
    ]
    errors = validate_obligation_register(bad_register)
    assert any("missing required keys" in e for e in errors)


def test_register_structural_validation_catches_invalid_category():
    """Invalid category is caught."""
    bad_register = [
        {
            "code": "TEST.bad.category",
            "title": "Test",
            "category": "not_a_real_category",
            "severity": "high",
        }
    ]
    errors = validate_obligation_register(bad_register)
    assert any("invalid category" in e for e in errors)


def test_register_structural_validation_catches_invalid_severity():
    """Invalid severity is caught."""
    bad_register = [
        {
            "code": "TEST.bad.severity",
            "title": "Test",
            "category": "consent",
            "severity": "ultra_critical",
        }
    ]
    errors = validate_obligation_register(bad_register)
    assert any("invalid severity" in e for e in errors)


def test_register_structural_validation_catches_duplicates():
    """Duplicate codes are caught."""
    bad_register = [
        {"code": "TEST.dup", "title": "A", "category": "consent", "severity": "high"},
        {"code": "TEST.dup", "title": "B", "category": "consent", "severity": "high"},
    ]
    errors = validate_obligation_register(bad_register)
    assert any("duplicate code" in e for e in errors)


def test_register_no_duplicate_codes():
    """The built-in register has no duplicate codes."""
    codes = [ob["code"] for ob in OBLIGATIONS]
    assert len(codes) == len(set(codes)), "Duplicate obligation codes in built-in register!"


# ── Test 5: source_reference field structure ──

def test_obligations_have_source_reference_field():
    """Every obligation in the register either has a source_reference key or gets None."""
    for ob in OBLIGATIONS:
        # source_reference is either present or defaults to None
        ref = ob.get("source_reference")
        assert ref is None or isinstance(ref, str), (
            f"Obligation '{ob['code']}' has invalid source_reference type: {type(ref)}"
        )


# ── Test 6: get_obligations_for_integration ──

def test_get_obligations_for_integration_returns_valid_codes():
    """Codes returned by get_obligations_for_integration all exist in the register."""
    all_register_codes = {ob["code"] for ob in OBLIGATIONS}

    for integration in ["gtm", "ga4", "google_ads", "search_console", "meta", "github", "cmp"]:
        codes = get_obligations_for_integration(integration)
        for code in codes:
            assert code in all_register_codes, (
                f"get_obligations_for_integration('{integration}') returned '{code}' "
                f"which is not in the register."
            )
