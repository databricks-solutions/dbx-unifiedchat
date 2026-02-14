# Tables-to-Genies Databricks App

Multi-page Dash application for creating Genie rooms from Unity Catalog tables.

## Features

1. **Catalog Browser** - Browse and select UC tables with tree view
2. **Enrichment Runner** - Enrich selected tables with metadata
3. **Graph Explorer** - Visualize table relationships (NetworkX-based)
4. **Genie Room Builder** - Select table groups and define rooms
5. **Genie Room Creator** - Create Genie spaces on Databricks

## Technology Stack

- **Framework**: Dash (Python) + dash-bootstrap-components
- **Backend**: Databricks SDK, databricks-sql-connector
- **Graph**: NetworkX + dash-cytoscape
- **Data**: 88 synthetic tables across 18 domains in serverless_dbx_unifiedchat_catalog

## Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABRICKS_HOST=https://fevm-serverless-dbx-unifiedchat.cloud.databricks.com
export DATABRICKS_TOKEN=your_token_here
export DATABRICKS_WAREHOUSE_ID=a4ed2ccbda385db9

# Run app
python app.py
```

Visit http://localhost:8080

## Deployment

```bash
databricks apps create tables-to-genies
databricks apps deploy tables-to-genies --source-code-path /Users/yang.yang@databricks.com/tables_to_genies/app
```

## Architecture

```
app/
├── app.py                  # Main Dash app with 5 pages
├── uc_browser.py           # UC catalog browsing
├── enrichment.py           # Table metadata enrichment
├── graph_builder.py        # NetworkX graph construction
├── genie_creator.py        # Genie space creation via SDK
├── requirements.txt        # Python dependencies
├── app.yaml                # Databricks App config
└── README.md
```

## Data Synthesis

The app works with 88 synthetic tables across 18 domains:
- world_cup_2026 (5 tables)
- nfl, nba (5 tables each)
- nasa, drug_discovery, semiconductors (5 tables each)
- genai (5 tables)
- nutrition, pharmaceuticals (5 tables each)
- iron_chef, japanese_anime, rock_bands (5 tables each)
- claims, providers (5 tables each)
- world_war_2, roman_history (5 tables each)
- international_policy (5 tables)
- demo_mixed (3 tables)

All data generated using Faker with realistic distributions, referential integrity, and domain-specific attributes.
