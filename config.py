"""
Configuration file for Multi-Agent Genie System

This module provides centralized configuration management for the entire
multi-agent system, including environment variables, defaults, and validation.
"""

import os
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class DatabricksConfig:
    """Databricks workspace configuration."""
    host: str
    token: str
    
    @classmethod
    def from_env(cls) -> 'DatabricksConfig':
        """Load configuration from environment variables."""
        host = os.getenv("DATABRICKS_HOST", "")
        token = os.getenv("DATABRICKS_TOKEN", "")
        
        if not host or not token:
            raise ValueError(
                "DATABRICKS_HOST and DATABRICKS_TOKEN must be set in environment"
            )
        
        return cls(host=host.rstrip("/"), token=token)


@dataclass
class UnityCatalogConfig:
    """Unity Catalog configuration."""
    catalog_name: str
    schema_name: str
    
    @classmethod
    def from_env(cls) -> 'UnityCatalogConfig':
        """Load configuration from environment variables."""
        return cls(
            catalog_name=os.getenv("CATALOG_NAME", "yyang"),
            schema_name=os.getenv("SCHEMA_NAME", "multi_agent_genie"),
        )
    
    @property
    def full_schema_name(self) -> str:
        """Get fully qualified schema name."""
        return f"{self.catalog_name}.{self.schema_name}"


@dataclass
class LLMConfig:
    """LLM endpoint configuration."""
    endpoint_name: str
    
    @classmethod
    def from_env(cls) -> 'LLMConfig':
        """Load configuration from environment variables."""
        return cls(
            endpoint_name=os.getenv("LLM_ENDPOINT", "databricks-claude-sonnet-4-5")
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
        """Load configuration from environment variables."""
        default_function = f"{uc_config.full_schema_name}.search_genie_spaces"
        
        return cls(
            function_name=os.getenv("VECTOR_SEARCH_FUNCTION", default_function),
            endpoint_name=os.getenv("VS_ENDPOINT_NAME", "genie_multi_agent_vs"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "databricks-gte-large-en"),
            pipeline_type=os.getenv("PIPELINE_TYPE", "TRIGGERED"),
        )


@dataclass
class TableMetadataConfig:
    """Table metadata enrichment configuration."""
    sample_size: int
    max_unique_values: int
    genie_exports_volume: str
    enriched_docs_table: str
    genie_space_ids: list[str]
    sql_warehouse_id: str
    
    @classmethod
    def from_env(cls, uc_config: UnityCatalogConfig) -> 'TableMetadataConfig':
        """Load configuration from environment variables."""
        default_volume = f"{uc_config.full_schema_name}.volume"
        default_table = f"{uc_config.full_schema_name}.enriched_genie_docs"
        
        # Parse Genie Space IDs from environment
        default_space_ids = "01f072dbd668159d99934dfd3b17f544,01f08f4d1f5f172ea825ec8c9a3c6064,01f073c5476313fe8f51966e3ce85bd7,01f07795f6981dc4a99d62c9fc7c2caa,01f08a9fd9ca125a986d01c1a7a5b2fe"
        space_ids_str = os.getenv("GENIE_SPACE_IDS", default_space_ids)
        space_ids = [sid.strip() for sid in space_ids_str.split(",") if sid.strip()]
        
        return cls(
            sample_size=int(os.getenv("SAMPLE_SIZE", "100")),
            max_unique_values=int(os.getenv("MAX_UNIQUE_VALUES", "50")),
            genie_exports_volume=os.getenv("GENIE_EXPORTS_VOLUME", default_volume),
            enriched_docs_table=os.getenv("ENRICHED_DOCS_TABLE", default_table),
            genie_space_ids=space_ids,
            sql_warehouse_id=os.getenv("SQL_WAREHOUSE_ID", ""),
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
        """Load configuration from environment variables."""
        return cls(
            model_name=os.getenv("MODEL_NAME", "multi_agent_genie_system"),
            endpoint_name=os.getenv("ENDPOINT_NAME", "multi-agent-genie-endpoint"),
            workload_size=os.getenv("WORKLOAD_SIZE", "Small"),
            scale_to_zero_enabled=os.getenv("SCALE_TO_ZERO", "true").lower() == "true",
        )


@dataclass
class LakebaseConfig:
    """Lakebase database configuration for state management.
    
    Lakebase is a fully-managed PostgreSQL OLTP database used for:
    - Short-term memory: Conversation checkpoints (CheckpointSaver)
    - Long-term memory: User preferences with semantic search (DatabricksStore)
    
    Required for distributed Model Serving to share state across instances.
    """
    instance_name: str
    embedding_endpoint: str
    embedding_dims: int
    
    @classmethod
    def from_env(cls) -> 'LakebaseConfig':
        """Load configuration from environment variables."""
        return cls(
            instance_name=os.getenv("LAKEBASE_INSTANCE_NAME", "agent-state-db"),
            embedding_endpoint=os.getenv("LAKEBASE_EMBEDDING_ENDPOINT", "databricks-gte-large-en"),
            embedding_dims=int(os.getenv("LAKEBASE_EMBEDDING_DIMS", "1024")),
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
    
    @classmethod
    def from_env(cls) -> 'AgentConfig':
        """Load all configuration from environment variables."""
        databricks = DatabricksConfig.from_env()
        unity_catalog = UnityCatalogConfig.from_env()
        llm = LLMConfig.from_env()
        vector_search = VectorSearchConfig.from_env(unity_catalog)
        table_metadata = TableMetadataConfig.from_env(unity_catalog)
        model_serving = ModelServingConfig.from_env()
        lakebase = LakebaseConfig.from_env()
        
        return cls(
            databricks=databricks,
            unity_catalog=unity_catalog,
            llm=llm,
            vector_search=vector_search,
            table_metadata=table_metadata,
            model_serving=model_serving,
            lakebase=lakebase,
        )
    
    def validate(self) -> None:
        """Validate configuration."""
        # Check Databricks connectivity
        if not self.databricks.host.startswith("https://"):
            raise ValueError("DATABRICKS_HOST must start with https://")
        
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
        print(f"\nLLM:")
        print(f"  Endpoint: {self.llm.endpoint_name}")
        print(f"\nVector Search:")
        print(f"  Function: {self.vector_search.function_name}")
        print(f"  Endpoint: {self.vector_search.endpoint_name}")
        print(f"  Embedding Model: {self.vector_search.embedding_model}")
        print(f"  Pipeline Type: {self.vector_search.pipeline_type}")
        print(f"\nTable Metadata:")
        print(f"  Sample Size: {self.table_metadata.sample_size}")
        print(f"  Max Unique Values: {self.table_metadata.max_unique_values}")
        print(f"  Exports Volume: {self.table_metadata.genie_exports_volume}")
        print(f"  Enriched Docs Table: {self.table_metadata.enriched_docs_table}")
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


def get_config(reload: bool = False) -> AgentConfig:
    """
    Get or create the global configuration instance.
    
    Args:
        reload: If True, reload configuration from environment (including .env file)
        
    Returns:
        AgentConfig instance
    """
    global _config
    
    if _config is None or reload:
        # Reload .env file if reloading (override existing env vars)
        if reload:
            load_dotenv(override=True)
        _config = AgentConfig.from_env()
        _config.validate()
    
    return _config


# Example usage
if __name__ == "__main__":
    config = get_config()
    config.print_summary()

