import logging
import os
from mlflow.models import ModelConfig

logger = logging.getLogger(__name__)

def load_deployment_config(config_file: str = "../prod_config.yaml"):
    """
    Load configuration for notebook deployment/testing.
    
    Args:
        config_file: Path to the YAML configuration file
        
    Returns:
        Dictionary containing extracted configuration values
    """
    logger.info("="*80)
    logger.info(f"LOADING CONFIGURATION FROM {config_file}")
    logger.info("="*80)
    
    # Initialize ModelConfig
    model_config = ModelConfig(development_config=config_file)
    
    # Extract configuration values
    catalog = model_config.get("catalog_name")
    schema = model_config.get("schema_name")
    
    # LLM Endpoints - Diversified by Agent Role
    default_endpoint = model_config.get("llm_endpoint")
    
    config = {
        "CATALOG": catalog,
        "SCHEMA": schema,
        "TABLE_NAME": f"{catalog}.{schema}.enriched_genie_docs_chunks",
        "VECTOR_SEARCH_INDEX": f"{catalog}.{schema}.enriched_genie_docs_chunks_vs_index",
        
        "LLM_ENDPOINT_CLARIFICATION": model_config.get("llm_endpoint_clarification") or default_endpoint,
        "LLM_ENDPOINT_PLANNING": model_config.get("llm_endpoint_planning") or default_endpoint,
        "LLM_ENDPOINT_SQL_SYNTHESIS_TABLE": model_config.get("llm_endpoint_sql_synthesis_table") or default_endpoint,
        "LLM_ENDPOINT_SQL_SYNTHESIS_GENIE": model_config.get("llm_endpoint_sql_synthesis_genie") or default_endpoint,
        "LLM_ENDPOINT_EXECUTION": model_config.get("llm_endpoint_execution") or default_endpoint,
        "LLM_ENDPOINT_SUMMARIZE": model_config.get("llm_endpoint_summarize") or default_endpoint,
        
        "LAKEBASE_INSTANCE_NAME": model_config.get("lakebase_instance_name"),
        "EMBEDDING_ENDPOINT": model_config.get("lakebase_embedding_endpoint"),
        "EMBEDDING_DIMS": model_config.get("lakebase_embedding_dims"),
        
        "GENIE_SPACE_IDS": model_config.get("genie_space_ids"),
        "SQL_WAREHOUSE_ID": model_config.get("sql_warehouse_id"),
        
        "UC_FUNCTION_NAMES": [
            f"{catalog}.{schema}.get_space_summary",
            f"{catalog}.{schema}.get_table_overview",
            f"{catalog}.{schema}.get_column_detail",
            f"{catalog}.{schema}.get_space_instructions",
            f"{catalog}.{schema}.get_space_details",
        ]
    }
    
    logger.info(f"Catalog: {config['CATALOG']}, Schema: {config['SCHEMA']}")
    logger.info(f"Lakebase: {config['LAKEBASE_INSTANCE_NAME']}")
    logger.info(f"Genie Spaces: {len(config['GENIE_SPACE_IDS'])} spaces configured")
    logger.info(f"SQL Warehouse ID: {config['SQL_WAREHOUSE_ID']}")
    
    if not config["SQL_WAREHOUSE_ID"]:
        error_msg = (
            "SQL_WAREHOUSE_ID is not configured! "
            f"Ensure 'sql_warehouse_id' is set in {config_file}."
        )
        logger.error(error_msg)
        raise ValueError(error_msg)
        
    return config
