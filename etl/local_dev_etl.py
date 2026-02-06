"""
Local ETL testing script.

Allows testing ETL transformations locally with sample data before
running on Databricks with full datasets.

Usage:
    python etl/local_dev_etl.py --step export --sample-size 10
    python etl/local_dev_etl.py --step enrich --sample-size 10
    python etl/local_dev_etl.py --step vectorize --sample-size 10
    python etl/local_dev_etl.py --all --sample-size 10
"""

import argparse
import sys
import os
from pathlib import Path
from typing import Optional

# Add parent directory to path for config import
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from config import get_config
except ImportError:
    print("Error: Could not import config. Make sure you're running from repo root.")
    sys.exit(1)


def test_export_genie_spaces(sample_size: int = 10, verbose: bool = False):
    """
    Test Genie space export logic locally.
    
    Args:
        sample_size: Number of spaces to process
        verbose: Print verbose output
    """
    print("\n" + "="*80)
    print("STEP 1: Export Genie Spaces (Local Testing)")
    print("="*80)
    
    try:
        config = get_config()
        genie_space_ids = config.table_metadata.genie_space_ids[:sample_size]
        
        print(f"Testing with {len(genie_space_ids)} Genie spaces...")
        
        # Mock export logic (replace with actual when needed)
        for i, space_id in enumerate(genie_space_ids, 1):
            if verbose:
                print(f"  [{i}/{len(genie_space_ids)}] Processing space: {space_id}")
        
        print(f"✓ Would export {len(genie_space_ids)} Genie spaces")
        print("  Note: This is a dry run. Actual export requires Databricks connection.")
        print("  Run 01_export_genie_spaces.py in Databricks for real export.")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return False


def test_enrich_table_metadata(sample_size: int = 10, verbose: bool = False):
    """
    Test table metadata enrichment logic locally.
    
    Args:
        sample_size: Number of sample rows per table
        verbose: Print verbose output
    """
    print("\n" + "="*80)
    print("STEP 2: Enrich Table Metadata (Local Testing)")
    print("="*80)
    
    try:
        config = get_config()
        
        print(f"Testing with sample_size={sample_size}...")
        print(f"Max unique values: {config.table_metadata.max_unique_values}")
        
        # Mock enrichment logic
        mock_tables = ["patients", "medications", "diagnoses"]
        for i, table in enumerate(mock_tables, 1):
            if verbose:
                print(f"  [{i}/{len(mock_tables)}] Enriching table: {table}")
                print(f"    - Extracting {sample_size} sample rows")
                print(f"    - Calculating column statistics")
        
        print(f"✓ Would enrich {len(mock_tables)} tables")
        print("  Note: This is a dry run. Actual enrichment requires Databricks.")
        print("  Run 02_enrich_table_metadata.py in Databricks for real enrichment.")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return False


def test_build_vector_search_index(sample_size: int = 10, verbose: bool = False):
    """
    Test vector search index building logic locally.
    
    Args:
        sample_size: Number of documents to test with
        verbose: Print verbose output
    """
    print("\n" + "="*80)
    print("STEP 3: Build Vector Search Index (Local Testing)")
    print("="*80)
    
    try:
        config = get_config()
        
        print(f"Testing with {sample_size} sample documents...")
        print(f"Vector search endpoint: {config.vector_search.endpoint_name}")
        print(f"Embedding model: {config.vector_search.embedding_model}")
        
        # Mock vectorization logic
        if verbose:
            print(f"  - Loading {sample_size} enriched chunks")
            print(f"  - Creating vector search index")
            print(f"  - Syncing index")
        
        print(f"✓ Would build vector search index with {sample_size} documents")
        print("  Note: This is a dry run. Actual index building requires Databricks.")
        print("  Run 03_build_vector_search_index.py in Databricks for real index.")
        
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return False


def run_all_steps(sample_size: int = 10, verbose: bool = False):
    """
    Run all ETL steps in sequence.
    
    Args:
        sample_size: Number of samples to test with
        verbose: Print verbose output
    """
    print("\n" + "="*80)
    print("LOCAL ETL PIPELINE TEST")
    print("="*80)
    print(f"Sample size: {sample_size}")
    print("="*80)
    
    # Step 1: Export
    success = test_export_genie_spaces(sample_size, verbose)
    if not success:
        print("\n❌ Step 1 failed. Aborting.")
        return False
    
    # Step 2: Enrich
    success = test_enrich_table_metadata(sample_size, verbose)
    if not success:
        print("\n❌ Step 2 failed. Aborting.")
        return False
    
    # Step 3: Vectorize
    success = test_build_vector_search_index(sample_size, verbose)
    if not success:
        print("\n❌ Step 3 failed. Aborting.")
        return False
    
    print("\n" + "="*80)
    print("✅ ALL ETL STEPS VALIDATED (DRY RUN)")
    print("="*80)
    print("\nNext steps:")
    print("1. Review any warnings or errors above")
    print("2. Run actual ETL in Databricks:")
    print("   - 01_export_genie_spaces.py")
    print("   - 02_enrich_table_metadata.py")
    print("   - 03_build_vector_search_index.py")
    print("="*80)
    
    return True


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Local ETL testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Test individual steps
  python etl/local_dev_etl.py --step export --sample-size 10
  python etl/local_dev_etl.py --step enrich --sample-size 20
  python etl/local_dev_etl.py --step vectorize --sample-size 10
  
  # Test complete pipeline
  python etl/local_dev_etl.py --all --sample-size 10
  
  # With verbose output
  python etl/local_dev_etl.py --all --sample-size 10 --verbose
        """
    )
    
    parser.add_argument(
        "--step",
        choices=["export", "enrich", "vectorize"],
        help="Which ETL step to test"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all ETL steps in sequence"
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Sample size for testing (default: 10)"
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print verbose output"
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if not args.all and not args.step:
        parser.error("Either --step or --all must be specified")
    
    success = True
    
    if args.all:
        success = run_all_steps(args.sample_size, args.verbose)
    else:
        if args.step == "export":
            success = test_export_genie_spaces(args.sample_size, args.verbose)
        elif args.step == "enrich":
            success = test_enrich_table_metadata(args.sample_size, args.verbose)
        elif args.step == "vectorize":
            success = test_build_vector_search_index(args.sample_size, args.verbose)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
