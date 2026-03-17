"""
Configuration file for Multi-Agent Genie System

This module provides centralized configuration management for the entire
multi-agent system, including environment variables, defaults, and validation.

Two loading paths:
  - Databricks (notebooks + serving): YAML → ModelConfig → from_model_config()
  - Local dev: .env → load_dotenv() → from_env()
"""

import os
from typing import Optional, Any
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file (local dev)
load_dotenv()


def _mc_get(mc: Any, key: str, default: Any = None) -> Any:
    """Safe get from ModelConfig — returns default if key missing or None."""
    try:
        val = mc.get(key)
        return val if val is not None else default
    except Exception:
        return default


@dataclass
class DatabricksConfig:
    """Databricks workspace configuration."""
    host: str
    token: str
    
    @classmethod
    def from_env(cls) -> 'DatabricksConfig':
        return cls(
            host=os.getenv("DATABRICKS_HOST", "").rstrip("/"),
            token=os.getenv("DATABRICKS_TOKEN", ""),
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'DatabricksConfig':
        return cls(
            host=os.getenv("DATABRICKS_HOST", "").rstrip("/"),
            token=os.getenv("DATABRICKS_TOKEN", ""),
        )


_DEFAULT_UC_FUNCTIONS = "get_space_summary,get_table_overview,get_column_detail,get_space_instructions,get_space_details"


def _parse_csv(val: Any, default: str = "") -> list[str]:
    """Parse a comma-separated string or list into a list of stripped strings."""
    if isinstance(val, list):
        return [s.strip() for s in val if str(s).strip()]
    s = str(val) if val else default
    return [x.strip() for x in s.split(",") if x.strip()]


@dataclass
class UnityCatalogConfig:
    """Unity Catalog configuration."""
    catalog_name: str
    schema_name: str
    uc_function_names: list[str]
    
    @classmethod
    def from_env(cls) -> 'UnityCatalogConfig':
        return cls(
            catalog_name=os.getenv("CATALOG_NAME", "yyang"),
            schema_name=os.getenv("SCHEMA_NAME", "multi_agent_genie"),
            uc_function_names=_parse_csv(os.getenv("UC_FUNCTION_NAMES"), _DEFAULT_UC_FUNCTIONS),
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'UnityCatalogConfig':
        return cls(
            catalog_name=_mc_get(mc, "catalog_name", "yyang"),
            schema_name=_mc_get(mc, "schema_name", "multi_agent_genie"),
            uc_function_names=_parse_csv(_mc_get(mc, "uc_function_names"), _DEFAULT_UC_FUNCTIONS),
        )
    
    @property
    def full_schema_name(self) -> str:
        """Get fully qualified schema name."""
        return f"{self.catalog_name}.{self.schema_name}"

    @property
    def uc_function_names_fq(self) -> list[str]:
        """Fully qualified UC function names."""
        return [f"{self.full_schema_name}.{fn}" for fn in self.uc_function_names]


_DEFAULT_LLM = "databricks-claude-sonnet-4-5"


@dataclass
class LLMConfig:
    """LLM endpoint configuration with agent-specific endpoints."""
    endpoint_name: str
    clarification_endpoint: str
    planning_endpoint: str
    sql_synthesis_table_endpoint: str
    sql_synthesis_genie_endpoint: str
    execution_endpoint: str
    summarize_endpoint: str
    
    @classmethod
    def from_env(cls) -> 'LLMConfig':
        d = os.getenv("LLM_ENDPOINT", _DEFAULT_LLM)
        return cls(
            endpoint_name=d,
            clarification_endpoint=os.getenv("LLM_ENDPOINT_CLARIFICATION", d),
            planning_endpoint=os.getenv("LLM_ENDPOINT_PLANNING", d),
            sql_synthesis_table_endpoint=os.getenv("LLM_ENDPOINT_SQL_SYNTHESIS_TABLE", d),
            sql_synthesis_genie_endpoint=os.getenv("LLM_ENDPOINT_SQL_SYNTHESIS_GENIE", d),
            execution_endpoint=os.getenv("LLM_ENDPOINT_EXECUTION", d),
            summarize_endpoint=os.getenv("LLM_ENDPOINT_SUMMARIZE", d),
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'LLMConfig':
        d = _mc_get(mc, "llm_endpoint", _DEFAULT_LLM)
        return cls(
            endpoint_name=d,
            clarification_endpoint=_mc_get(mc, "llm_endpoint_clarification", d),
            planning_endpoint=_mc_get(mc, "llm_endpoint_planning", d),
            sql_synthesis_table_endpoint=_mc_get(mc, "llm_endpoint_sql_synthesis_table", d),
            sql_synthesis_genie_endpoint=_mc_get(mc, "llm_endpoint_sql_synthesis_genie", d),
            execution_endpoint=_mc_get(mc, "llm_endpoint_execution", d),
            summarize_endpoint=_mc_get(mc, "llm_endpoint_summarize", d),
        )


@dataclass
class VectorSearchConfig:
    """Vector search configuration."""
    function_name: str
    endpoint_name: str
    embedding_model: str
    pipeline_type: str
    
    @classmethod
    def from_env(cls, uc_config: UnityCatalogConfig) -> 'VectorSearchConfig':
        default_function = f"{uc_config.full_schema_name}.search_genie_spaces"
        return cls(
            function_name=os.getenv("VECTOR_SEARCH_FUNCTION", default_function),
            endpoint_name=os.getenv("VS_ENDPOINT_NAME", "genie_multi_agent_vs"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "databricks-gte-large-en"),
            pipeline_type=os.getenv("PIPELINE_TYPE", "TRIGGERED"),
        )

    @classmethod
    def from_model_config(cls, mc: Any, uc_config: UnityCatalogConfig) -> 'VectorSearchConfig':
        default_function = f"{uc_config.full_schema_name}.search_genie_spaces"
        return cls(
            function_name=default_function,
            endpoint_name=_mc_get(mc, "vs_endpoint_name", "genie_multi_agent_vs"),
            embedding_model=_mc_get(mc, "embedding_model", "databricks-gte-large-en"),
            pipeline_type=_mc_get(mc, "pipeline_type", "TRIGGERED"),
        )


@dataclass
class TableMetadataConfig:
    """Table metadata enrichment configuration."""
    sample_size: int
    max_unique_values: int
    volume_name: str
    enriched_docs_table: str
    source_table: str
    genie_space_ids: list[str]
    sql_warehouse_id: str
    
    @classmethod
    def from_env(cls, uc_config: UnityCatalogConfig) -> 'TableMetadataConfig':
        default_space_ids = "01f072dbd668159d99934dfd3b17f544,01f08f4d1f5f172ea825ec8c9a3c6064,01f073c5476313fe8f51966e3ce85bd7,01f07795f6981dc4a99d62c9fc7c2caa,01f08a9fd9ca125a986d01c1a7a5b2fe"
        space_ids_str = os.getenv("GENIE_SPACE_IDS") or os.getenv("genie_ids", default_space_ids)
        return cls(
            sample_size=int(os.getenv("SAMPLE_SIZE", "100")),
            max_unique_values=int(os.getenv("MAX_UNIQUE_VALUES", "50")),
            volume_name=os.getenv("VOLUME_NAME", "volume"),
            enriched_docs_table=os.getenv("ENRICHED_DOCS_TABLE", "enriched_genie_docs"),
            source_table=os.getenv("SOURCE_TABLE", "enriched_genie_docs_chunks"),
            genie_space_ids=_parse_csv(space_ids_str),
            sql_warehouse_id=os.getenv("SQL_WAREHOUSE_ID", "").strip(),
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'TableMetadataConfig':
        return cls(
            sample_size=int(_mc_get(mc, "sample_size", 100)),
            max_unique_values=int(_mc_get(mc, "max_unique_values", 50)),
            volume_name=_mc_get(mc, "volume_name", "volume"),
            enriched_docs_table=_mc_get(mc, "enriched_docs_table", "enriched_genie_docs"),
            source_table=_mc_get(mc, "source_table", "enriched_genie_docs_chunks"),
            genie_space_ids=_parse_csv(_mc_get(mc, "genie_space_ids", "")),
            sql_warehouse_id=str(_mc_get(mc, "sql_warehouse_id", "")).strip(),
        )



@dataclass
class ModelServingConfig:
    """Model serving configuration."""
    model_name: str
    endpoint_name: str
    workload_size: str
    scale_to_zero_enabled: bool
    
    @classmethod
    def from_env(cls) -> 'ModelServingConfig':
        return cls(
            model_name=os.getenv("MODEL_NAME", "super_agent_hybrid"),
            endpoint_name=os.getenv("ENDPOINT_NAME", "multi-agent-genie-endpoint"),
            workload_size=os.getenv("WORKLOAD_SIZE", "Small"),
            scale_to_zero_enabled=os.getenv("SCALE_TO_ZERO", "true").lower() == "true",
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'ModelServingConfig':
        return cls(
            model_name=_mc_get(mc, "model_name", "super_agent_hybrid"),
            endpoint_name=_mc_get(mc, "endpoint_name", "multi-agent-genie-endpoint"),
            workload_size=_mc_get(mc, "workload_size", "Small"),
            scale_to_zero_enabled=str(_mc_get(mc, "scale_to_zero", "true")).lower() == "true",
        )


@dataclass
class LakebaseConfig:
    """Lakebase database configuration for state management.
    
    Used for:
    - Short-term memory: Conversation checkpoints (CheckpointSaver)
    - Long-term memory: User preferences with semantic search (DatabricksStore)
    """
    instance_name: str
    embedding_endpoint: str
    embedding_dims: int
    
    @classmethod
    def from_env(cls) -> 'LakebaseConfig':
        return cls(
            instance_name=os.getenv("LAKEBASE_INSTANCE_NAME", "agent-state-db"),
            embedding_endpoint=os.getenv("LAKEBASE_EMBEDDING_ENDPOINT", "databricks-gte-large-en"),
            embedding_dims=int(os.getenv("LAKEBASE_EMBEDDING_DIMS", "1024")),
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'LakebaseConfig':
        return cls(
            instance_name=_mc_get(mc, "lakebase_instance_name", "agent-state-db"),
            embedding_endpoint=_mc_get(mc, "lakebase_embedding_endpoint", "databricks-gte-large-en"),
            embedding_dims=int(_mc_get(mc, "lakebase_embedding_dims", 1024)),
        )


@dataclass
class AgentConfig:
    """Complete agent system configuration."""
    databricks: DatabricksConfig
    unity_catalog: UnityCatalogConfig
    llm: LLMConfig
    vector_search: VectorSearchConfig
    table_metadata: TableMetadataConfig
    model_serving: ModelServingConfig
    lakebase: LakebaseConfig
    
    @property
    def enriched_docs_table_fq(self) -> str:
        """Fully qualified enriched docs (pre-chunks) table name."""
        return f"{self.unity_catalog.full_schema_name}.{self.table_metadata.enriched_docs_table}"

    @property
    def source_table_fq(self) -> str:
        """Fully qualified source (chunks) table name."""
        return f"{self.unity_catalog.full_schema_name}.{self.table_metadata.source_table}"

    @property
    def vs_index_fq(self) -> str:
        """Fully qualified vector search index name."""
        return f"{self.unity_catalog.full_schema_name}.{self.table_metadata.source_table}_vs_index"

    @classmethod
    def from_env(cls) -> 'AgentConfig':
        """Load all configuration from environment variables (.env / local dev)."""
        uc = UnityCatalogConfig.from_env()
        return cls(
            databricks=DatabricksConfig.from_env(),
            unity_catalog=uc,
            llm=LLMConfig.from_env(),
            vector_search=VectorSearchConfig.from_env(uc),
            table_metadata=TableMetadataConfig.from_env(uc),
            model_serving=ModelServingConfig.from_env(),
            lakebase=LakebaseConfig.from_env(),
        )

    @classmethod
    def from_model_config(cls, mc: Any) -> 'AgentConfig':
        """Load all configuration directly from ModelConfig (Databricks path)."""
        uc = UnityCatalogConfig.from_model_config(mc)
        return cls(
            databricks=DatabricksConfig.from_model_config(mc),
            unity_catalog=uc,
            llm=LLMConfig.from_model_config(mc),
            vector_search=VectorSearchConfig.from_model_config(mc, uc),
            table_metadata=TableMetadataConfig.from_model_config(mc),
            model_serving=ModelServingConfig.from_model_config(mc),
            lakebase=LakebaseConfig.from_model_config(mc),
        )
    
    def validate(self) -> None:
        """Validate configuration."""
        # # Check Databricks connectivity
        # if not self.databricks.host.startswith("https://"):
        #     raise ValueError("DATABRICKS_HOST must start with https://")
        
        # Check catalog/schema names
        if not self.unity_catalog.catalog_name:
            raise ValueError("CATALOG_NAME cannot be empty")
        
        if not self.unity_catalog.schema_name:
            raise ValueError("SCHEMA_NAME cannot be empty")
        
        # Check LLM endpoint
        if not self.llm.endpoint_name:
            raise ValueError("LLM_ENDPOINT cannot be empty")
        
        # Check vector search
        if not self.vector_search.endpoint_name:
            raise ValueError("VS_ENDPOINT_NAME cannot be empty")
        
        # Check SQL Warehouse ID (critical for SQL execution agent)
        if not self.table_metadata.sql_warehouse_id:
            raise ValueError(
                "SQL_WAREHOUSE_ID cannot be empty. "
                "Set it in .env file or environment variable. "
                "Get warehouse ID from: SQL Warehouses UI → Click warehouse → Copy ID from URL or Details"
            )
        
        # Validate SQL Warehouse ID format (should be alphanumeric, typically 16 chars)
        if not self.table_metadata.sql_warehouse_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(
                f"SQL_WAREHOUSE_ID format invalid: '{self.table_metadata.sql_warehouse_id}'. "
                "Expected alphanumeric string (e.g., '148ccb90800933a1')"
            )
        
        # Check Lakebase (critical for distributed Model Serving)
        if not self.lakebase.instance_name:
            raise ValueError("LAKEBASE_INSTANCE_NAME cannot be empty")
        
        if self.lakebase.embedding_dims <= 0:
            raise ValueError("LAKEBASE_EMBEDDING_DIMS must be positive")
        
        print("✓ Configuration validated successfully")
    
    def print_summary(self) -> None:
        """Print configuration summary (without sensitive data)."""
        print("="*80)
        print("MULTI-AGENT SYSTEM CONFIGURATION")
        print("="*80)
        print(f"\nDatabricks:")
        print(f"  Host: {self.databricks.host}")
        print(f"  Token: {'*' * 10}...{self.databricks.token[-4:]}")
        print(f"\nUnity Catalog:")
        print(f"  Catalog: {self.unity_catalog.catalog_name}")
        print(f"  Schema: {self.unity_catalog.schema_name}")
        print(f"  Full Schema: {self.unity_catalog.full_schema_name}")
        print(f"  UC Functions: {', '.join(self.unity_catalog.uc_function_names)}")
        print(f"\nLLM Endpoints (Diversified by Agent):")
        print(f"  Default/Fallback: {self.llm.endpoint_name}")
        print(f"  Clarification Agent: {self.llm.clarification_endpoint}")
        print(f"  Planning Agent: {self.llm.planning_endpoint}")
        print(f"  SQL Synthesis (Table) Agent: {self.llm.sql_synthesis_table_endpoint}")
        print(f"  SQL Synthesis (Genie) Agent: {self.llm.sql_synthesis_genie_endpoint}")
        print(f"  SQL Execution Agent: {self.llm.execution_endpoint}")
        print(f"  Summarize Agent: {self.llm.summarize_endpoint}")
        print(f"\nVector Search:")
        print(f"  Function: {self.vector_search.function_name}")
        print(f"  Endpoint: {self.vector_search.endpoint_name}")
        print(f"  Embedding Model: {self.vector_search.embedding_model}")
        print(f"  Pipeline Type: {self.vector_search.pipeline_type}")
        print(f"\nTable Metadata:")
        print(f"  Sample Size: {self.table_metadata.sample_size}")
        print(f"  Max Unique Values: {self.table_metadata.max_unique_values}")
        print(f"  Volume Name: {self.table_metadata.volume_name}")
        print(f"  Enriched Docs Table: {self.table_metadata.enriched_docs_table}")
        print(f"  Source Table: {self.table_metadata.source_table}")
        print(f"  Enriched Docs Table (FQ): {self.enriched_docs_table_fq}")
        print(f"  Source Table (FQ): {self.source_table_fq}")
        print(f"  VS Index (FQ): {self.vs_index_fq}")
        print(f"  SQL Warehouse ID: {self.table_metadata.sql_warehouse_id}")
        print(f"  Genie Space IDs: {len(self.table_metadata.genie_space_ids)} spaces")
        for i, sid in enumerate(self.table_metadata.genie_space_ids, 1):
            print(f"    {i}. {sid}")
        print(f"\nModel Serving:")
        print(f"  Model Name: {self.model_serving.model_name}")
        print(f"  Endpoint Name: {self.model_serving.endpoint_name}")
        print(f"  Workload Size: {self.model_serving.workload_size}")
        print(f"  Scale to Zero: {self.model_serving.scale_to_zero_enabled}")
        print(f"\nLakebase (State Management):")
        print(f"  Instance Name: {self.lakebase.instance_name}")
        print(f"  Embedding Endpoint: {self.lakebase.embedding_endpoint}")
        print(f"  Embedding Dimensions: {self.lakebase.embedding_dims}")
        print(f"  Purpose: Short-term (checkpoints) + Long-term (user memories)")
        print("="*80)


# Singleton instance
_config: Optional[AgentConfig] = None


def is_databricks() -> bool:
    """Detect if running on Databricks (Notebook, Job, or Model Serving)"""
    return (
        "DATABRICKS_RUNTIME_VERSION" in os.environ or 
        os.environ.get("IS_MODEL_SERVING") == "true" or
        os.environ.get("DATABRICKS_MODEL_SERVING_ENVIRONMENT") == "true" or
        os.path.exists("/databricks")
    )


def get_config(reload: bool = False) -> AgentConfig:
    """
    Get or create the global configuration instance.

    Databricks path: YAML → ModelConfig → AgentConfig.from_model_config() (direct)
    Local dev path:  .env → load_dotenv() → AgentConfig.from_env()
    """
    global _config
    
    if _config is None or reload:
        if is_databricks():
            print("Detected Databricks environment. Loading configuration via ModelConfig...")
            try:
                from mlflow.models import ModelConfig
                dev_config_path = os.environ.get("AGENT_CONFIG_FILE", "/tmp/agent_config.yaml")
                mc = ModelConfig(development_config=dev_config_path)
                _config = AgentConfig.from_model_config(mc)
                print("✓ Configuration loaded via ModelConfig (direct)")
            except Exception as e:
                print(f"Warning: Failed to load ModelConfig: {e}. Falling back to env vars.")
                if reload:
                    load_dotenv(override=True)
                _config = AgentConfig.from_env()
        else:
            if reload:
                load_dotenv(override=True)
            _config = AgentConfig.from_env()

        _config.validate()
    
    return _config


# Example usage
if __name__ == "__main__":
    config = get_config()
    config.print_summary()

