import pytest
from app.services.finding_service import _generate_recommended_action
from app.db.models.evidence import Verdict


def test_generate_recommended_action_cannot_determine():
    action = _generate_recommended_action(Verdict.CANNOT_DETERMINE, ["gtm:container_123"])
    assert "Provide the necessary client authorization" in action


def test_generate_recommended_action_gtm():
    action1 = _generate_recommended_action(Verdict.OBSERVED, ["gtm:container_123"], "S6.consentGating")
    assert action1 == "Configure the identified tag with the required consent settings."
    action2 = _generate_recommended_action(Verdict.OBSERVED, ["gtm:container_123"], "S9.sharing.enhancedConversions")
    assert action2 == "Review the identified tag transmitting user data for compliance."
    action3 = _generate_recommended_action(Verdict.OBSERVED, ["gtm:container_123"], "S12.governance.tagInventory")
    assert action3 == "Review the tag inventory and remove unused/unapproved tags."
    action4 = _generate_recommended_action(Verdict.OBSERVED, ["gtm:container_123"], "Unknown.code")
    assert action4 == "Review the identified GTM configuration."

    # Test that adapter_action takes precedence over obligation-specific defaults
    action5 = _generate_recommended_action(
        Verdict.OBSERVED, 
        ["gtm:container_123"], 
        "S6.consentGating",
        adapter_action="Custom adapter action"
    )
    assert action5 == "Custom adapter action"

def test_generate_recommended_action_ga4():
    action = _generate_recommended_action(Verdict.OBSERVED, ["ga4:property_123"])
    assert action == "Review the identified retention configuration and document/revise it as appropriate."


def test_generate_recommended_action_google_ads():
    action = _generate_recommended_action(Verdict.OBSERVED, ["google_ads:customer_123"])
    assert action == "Review the identified audience/list against the applicable consent/purpose basis."


def test_generate_recommended_action_meta():
    action = _generate_recommended_action(Verdict.OBSERVED, ["meta:business_123"])
    assert action == "Review the identified audience-sharing or Advanced Matching configuration."


def test_generate_recommended_action_github():
    # Generic fallback
    action1 = _generate_recommended_action(Verdict.OBSERVED, ["github:repo_123"])
    assert action1 == "Review the identified code component for compliance."

    # Specific GitHub obligations via adapter_action
    action2 = _generate_recommended_action(
        Verdict.OBSERVED, 
        ["github:repo_123"], 
        "APP.forms.disclosure", 
        adapter_action="Add a privacy disclosure statement near the form."
    )
    assert action2 == "Add a privacy disclosure statement near the form."

    action3 = _generate_recommended_action(
        Verdict.OBSERVED, 
        ["github:repo_123"], 
        "APP.sdk.consent", 
        adapter_action="Gate initialization behind a consent check."
    )
    assert action3 == "Gate initialization behind a consent check."


def test_generate_recommended_action_search_console():
    action = _generate_recommended_action(Verdict.OBSERVED, ["search_console:property_123"])
    assert action == "Add or intentionally exclude the discovered property from scope."


def test_generate_recommended_action_cmp():
    action = _generate_recommended_action(Verdict.OBSERVED, ["cmp:onetrust_123"])
    assert action == "Correct the identified purpose/category mapping."


def test_generate_recommended_action_fallback():
    action = _generate_recommended_action(Verdict.OBSERVED, ["unknown:resource"])
    assert action == "Review the identified configuration against applicable compliance requirements."


def test_generate_recommended_action_not_observed():
    # NOT_OBSERVED doesn't technically need an action, but if passed it should return None
    action = _generate_recommended_action(Verdict.NOT_OBSERVED, ["gtm:container"])
    assert action is None
