import logging
import os
import yaml

logger = logging.getLogger(__name__)

# All config keys that build_config_yaml writes.
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
    "source_table",
    "volume_name",
    "uc_function_names",
]


def build_config_yaml(params: dict, path: str = "/tmp/agent_config.yaml") -> str:
    """
    Generate a ModelConfig-compatible YAML from a dict of widget parameters.

    Handles format conversion:
      - genie_space_ids, uc_function_names: comma-separated string -> YAML list
      - lakebase_embedding_dims / sample_size / max_unique_values: string -> int

    Args:
        params: dict of config values (typically from dbutils.widgets)
        path: where to write the temp YAML

    Returns:
        Absolute path to the generated YAML file.
    """
    cfg = dict(params)

    for list_key in ("genie_space_ids", "uc_function_names"):
        if isinstance(cfg.get(list_key), str):
            cfg[list_key] = [s.strip() for s in cfg[list_key].split(",") if s.strip()]

    for int_key in ("lakebase_embedding_dims", "sample_size", "max_unique_values"):
        if int_key in cfg and isinstance(cfg[int_key], str):
            cfg[int_key] = int(cfg[int_key])

    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)

    logger.info(f"Generated config YAML at {path}")
    return path
