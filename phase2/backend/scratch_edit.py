import re
import sys

path = 'app/services/report_generator.py'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Remove synthetic findings block
start_str = '        configured_ints = [normalize_integration_key(i) for i in (assessment.selected_integrations or [])]'
end_str = '        recon_data: list[dict[str, Any]] = []'

if start_str in content and end_str in content:
    start_idx = content.find(start_str)
    end_idx = content.find(end_str)
    content = content[:start_idx + len(start_str)] + '\n\n' + content[end_idx:]
    print("Removed synth block")

# 2. Add is_connected and outcome_msg logic to individual reports
find_str = '            int_review = len([f for f in int_findings if f.get("human_review_required")])'
rep_str = '''            int_review = len([f for f in int_findings if f.get("human_review_required")])

            is_connected = False
            conn_meta = conn_map.get(int_key)
            if conn_meta and conn_meta.get("connected_at"):
                is_connected = True

            if not is_connected:
                outcome_msg = "Integration not connected; no provider evidence collected."
            else:
                outcome_msg = _build_assessment_outcome(int_obs, int_not_obs, int_cannot)'''

if find_str in content:
    content = content.replace(find_str, rep_str)
    print("Added outcome_msg logic")

# 3. Use outcome_msg in the individual report
find_str2 = '"assessment_outcome": _build_assessment_outcome(int_obs, int_not_obs, int_cannot),'
rep_str2 = '"assessment_outcome": outcome_msg,'
if find_str2 in content:
    content = content.replace(find_str2, rep_str2, 1) # Only first match (individual reports)
    print("Updated assessment_outcome in dict")

# 4. Remove synth check from overall stats override calculation
find_str3 = '        actual_findings = [f for f in findings_data if not str(f.get("finding_id", "")).startswith("synth-")]'
rep_str3 = '        actual_findings = findings_data'
if find_str3 in content:
    content = content.replace(find_str3, rep_str3)
    print("Updated actual_findings in overall stats")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print("Done")
