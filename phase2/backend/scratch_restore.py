import json
import os

path = r'C:\Users\sarve\.gemini\antigravity-ide\brain\8b70b0d9-518d-4c7b-a36e-705e31025506\.system_generated\logs\transcript_full.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        if 'Showing lines 1 to 574' in line:
            data = json.loads(line)
            content = data.get('content', '') or str(data)
            
            lines = content.split('\\n')
            if len(lines) < 100:
                lines = content.split('\n')
            
            out_lines = []
            found_start = False
            for l in lines:
                if '1: """Report Generator Service.' in l:
                    found_start = True
                
                if found_start:
                    if ': ' in l and l.split(': ')[0].isdigit():
                        out_lines.append(l.split(': ', 1)[1])
                    else:
                        if len(out_lines) > 500:
                            break
            
            if len(out_lines) > 500:
                with open('app/services/report_generator.py', 'w', encoding='utf-8') as out:
                    out.write('\n'.join(out_lines))
                print('Restored successfully, lines:', len(out_lines))
                break
