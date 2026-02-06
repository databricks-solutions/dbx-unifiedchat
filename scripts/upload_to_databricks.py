#!/usr/bin/env python3
"""
Upload Test Files to Databricks Workspace

Simple script to upload test notebook and agent.py to your workspace.
Then you can run the test directly in the Databricks UI.
"""

import os
import sys
import base64
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

try:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import workspace
except ImportError:
    print("❌ Error: databricks-sdk not installed")
    sys.exit(1)

print("\n" + "📤" * 40)
print("UPLOAD FILES TO DATABRICKS")
print("📤" * 40 + "\n")

# Initialize client
print("Connecting to Databricks...")
w = WorkspaceClient()

# Get current user
current_user = w.current_user.me()
username = current_user.user_name
print(f"✅ Connected as: {username}\n")

# Define paths
workspace_dir = f"/Users/{username}/multi_agent_test"
test_notebook_path = f"{workspace_dir}/06_Test_Multi_Agent_System"
agent_py_path = f"{workspace_dir}/agent.py"

# Local files
local_test_notebook = Path("Notebooks/06_Test_Multi_Agent_System.py")
local_agent_py = Path("Notebooks/agent.py")

# Verify local files exist
if not local_test_notebook.exists():
    print(f"❌ Test notebook not found: {local_test_notebook}")
    sys.exit(1)

if not local_agent_py.exists():
    print(f"❌ Agent file not found: {local_agent_py}")
    sys.exit(1)

print("="*80)
print("UPLOADING FILES")
print("="*80)

# Create directory
try:
    w.workspace.mkdirs(workspace_dir)
    print(f"✅ Directory ready: {workspace_dir}")
except Exception as e:
    print(f"⚠️  Directory note: {str(e)}")

# Upload test notebook
print(f"\n1. Uploading test notebook...")
try:
    with open(local_test_notebook, 'rb') as f:
        content = base64.b64encode(f.read()).decode('utf-8')
    
    w.workspace.import_(
        path=test_notebook_path,
        format=workspace.ImportFormat.SOURCE,
        language=workspace.Language.PYTHON,
        content=content,
        overwrite=True
    )
    print(f"   ✅ Uploaded: {test_notebook_path}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")
    sys.exit(1)

# Upload agent.py
print(f"\n2. Uploading agent.py...")
try:
    with open(local_agent_py, 'rb') as f:
        content = base64.b64encode(f.read()).decode('utf-8')
    
    w.workspace.import_(
        path=agent_py_path,
        format=workspace.ImportFormat.SOURCE,
        language=workspace.Language.PYTHON,
        content=content,
        overwrite=True
    )
    print(f"   ✅ Uploaded: {agent_py_path}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")
    sys.exit(1)

# Success!
print("\n" + "="*80)
print("✅ SUCCESS - FILES UPLOADED")
print("="*80)

host = os.getenv('DATABRICKS_HOST', 'your-workspace')
# Clean up protocol from host if present
host = host.replace('https://', '').replace('http://', '')

print(f"""
📁 Files are now in your Databricks workspace:

   Test Notebook: {test_notebook_path}
   Agent File:    {agent_py_path}

🚀 NEXT STEPS - Run the Test:

1. Open Databricks in your browser:
   https://{host}

2. Navigate to Workspace → Users → {username} → multi_agent_test

3. Click on: 06_Test_Multi_Agent_System

4. Click "Run All" button (or run cells one by one)

5. Watch the tests execute and review results!

⏱️  Expected duration: 5-10 minutes

📊 What you'll see:
   - 5 comprehensive tests
   - Real-time agent responses  
   - Performance metrics
   - Final assessment with recommendations

💡 Tips:
   - First cell installs dependencies (takes ~2 min)
   - Each test shows which agents are called
   - Final summary shows success rate
   - If ≥80% pass → Ready for deployment! 🎉

""")

print("="*80)
print("Files successfully uploaded! Follow the steps above to run the test.")
print("="*80 + "\n")

