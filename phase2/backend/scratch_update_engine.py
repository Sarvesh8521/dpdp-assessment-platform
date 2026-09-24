import re
import sys

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Remove GTMCMPConfigReconciliation class and its reference
    content = re.sub(r'# ── Rule 8: GTM ↔ CMP configuration ──\nclass GTMCMPConfigReconciliation\(ReconciliationRule\):.*?(?=# ── Rule Registry ──)', '', content, flags=re.DOTALL)
    content = re.sub(r'\s*GTMCMPConfigReconciliation\(\),', '', content)
    
    # Change human_review_required=True to False where outcome="insufficient_data"
    # We can do this by finding all ReconciliationFinding instantiations
    
    def repl(m):
        finding_block = m.group(0)
        if 'outcome="insufficient_data"' in finding_block:
            finding_block = finding_block.replace('human_review_required=True', 'human_review_required=False')
        return finding_block

    content = re.sub(r'ReconciliationFinding\([^)]+\)', repl, content)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

if __name__ == "__main__":
    process_file('app/services/reconciliation/engine.py')
