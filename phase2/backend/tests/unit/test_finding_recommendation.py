import pytest
from app.services.finding_service import _generate_recommended_action
from app.db.models.evidence import Verdict


def test_generate_recommended_action_cannot_determine():
    action = _generate_recommended_action(Verdict.CANNOT_DETERMINE, ["gtm:container_123"])
    assert "Provide the necessary client authorization" in action


def test_generate_recommended_action_gtm():
    action = _generate_recommended_action(Verdict.OBSERVED, ["gtm:container_123"])
    assert action == "Configure the identified tag with the required consent settings."


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
    action = _generate_recommended_action(Verdict.OBSERVED, ["github:repo_123"])
    assert action == "Gate SDK initialization behind the consent decision."


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
