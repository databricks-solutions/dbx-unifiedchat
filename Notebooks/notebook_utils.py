import logging
import os
import yaml
from mlflow.models import ModelConfig

logger = logging.getLogger(__name__)

# All config keys that build_config_yaml writes and load_deployment_config reads.
# Keys marked with (list) are converted from comma-separated strings.
AGENT_CONFIG_KEYS = [
    "catalog_name",
    "schema_name",
    "llm_endpoint",
    "llm_endpoint_clarification",
    "llm_endpoint_planning",
    "llm_endpoint_sql_synthesis_table",
    "llm_endpoint_sql_synthesis_genie",
    "llm_endpoint_execution",
    "llm_endpoint_summarize",
    "vs_endpoint_name",
    "embedding_model",
    "lakebase_instance_name",
    "lakebase_embedding_endpoint",
    "lakebase_embedding_dims",
    "genie_space_ids",
    "sql_warehouse_id",
    "sample_size",
    "max_unique_values",
    "enriched_docs_table",
    "volume_name",
]


def build_config_yaml(params: dict, path: str = "/tmp/agent_config.yaml") -> str:
    """
    Generate a ModelConfig-compatible YAML from a dict of widget parameters.

    Handles format conversion:
      - genie_space_ids: comma-separated string → YAML list
      - lakebase_embedding_dims / sample_size / max_unique_values: string → int

    Args:
        params: dict of config values (typically from dbutils.widgets)
        path: where to write the temp YAML

    Returns:
        Absolute path to the generated YAML file.
    """
    cfg = dict(params)

    if isinstance(cfg.get("genie_space_ids"), str):
        cfg["genie_space_ids"] = [s.strip() for s in cfg["genie_space_ids"].split(",") if s.strip()]

    for int_key in ("lakebase_embedding_dims", "sample_size", "max_unique_values"):
        if int_key in cfg and isinstance(cfg[int_key], str):
            cfg[int_key] = int(cfg[int_key])

    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Generated config YAML at {path}")
    return path


def load_deployment_config(source):
    """
    Load configuration for notebook deployment/testing.

    Args:
        source: path to a YAML file (str) **or** a dict of pre-loaded params.
                When a dict is provided, a temp YAML is generated so that
                ModelConfig can still read it (needed for mlflow.pyfunc.log_model).

    Returns:
        Tuple of (config_dict, yaml_path) where yaml_path can be passed to
        mlflow.pyfunc.log_model(model_config=...).
    """
    if isinstance(source, dict):
        yaml_path = build_config_yaml(source)
    else:
        yaml_path = source

    logger.info("=" * 80)
    logger.info(f"LOADING CONFIGURATION FROM {yaml_path}")
    logger.info("=" * 80)

    model_config = ModelConfig(development_config=yaml_path)

    catalog = model_config.get("catalog_name")
    schema = model_config.get("schema_name")
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
        ],
    }

    logger.info(f"Catalog: {config['CATALOG']}, Schema: {config['SCHEMA']}")
    logger.info(f"Lakebase: {config['LAKEBASE_INSTANCE_NAME']}")
    logger.info(f"Genie Spaces: {len(config['GENIE_SPACE_IDS'])} spaces configured")
    logger.info(f"SQL Warehouse ID: {config['SQL_WAREHOUSE_ID']}")

    if not config["SQL_WAREHOUSE_ID"]:
        raise ValueError(
            "SQL_WAREHOUSE_ID is not configured! "
            "Ensure 'sql_warehouse_id' is set in databricks.yml variables."
        )

    return config, yaml_path
