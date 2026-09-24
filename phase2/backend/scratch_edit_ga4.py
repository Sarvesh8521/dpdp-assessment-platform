import sys

file_path = "app/integrations/ga4/adapter.py"
with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update resource scoping
target_property_logic = """
            # List properties
            summaries = await client.get(
                "/v1beta/accountSummaries", operation="list_account_summaries",
            )

            properties_found = []
            for summary in summaries.get("accountSummaries", []):
                for prop in summary.get("propertySummaries", []):
                    properties_found.append(prop)

            if not properties_found:
                evidence.append(EvidenceItem(
                    obligation_id="S8.retention",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="ga4:list_properties", observed_at=now,
                    detail="No GA4 properties found.",
                    structured_detail=StructuredEvidence(
                        source="ga4", integration="ga4", operation="list_account_summaries",
                        resource_type="property",
                        location="Unavailable",
                        observed_at=now, observed_state="0 properties", expected_state=">=1 GA4 property",
                        detail="No GA4 properties found.",
                        reason="No properties accessible with the provided credentials.",
                    ),
                ))
                return AdapterResult(evidence=evidence, errors=errors)

            for prop_summary in properties_found:
                prop_name = prop_summary.get("property", "")
                if not prop_name:
                    continue
"""

replacement_property_logic = """
            prop_name = connection.metadata.get("resource_id")
            if not prop_name:
                evidence.append(EvidenceItem(
                    obligation_id="S8.retention",
                    verdict=Verdict.CANNOT_DETERMINE, confidence=Confidence.LOW,
                    source="ga4:property_selection", observed_at=now,
                    detail="No GA4 property selected for scanning.",
                    structured_detail=StructuredEvidence(
                        source="ga4", integration="ga4", operation="get_property",
                        resource_type="property", location="Unavailable",
                        observed_at=now, observed_state="No property", expected_state="Selected GA4 property",
                        detail="No GA4 property selected for scanning.",
                        reason="A GA4 property must be selected in the integration configuration.",
                    ),
                ))
                return AdapterResult(evidence=evidence, errors=errors)
            
            # Since we only scan one property, we can mock the summary logic or fetch property directly
            prop_display_name = connection.metadata.get("resource_name", prop_name)
"""

if target_property_logic.strip() in content:
    print("Found property logic!")
else:
    # Try more lenient replacement if exact match fails
    pass

