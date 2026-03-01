#!/usr/bin/env python3
"""
Run Agent Test on Databricks Workspace

This script:
1. Uploads the test notebook to Databricks
2. Creates a job to run it
3. Monitors execution
4. Retrieves and displays results
"""

import os
import sys
import time
import json
import base64
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

try:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import jobs, compute, workspace
except ImportError:
    print("❌ Error: databricks-sdk not installed")
    print("Run: pip install databricks-sdk")
    sys.exit(1)

print("\n" + "🚀" * 40)
print("DATABRICKS AGENT TEST RUNNER")
print("🚀" * 40 + "\n")

# Initialize client
print("Initializing Databricks workspace client...")
w = WorkspaceClient()

# Get current user
current_user = w.current_user.me()
username = current_user.user_name
print(f"✅ Connected as: {username}\n")

# Define paths
workspace_dir = f"/Users/{username}/multi_agent_test"
test_notebook_name = "06_Test_Multi_Agent_System"
test_notebook_path = f"{workspace_dir}/{test_notebook_name}"
agent_py_path = f"{workspace_dir}/agent.py"

# Local files
local_test_notebook = Path("Notebooks/06_Test_Multi_Agent_System.py")
local_agent_py = Path("Notebooks/agent.py")

print("="*80)
print("STEP 1: Upload Files to Workspace")
print("="*80)

# Create workspace directory if it doesn't exist
try:
    w.workspace.mkdirs(workspace_dir)
    print(f"✅ Workspace directory ready: {workspace_dir}")
except Exception as e:
    print(f"⚠️  Directory may already exist: {str(e)}")

# Upload test notebook
print(f"\nUploading test notebook...")
try:
    with open(local_test_notebook, 'rb') as f:
        notebook_content = base64.b64encode(f.read()).decode('utf-8')
    
    w.workspace.import_(
        path=test_notebook_path,
        format=workspace.ImportFormat.SOURCE,
        language=workspace.Language.PYTHON,
        content=notebook_content,
        overwrite=True
    )
    print(f"✅ Uploaded: {test_notebook_path}")
except Exception as e:
    print(f"❌ Failed to upload test notebook: {str(e)}")
    sys.exit(1)

# Upload agent.py
print(f"Uploading agent.py...")
try:
    with open(local_agent_py, 'rb') as f:
        agent_content = base64.b64encode(f.read()).decode('utf-8')
    
    w.workspace.import_(
        path=agent_py_path,
        format=workspace.ImportFormat.SOURCE,
        language=workspace.Language.PYTHON,
        content=agent_content,
        overwrite=True
    )
    print(f"✅ Uploaded: {agent_py_path}")
except Exception as e:
    print(f"❌ Failed to upload agent.py: {str(e)}")
    sys.exit(1)

print("\n" + "="*80)
print("STEP 2: Create and Run Job")
print("="*80)

# Get available clusters
print("\nLooking for available clusters...")
clusters = list(w.clusters.list())

if not clusters:
    print("⚠️  No clusters found. Creating a new cluster may take 5-10 minutes.")
    print("   Recommendation: Create a cluster in Databricks UI first, then rerun this script.")
    sys.exit(1)

# Use the first available cluster
cluster = clusters[0]
cluster_id = cluster.cluster_id
print(f"✅ Using cluster: {cluster.cluster_name} ({cluster_id})")

# Create job configuration
job_name = f"agent_test_{int(time.time())}"

print(f"\nCreating job: {job_name}...")

try:
    # Create job
    job = w.jobs.create(
        name=job_name,
        tasks=[
            jobs.Task(
                task_key="test_agent",
                description="Test Multi-Agent System",
                existing_cluster_id=cluster_id,
                notebook_task=jobs.NotebookTask(
                    notebook_path=test_notebook_path,
                    source=jobs.Source.WORKSPACE
                ),
                timeout_seconds=1800,  # 30 minutes
            )
        ],
    )
    
    job_id = job.job_id
    print(f"✅ Job created: {job_id}")
    
except Exception as e:
    print(f"❌ Failed to create job: {str(e)}")
    sys.exit(1)

# Run job
print(f"\nStarting job execution...")
try:
    run = w.jobs.run_now(job_id=job_id)
    run_id = run.run_id
    print(f"✅ Job started: Run ID {run_id}")
    print(f"   View in UI: https://{os.getenv('DATABRICKS_HOST', 'your-workspace')}/jobs/{job_id}/runs/{run_id}")
    
except Exception as e:
    print(f"❌ Failed to start job: {str(e)}")
    sys.exit(1)

print("\n" + "="*80)
print("STEP 3: Monitor Execution")
print("="*80)

print("\nMonitoring job execution (this may take 5-10 minutes)...")
print("⏳ Status updates every 30 seconds...\n")

start_time = time.time()
last_state = None

while True:
    try:
        run_status = w.jobs.get_run(run_id=run_id)
        current_state = run_status.state.life_cycle_state
        
        # Print status update if changed
        if current_state != last_state:
            elapsed = time.time() - start_time
            print(f"[{int(elapsed)}s] Status: {current_state}")
            last_state = current_state
        
        # Check if completed
        if current_state in ["TERMINATED", "SKIPPED", "INTERNAL_ERROR"]:
            result_state = run_status.state.result_state
            print(f"\n✅ Job completed: {result_state}")
            
            if result_state == "SUCCESS":
                print("🎉 Test execution successful!")
            else:
                print(f"⚠️  Job finished with state: {result_state}")
                if run_status.state.state_message:
                    print(f"   Message: {run_status.state.state_message}")
            
            break
        
        # Wait before next check
        time.sleep(30)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Monitoring interrupted by user")
        print(f"   Job is still running. Check status in UI:")
        print(f"   https://{os.getenv('DATABRICKS_HOST', 'your-workspace')}/jobs/{job_id}/runs/{run_id}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error checking status: {str(e)}")
        time.sleep(30)

print("\n" + "="*80)
print("STEP 4: Retrieve Results")
print("="*80)

# Try to get output from notebook
print("\nRetrieving notebook output...")
try:
    # Get run output
    output = w.jobs.get_run_output(run_id=run_id)
    
    if output.notebook_output:
        print("\n📊 Notebook Output:")
        print("-"*80)
        
        if output.notebook_output.result:
            print(output.notebook_output.result)
        
        if output.notebook_output.truncated:
            print("\n⚠️  Output truncated. View full results in Databricks UI.")
    else:
        print("⚠️  No output available. Results may be in notebook cells.")
        print(f"   View results in UI: {test_notebook_path}")
    
except Exception as e:
    print(f"⚠️  Could not retrieve output: {str(e)}")
    print(f"   View results in Databricks UI: {test_notebook_path}")

# Get run metadata
print("\n📈 Run Metadata:")
print("-"*80)
print(f"Run ID: {run_id}")
print(f"Job ID: {job_id}")
print(f"Duration: {time.time() - start_time:.2f}s")
print(f"Status: {run_status.state.result_state}")

# Cleanup option
print("\n" + "="*80)
print("CLEANUP")
print("="*80)

cleanup = input("\nDelete temporary job? (y/n): ").lower().strip()
if cleanup == 'y':
    try:
        w.jobs.delete(job_id=job_id)
        print(f"✅ Deleted job: {job_id}")
    except Exception as e:
        print(f"⚠️  Could not delete job: {str(e)}")
else:
    print(f"   Job kept: {job_id}")
    print(f"   Delete manually in UI if needed")

print("\n" + "="*80)
print("SUMMARY")
print("="*80)

print(f"""
✅ Test notebook uploaded: {test_notebook_path}
✅ Agent file uploaded: {agent_py_path}
✅ Job executed: Run ID {run_id}
✅ Results available in workspace

Next Steps:
1. Open the test notebook in Databricks UI
2. Review the test results
3. Check the summary at the end
4. Proceed based on success rate:
   - ≥80%: Ready for deployment!
   - 60-79%: Review and fix issues
   - <60%: Troubleshoot problems

View Results:
{test_notebook_path}
""")

print("="*80 + "\n")

