import re
import difflib

def extract_prompts(file_path, is_magic=False):
    with open(file_path, 'r') as f:
        content = f.read()
        
    if is_magic:
        content = re.sub(r'^# MAGIC ', '', content, flags=re.MULTILINE)
        
    # Extract table prompt
    table_pattern = r'class SQLSynthesisTableAgent.*?system_prompt=\(\s*(.*?)\s*\)'
    table_match = re.search(table_pattern, content, re.DOTALL)
    table_prompt = table_match.group(1) if table_match else None
    
    # Extract genie prompt
    genie_pattern = r'class SQLSynthesisGenieAgent.*?system_prompt=\(\s*("""(.*?)""")\s*\)'
    genie_match = re.search(genie_pattern, content, re.DOTALL)
    genie_prompt = genie_match.group(1) if genie_match else None
    
    return table_prompt, genie_prompt

src_table, src_genie = extract_prompts('src/multi_agent/agents/sql_synthesis_agents.py')
orig_table, orig_genie = extract_prompts('Notebooks/archive/Super_Agent_hybrid_original.py', is_magic=True)

if src_table != orig_table:
    print("=== Table Agent Differences ===")
    if src_table and orig_table:
        diff = difflib.unified_diff(orig_table.splitlines(), src_table.splitlines(), lineterm='')
        print('\n'.join(list(diff)[:20]))
    else:
        print("One of them is None", src_table is None, orig_table is None)

if src_genie != orig_genie:
    print("=== Genie Agent Differences ===")
    if src_genie and orig_genie:
        diff = difflib.unified_diff(orig_genie.splitlines(), src_genie.splitlines(), lineterm='')
        print('\n'.join(list(diff)[:20]))
    else:
        print("One of them is None", src_genie is None, orig_genie is None)
