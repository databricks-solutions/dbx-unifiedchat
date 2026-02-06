# Databricks notebook source
# MAGIC %md
# MAGIC # Export Genie Spaces to Volume
# MAGIC 
# MAGIC This notebook exports Genie space metadata (space.json and serialized.json) to Unity Catalog Volume.
# MAGIC These exported files are used by the metadata enrichment pipeline.
# MAGIC 
# MAGIC **Configuration:** Provide Genie space IDs via environment variable `GENIE_SPACE_IDS`

# COMMAND ----------

# MAGIC %md
# MAGIC ## Setup and Configuration

# COMMAND ----------

import os
import re
import json
import pathlib
import requests
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables from .env file (if running locally)
load_dotenv()

# COMMAND ----------

# DBTITLE 1,Setup Parameters

dbutils.widgets.removeAll()

# Databricks connection (use environment variables or widgets)
dbutils.widgets.text("databricks_host", os.getenv("DATABRICKS_HOST", ""), "Databricks Host")
dbutils.widgets.text("databricks_token", os.getenv("DATABRICKS_TOKEN", ""), "Databricks Token")

# Unity Catalog location
dbutils.widgets.text("catalog_name", os.getenv("CATALOG_NAME", "yyang"), "Catalog Name")
dbutils.widgets.text("schema_name", os.getenv("SCHEMA_NAME", "multi_agent_genie"), "Schema Name")
dbutils.widgets.text("volume_name", os.getenv("VOLUME_NAME", "volume"), "Volume Name")

# Genie Space IDs (comma-separated)
default_space_ids = os.getenv(
    "GENIE_SPACE_IDS",
    "01f072dbd668159d99934dfd3b17f544,01f08f4d1f5f172ea825ec8c9a3c6064,01f073c5476313fe8f51966e3ce85bd7,01f07795f6981dc4a99d62c9fc7c2caa,01f08a9fd9ca125a986d01c1a7a5b2fe"
)
dbutils.widgets.text("genie_space_ids", default_space_ids, "Genie Space IDs (comma-separated)")

# Get widget values
HOST = dbutils.widgets.get("databricks_host").rstrip("/")
TOKEN = dbutils.widgets.get("databricks_token")
catalog_name = dbutils.widgets.get("catalog_name")
schema_name = dbutils.widgets.get("schema_name")
volume_name = dbutils.widgets.get("volume_name")
genie_space_ids_str = dbutils.widgets.get("genie_space_ids")

# Parse Genie Space IDs
GENIE_SPACE_IDS = [sid.strip() for sid in genie_space_ids_str.split(",") if sid.strip()]

# Validate required parameters
if not HOST:
    HOST = os.environ.get("DATABRICKS_HOST", "").rstrip("/")
if not TOKEN:
    TOKEN = os.environ.get("DATABRICKS_TOKEN") or os.environ.get("DATABRICKS_PAT")

if not HOST or not TOKEN:
    raise SystemExit(
        "Please provide DATABRICKS_HOST and DATABRICKS_TOKEN via environment variables or widgets"
    )

if not GENIE_SPACE_IDS:
    raise SystemExit("Please provide at least one Genie Space ID via GENIE_SPACE_IDS")

# Setup API headers
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}

print(f"Configuration:")
print(f"  Host: {HOST}")
print(f"  Token: {'*' * 10}...{TOKEN[-4:] if len(TOKEN) > 4 else '****'}")
print(f"  Catalog: {catalog_name}")
print(f"  Schema: {schema_name}")
print(f"  Volume: {volume_name}")
print(f"  Genie Space IDs: {len(GENIE_SPACE_IDS)} spaces")
for i, sid in enumerate(GENIE_SPACE_IDS, 1):
    print(f"    {i}. {sid}")

# COMMAND ----------

# DBTITLE 1,Create Unity Catalog Volume

# Create catalog, schema, and volume if they don't exist
spark.sql(f"CREATE CATALOG IF NOT EXISTS `{catalog_name}`")
print(f"✓ Catalog '{catalog_name}' ready")

spark.sql(f"USE CATALOG `{catalog_name}`")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{schema_name}`")
print(f"✓ Schema '{schema_name}' ready")

spark.sql(f"USE SCHEMA `{schema_name}`")
spark.sql(f"CREATE VOLUME IF NOT EXISTS `{volume_name}`")
print(f"✓ Volume '{volume_name}' ready")

# Create export directory
OUTDIR = pathlib.Path(f"/Volumes/{catalog_name}/{schema_name}/{volume_name}/genie_exports")
OUTDIR.mkdir(exist_ok=True, parents=True)
print(f"\n✓ Export directory ready: {OUTDIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Helper Functions

# COMMAND ----------

def safe_name(s: str) -> str:
    """
    Convert a string to a safe filename.
    
    Args:
        s: Input string (e.g., space title)
        
    Returns:
        Safe filename string
    """
    s = s.strip() or "untitled"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)[:150]


def get_space_info(space_id: str) -> Optional[Dict]:
    """
    Get basic information about a Genie space.
    
    Args:
        space_id: Genie space ID
        
    Returns:
        Dictionary with space info or None if not found
    """
    try:
        url = f"{HOST}/api/2.0/genie/spaces/{space_id}"
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        if resp.status_code == 404:
            print(f"  ✗ Space not found: {space_id}")
            return None
        elif resp.status_code == 403:
            print(f"  ✗ Access denied: {space_id}")
            return None
        else:
            print(f"  ✗ Error fetching space {space_id}: {e}")
            return None
    except Exception as e:
        print(f"  ✗ Unexpected error for space {space_id}: {e}")
        return None


def export_space(space_id: str, title_hint: str = "") -> Optional[str]:
    """
    Export a Genie space to JSON files.
    
    Args:
        space_id: Genie space ID
        title_hint: Space title (if known)
        
    Returns:
        Base filename if successful, None if failed
    """
    print(f"\nExporting space: {space_id}")
    
    # Fetch full space data with serialized_space
    url = f"{HOST}/api/2.0/genie/spaces/{space_id}"
    try:
        resp = requests.get(
            url, 
            headers=HEADERS, 
            params={"include_serialized_space": "true"}, 
            timeout=120
        )
        resp.raise_for_status()
    except requests.HTTPError as e:
        if resp.status_code == 403:
            print(f"  ✗ 403 Forbidden - Access denied")
            return None
        elif resp.status_code == 404:
            print(f"  ✗ 404 Not Found - Space does not exist")
            return None
        else:
            print(f"  ✗ HTTP Error: {e}")
            return None
    except Exception as e:
        print(f"  ✗ Request failed: {e}")
        return None
    
    obj = resp.json()
    
    # Get space title
    title = obj.get("title") or title_hint or space_id
    print(f"  Title: {title}")
    
    # Create base filename
    base = f"{space_id}__{safe_name(title)}"
    
    # Save main space.json
    space_json_path = OUTDIR / f"{base}.space.json"
    space_json_path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    print(f"  ✓ Saved: {space_json_path.name}")
    
    # Save serialized_space if available
    serialized = obj.get("serialized_space")
    if serialized:
        try:
            # Try to parse as JSON
            ser_obj = json.loads(serialized)
            ser_path = OUTDIR / f"{base}.serialized.json"
            ser_path.write_text(json.dumps(ser_obj, indent=2), encoding="utf-8")
            print(f"  ✓ Saved: {ser_path.name}")
        except json.JSONDecodeError:
            # Fallback: save raw string
            ser_path = OUTDIR / f"{base}.serialized.txt"
            ser_path.write_text(serialized, encoding="utf-8")
            print(f"  ⚠ Saved raw serialized: {ser_path.name}")
    else:
        print(f"  ⚠ No serialized_space data")
    
    return base


def list_all_spaces() -> List[Dict]:
    """
    List all accessible Genie spaces.
    
    Returns:
        List of space dictionaries
    """
    spaces = []
    params = {}
    
    print("Fetching list of all spaces...")
    
    while True:
        try:
            resp = requests.get(
                f"{HOST}/api/2.0/genie/spaces", 
                headers=HEADERS, 
                params=params, 
                timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            
            spaces.extend(data.get("spaces", []))
            
            token = data.get("next_page_token") or data.get("page_token") or None
            if not token:
                break
            params = {"page_token": token}
            
        except Exception as e:
            print(f"Error fetching spaces: {e}")
            break
    
    return spaces

# COMMAND ----------

# MAGIC %md
# MAGIC ## Export Specified Genie Spaces

# COMMAND ----------

print("=" * 80)
print("EXPORTING GENIE SPACES")
print("=" * 80)

exported_spaces = []
failed_spaces = []

for i, space_id in enumerate(GENIE_SPACE_IDS, 1):
    print(f"\n[{i}/{len(GENIE_SPACE_IDS)}] Processing space: {space_id}")
    print("-" * 80)
    
    try:
        # Get space info first
        space_info = get_space_info(space_id)
        if not space_info:
            failed_spaces.append(space_id)
            continue
        
        title = space_info.get("title", "")
        
        # Export the space
        base_name = export_space(space_id, title)
        
        if base_name:
            exported_spaces.append({
                "space_id": space_id,
                "title": title,
                "base_name": base_name
            })
            print(f"  ✓ Successfully exported")
        else:
            failed_spaces.append(space_id)
            print(f"  ✗ Export failed")
            
    except Exception as e:
        print(f"  ✗ Unexpected error: {e}")
        failed_spaces.append(space_id)
        import traceback
        traceback.print_exc()

# COMMAND ----------

# MAGIC %md
# MAGIC ## Export Summary

# COMMAND ----------

print("\n" + "=" * 80)
print("EXPORT SUMMARY")
print("=" * 80)

print(f"\nTotal spaces requested: {len(GENIE_SPACE_IDS)}")
print(f"Successfully exported: {len(exported_spaces)}")
print(f"Failed to export: {len(failed_spaces)}")

if exported_spaces:
    print("\n✓ Successfully Exported Spaces:")
    print("-" * 80)
    for i, space in enumerate(exported_spaces, 1):
        print(f"{i}. {space['title']}")
        print(f"   ID: {space['space_id']}")
        print(f"   Files: {space['base_name']}.space.json")
        print(f"          {space['base_name']}.serialized.json")
        print()

if failed_spaces:
    print("\n✗ Failed Spaces:")
    print("-" * 80)
    for i, space_id in enumerate(failed_spaces, 1):
        print(f"{i}. {space_id}")

print("\n" + "=" * 80)
print(f"Export location: {OUTDIR}")
print("=" * 80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## List Exported Files

# COMMAND ----------

import glob

# List all exported files
space_files = list(OUTDIR.glob("*.space.json"))
serialized_files = list(OUTDIR.glob("*.serialized.json"))

print(f"\nExported Files in {OUTDIR}:")
print("=" * 80)
print(f"\nSpace JSON files: {len(space_files)}")
for f in sorted(space_files):
    size_kb = f.stat().st_size / 1024
    print(f"  - {f.name:60s} ({size_kb:.1f} KB)")

print(f"\nSerialized JSON files: {len(serialized_files)}")
for f in sorted(serialized_files):
    size_kb = f.stat().st_size / 1024
    print(f"  - {f.name:60s} ({size_kb:.1f} KB)")

print("\n" + "=" * 80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Optional: Export All Accessible Spaces

# COMMAND ----------

# DBTITLE 1,Export All Spaces (Commented Out - Run if needed)

# Uncomment to export ALL accessible Genie spaces instead of specified ones

# print("=" * 80)
# print("EXPORTING ALL ACCESSIBLE GENIE SPACES")
# print("=" * 80)
# 
# all_spaces = list_all_spaces()
# print(f"\nFound {len(all_spaces)} accessible spaces")
# 
# all_exported = []
# all_failed = []
# 
# for i, space in enumerate(all_spaces, 1):
#     space_id = space.get("space_id") or space.get("id")
#     title = space.get("title", "")
#     
#     if not space_id:
#         print(f"\n[{i}/{len(all_spaces)}] Skipping - no space_id")
#         continue
#     
#     print(f"\n[{i}/{len(all_spaces)}] Processing: {title or space_id}")
#     print("-" * 80)
#     
#     try:
#         base_name = export_space(space_id, title)
#         if base_name:
#             all_exported.append({"space_id": space_id, "title": title})
#             print(f"  ✓ Successfully exported")
#         else:
#             all_failed.append(space_id)
#             print(f"  ✗ Export failed")
#     except Exception as e:
#         print(f"  ✗ Error: {e}")
#         all_failed.append(space_id)
# 
# print(f"\n" + "=" * 80)
# print(f"Exported: {len(all_exported)}/{len(all_spaces)} spaces")
# print(f"Failed: {len(all_failed)} spaces")
# print("=" * 80)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Next Steps
# MAGIC 
# MAGIC ✓ Genie spaces exported to: `/Volumes/{catalog}/{schema}/{volume}/genie_exports/`
# MAGIC 
# MAGIC **Next:** Run `02_Table_MetaInfo_Enrichment.py` to enrich these exports with table metadata

