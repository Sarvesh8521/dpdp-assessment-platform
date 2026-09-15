"""Realistic demo fixtures for all 7 integrations + Phase 1 scanner.

Per PHASE2_SPEC Section 19:
- GTM: 20 tags, 5 with consent settings
- GA4: retention, Signals, linked services
- Google Ads: Customer Match list metadata, Enhanced Conversions
- Meta: audience metadata, Advanced Matching
- Search Console: declared + undeclared property
- GitHub: 3 planted issues
- CMP: purpose taxonomy, categories, geolocation
- Phase 1: observed tracker absent from GTM
"""

from datetime import datetime, timezone
from app.domain.evidence import Confidence, EvidenceItem, Verdict

NOW = datetime.now(timezone.utc)

# 1. GTM Fixture: 20 tags, 5 with consent settings, some with user data
GTM_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="S6.consentGating",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="gtm:container:live",
        observed_at=NOW,
        detail="Analyzed 20 tags across 1 container. 5 tags have consent settings configured; 15 tags lack consent gating.",
        raw_metadata={
            "total_tags": 20,
            "tags_with_consent": 5,
            "tags_without_consent": 15,
            "tags": [
                {"name": "Google Analytics 4 Config", "type": "gaawc", "vendor": "google_analytics", "has_consent_settings": True, "sends_user_data": False},
                {"name": "Google Ads Conversion Tracking", "type": "awct", "vendor": "google_ads", "has_consent_settings": True, "sends_user_data": True},
                {"name": "Google Ads Remarketing", "type": "sp", "vendor": "google_ads", "has_consent_settings": True, "sends_user_data": True},
                {"name": "Meta Pixel Base", "type": "html", "vendor": "meta", "has_consent_settings": True, "sends_user_data": True},
                {"name": "Hotjar Tracking Code", "type": "hjid", "vendor": "hotjar", "has_consent_settings": True, "sends_user_data": False},
                # 15 tags WITHOUT consent settings (violations)
                {"name": "LinkedIn Insight Tag", "type": "btt", "vendor": "linkedin", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Twitter Universal Website Tag", "type": "twitter", "vendor": "twitter", "has_consent_settings": False, "sends_user_data": False},
                {"name": "TikTok Pixel", "type": "html", "vendor": "tiktok", "has_consent_settings": False, "sends_user_data": True},
                {"name": "Pinterest Tag", "type": "pinterest", "vendor": "pinterest", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Criteo OneTag", "type": "html", "vendor": "criteo", "has_consent_settings": False, "sends_user_data": True},
                {"name": "Microsoft Advertising UET", "type": "bing", "vendor": "microsoft", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Snap Pixel", "type": "snap", "vendor": "snapchat", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Crazy Egg", "type": "html", "vendor": "crazyegg", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Taboola Pixel", "type": "html", "vendor": "taboola", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Outbrain Pixel", "type": "html", "vendor": "outbrain", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Reddit Pixel", "type": "html", "vendor": "reddit", "has_consent_settings": False, "sends_user_data": False},
                {"name": "Mixpanel Analytics", "type": "html", "vendor": "mixpanel", "has_consent_settings": False, "sends_user_data": True},
                {"name": "HubSpot Tracking", "type": "html", "vendor": "hubspot", "has_consent_settings": False, "sends_user_data": True},
                {"name": "FullStory Session Replay", "type": "html", "vendor": "fullstory", "has_consent_settings": False, "sends_user_data": True},
                {"name": "Intercom Messenger", "type": "html", "vendor": "intercom", "has_consent_settings": False, "sends_user_data": True},
            ],
        },
    ),
    EvidenceItem(
        obligation_id="S6.consentMode",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="gtm:consent_mode",
        observed_at=NOW,
        detail="Consent Mode configured with default states for ad_storage and analytics_storage.",
    ),
    EvidenceItem(
        obligation_id="S9.sharing.enhancedConversions",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="gtm:tags:enhanced_conversions",
        observed_at=NOW,
        detail="Tags configured to send user-provided data for Enhanced Conversions.",
    ),
    EvidenceItem(
        obligation_id="S12.governance.tagInventory",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="gtm:inventory",
        observed_at=NOW,
        detail="Active GTM inventory contains 20 tags across 8 vendor categories.",
    ),
]

# 2. GA4 Fixture: retention, Signals, linked services
GA4_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="S8.retention",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="ga4:dataRetentionSettings",
        observed_at=NOW,
        detail="Event data retention is set to 14 months (maximum allowed in GA4 standard).",
        raw_metadata={"eventDataRetention": "FOURTEEN_MONTHS", "resetUserDataOnNewActivity": True},
    ),
    EvidenceItem(
        obligation_id="S9.sharing.crossDevice",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="ga4:googleSignalsSettings",
        observed_at=NOW,
        detail="Google Signals is ENABLED for cross-device reporting and remarketing audiences.",
        raw_metadata={"googleSignals": "ENABLED"},
    ),
    EvidenceItem(
        obligation_id="S9.sharing.documented",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="ga4:linked_services:google_ads",
        observed_at=NOW,
        detail="GA4 linked to Google Ads account 123-456-7890 and BigQuery export enabled.",
        raw_metadata={"link_type": "google_ads", "ad_account": "123-456-7890"},
    ),
]

# 3. Google Ads Fixture: Customer Match list metadata, Enhanced Conversions
GOOGLE_ADS_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="S9.sharing.audienceUpload",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="google_ads:user_lists:customer_match",
        observed_at=NOW,
        detail="Found 3 Customer Match user lists (hashed email/phone uploads) without documented consent basis.",
        raw_metadata={"customer_match_lists_count": 3, "upload_types": ["EMAIL", "PHONE"]},
    ),
    EvidenceItem(
        obligation_id="S9.sharing.enhancedConversions",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="google_ads:conversion_actions",
        observed_at=NOW,
        detail="Enhanced Conversions enabled on purchase and lead conversion actions.",
    ),
]

# 4. Meta Fixture: Custom Audiences, Advanced Matching
META_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="S9.sharing.advancedMatching",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="meta:pixel:advanced_matching",
        observed_at=NOW,
        detail="Automatic Advanced Matching is ENABLED — transmits hashed customer parameters to Meta.",
        raw_metadata={"advanced_matching_enabled": True},
    ),
    EvidenceItem(
        obligation_id="S9.sharing.audienceUpload",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="meta:custom_audiences",
        observed_at=NOW,
        detail="Custom Audiences created via customer file upload.",
        raw_metadata={"custom_audiences_count": 2},
    ),
]

# 5. Search Console Fixture: Declared vs undeclared properties
SEARCH_CONSOLE_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="S10.scope.properties",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="search_console:sites",
        observed_at=NOW,
        detail="Verified property found: https://example.com/",
        raw_metadata={"siteUrl": "https://example.com/"},
    ),
    EvidenceItem(
        obligation_id="S10.scope.properties",
        verdict=Verdict.NOT_OBSERVED,
        confidence=Confidence.HIGH,
        source="search_console:undeclared",
        observed_at=NOW,
        detail="Undeclared subdomain detected in Search Console: https://staging.example.com/ is indexed.",
        raw_metadata={"siteUrl": "https://staging.example.com/"},
    ),
]

# 6. GitHub Fixture: 3 planted issues (unconsented SDK, plaintext logging, un-gated form)
GITHUB_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="APP.sdk.consent",
        verdict=Verdict.NOT_OBSERVED,
        confidence=Confidence.HIGH,
        source="github:code_scan:sdk_init",
        observed_at=NOW,
        detail="Facebook SDK (com.facebook.android:facebook-android-sdk) initialized in Application.onCreate() before consent check.",
        raw_metadata={"sdks": [{"sdk": "Facebook SDK", "file": "MainApplication.kt", "line": 42}]},
    ),
    EvidenceItem(
        obligation_id="APP.logging.noPII",
        verdict=Verdict.NOT_OBSERVED,
        confidence=Confidence.HIGH,
        source="github:code_scan:logging",
        observed_at=NOW,
        detail="Log.d() call outputs user email address in AuthRepository.kt.",
        raw_metadata={"violation": "Log.d(\"Auth\", \"User email: \" + user.email)", "file": "AuthRepository.kt", "line": 88},
    ),
    EvidenceItem(
        obligation_id="S10.scope.sdks",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="github:dependencies",
        observed_at=NOW,
        detail="Discovered 6 analytics/advertising SDKs in dependencies.",
        raw_metadata={"sdks": [{"sdk": "Google Analytics"}, {"sdk": "Facebook SDK"}, {"sdk": "AppsFlyer"}]},
    ),
]

# 7. CMP Fixture: OneTrust purpose taxonomy, categories, geolocation
CMP_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="CMP.purposes.configured",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="cmp:purposes",
        observed_at=NOW,
        detail="CMP has 4 cookie categories configured: Strictly Necessary, Performance, Functional, Targeting.",
        raw_metadata={"categories": ["Strictly Necessary", "Performance", "Functional", "Targeting"]},
    ),
    EvidenceItem(
        obligation_id="CMP.geolocation.india",
        verdict=Verdict.OBSERVED,
        confidence=Confidence.HIGH,
        source="cmp:geolocation",
        observed_at=NOW,
        detail="Geolocation rule active: India users served opt-in consent banner with granular toggles.",
        raw_metadata={"country": "IN", "rule": "opt-in"},
    ),
]

# 8. Phase 1 Scanner Mock Fixture (Tracker observed on website that is absent from GTM)
PHASE1_FIXTURE_EVIDENCE = [
    EvidenceItem(
        obligation_id="S6.consentGating",
        verdict=Verdict.NOT_OBSERVED,
        confidence=Confidence.HIGH,
        source="phase1_scanner:network_traffic",
        observed_at=NOW,
        detail="Network requests detected to clarity.ms (Microsoft Clarity) before user consent was given.",
        raw_metadata={"tags": [{"vendor": "microsoft_clarity", "type": "clarity"}]},
    )
]

FIXTURES_BY_INTEGRATION = {
    "gtm": GTM_FIXTURE_EVIDENCE,
    "GTM": GTM_FIXTURE_EVIDENCE,
    "ga4": GA4_FIXTURE_EVIDENCE,
    "GA4": GA4_FIXTURE_EVIDENCE,
    "google_ads": GOOGLE_ADS_FIXTURE_EVIDENCE,
    "GoogleAds": GOOGLE_ADS_FIXTURE_EVIDENCE,
    "search_console": SEARCH_CONSOLE_FIXTURE_EVIDENCE,
    "SearchConsole": SEARCH_CONSOLE_FIXTURE_EVIDENCE,
    "meta": META_FIXTURE_EVIDENCE,
    "Meta": META_FIXTURE_EVIDENCE,
    "github": GITHUB_FIXTURE_EVIDENCE,
    "GitHub": GITHUB_FIXTURE_EVIDENCE,
    "cmp": CMP_FIXTURE_EVIDENCE,
    "CMP": CMP_FIXTURE_EVIDENCE,
    "phase1_scanner": PHASE1_FIXTURE_EVIDENCE,
}
