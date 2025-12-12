# Multi-Agent System Architecture Diagram

This document contains the architecture diagrams for the KUMC POC Multi-Agent System project.

## Overview

The system consists of three main components:
1. **Multi-Agent System** - Main query processing and response system
2. **Vector Search Index Pipeline** - Builds searchable index of Genie space metadata
3. **Table Metadata Update Pipeline** - Enriches table metadata for better search

## Architecture Components

### Main Agents

1. **Super Agent** - Main orchestrator that coordinates all other agents
2. **Thinking & Planning Agent** - Breaks down queries and plans execution strategy
3. **Genie Agents** - Domain-specific agents (Patients, Medications, Diagnoses, etc.)
4. **SQL Synthesis Agent** - Assembles SQL queries from metadata or sub-results
5. **SQL Execution Agent** - Executes SQL queries on Delta tables

### Decision Points

1. **Question Clarity Check** - Determines if user query needs clarification
2. **Single vs Multiple Agent Decision** - Routes to appropriate execution path
3. **Join Requirement Decision** - Determines if row-wise join or verbal merge is needed

### Execution Paths

#### Path 1: Single Genie Agent
- Used when one Genie Agent can completely answer the question
- Direct call to Genie Agent → Return result

#### Path 2: Multiple Agents with Join (Fast Route)
- SQL Synthesis Agent uses metadata directly
- Generates and executes joined SQL query
- Returns result first (fast response)

#### Path 3: Multiple Agents with Join (Slow Route)
- Parallel async calls to multiple Genie Agents
- Collects sql_results from each agent
- SQL Synthesis Agent combines the results
- Returns comprehensive result (more accurate)

#### Path 4: Multiple Agents - Verbal Merge
- No common column needed for join
- Calls multiple agents in parallel
- Verbally integrates answers
- Returns integrated response with separate SQL results

### Supporting Pipelines

#### Pipeline 1: Table Metadata Update (Build First)
```
Sample Column Values → Build Value Dictionary → Enrich Metadata → Save to Delta Table
```

Purpose: Enriches table metadata with column samples and value dictionaries

#### Pipeline 2: Vector Search Index Generation (Build Second)
```
Parse Genie Space JSON → Get Baseline Docs → Enrich with Table Metadata → Build VS Index
```

Purpose: Creates searchable vector index of enriched Genie space metadata

#### Pipeline 3: Genie Space Export (Prerequisite)
```
Export Genie Spaces → Generate space.json
```

Purpose: Exports Genie space configurations for processing

### Data Stores

1. **Vector Search Index** - Databricks managed vector search index containing enriched metadata
2. **Delta Tables** - Underlying data tables behind Genie Agents
3. **Enriched Metadata Table** - Unity Catalog table with enriched parsed docs

### Integration

- **MLflow** - Logging and deployment for all agents
- **LangGraph** - Supervisor-style agent orchestration
- **Databricks ResponsesAgent** - Integration with Databricks Genie

### Future Components (TODO)

1. **Full-text Cache** - Cache complete query-response pairs
2. **Parameterized SQL Cache** - Cache SQL queries with parameters
3. **Semantic Cache** - Cache semantically similar queries

## Query Flow Example

### Example Query: "How many patients older than 50 years are on Voltaren?"

1. **User** → Super Agent
2. **Super Agent** → Thinking & Planning Agent
3. **Thinking & Planning Agent** breaks down:
   - Sub-task 1: Count patients older than 50 years
   - Sub-task 2: Count patients taking Voltaren
   - Requirement: Need to join on patient_id
4. **Vector Search Tool** identifies:
   - Patients Genie Agent (has age information)
   - Medications Genie Agent (has medication information)
5. **Decision**: Multiple agents + Join required
6. **Fast Route** (parallel execution):
   - SQL Synthesis Agent creates joined query using metadata
   - SQL Execution Agent runs on Delta tables
   - Returns count immediately
7. **Slow Route** (parallel execution):
   - Patients Agent: Gets patients > 50 years
   - Medications Agent: Gets patients on Voltaren
   - SQL Synthesis Agent: Joins results on patient_id
   - SQL Execution Agent: Executes final query
   - Returns comprehensive result
8. **Super Agent** → Returns to User

## File Formats

### Available Formats

1. **Mermaid Diagram** (`architecture_diagram.mmd`)
   - Can be rendered in Markdown viewers, GitHub, or Mermaid Live Editor
   - Convert to PNG/SVG/PDF using Mermaid CLI or online tools

2. **PlantUML Diagram** (`architecture_diagram.puml`)
   - Can be rendered using PlantUML tools
   - Convert to PNG/SVG/PDF using PlantUML

3. **CSV Format** (`architecture_nodes_edges.csv`)
   - Two sections: Edges (connections) and Nodes (components)
   - Can be imported into Lucid Chart, Visio, or other diagramming tools

4. **This Documentation** (`ARCHITECTURE_DIAGRAM.md`)
   - Human-readable overview with embedded diagrams

## How to Use These Files

### For Lucid Chart Import

1. **Method 1: Using CSV**
   - Open Lucid Chart
   - Go to File → Import Data → CSV
   - Import `architecture_nodes_edges.csv`
   - Map columns appropriately (Source → Target with Label)

2. **Method 2: Manual Recreation**
   - Use this documentation as reference
   - Create shapes based on Node definitions in CSV
   - Create connections based on Edge definitions in CSV
   - Apply colors from the Type/Color mapping

### For Rendering to PNG/PDF

#### Using Mermaid CLI
```bash
# Install mermaid-cli
npm install -g @mermaid-js/mermaid-cli

# Generate PNG
mmdc -i architecture_diagram.mmd -o architecture_diagram.png -w 3000 -H 2000

# Generate SVG (scalable)
mmdc -i architecture_diagram.mmd -o architecture_diagram.svg

# Generate PDF
mmdc -i architecture_diagram.mmd -o architecture_diagram.pdf
```

#### Using PlantUML
```bash
# Install PlantUML (requires Java)
# Download from https://plantuml.com/download

# Generate PNG
java -jar plantuml.jar architecture_diagram.puml

# Generate SVG
java -jar plantuml.jar -tsvg architecture_diagram.puml

# Generate PDF
java -jar plantuml.jar -tpdf architecture_diagram.puml
```

#### Using Online Tools
- **Mermaid Live Editor**: https://mermaid.live
  - Paste content from `architecture_diagram.mmd`
  - Export as PNG/SVG/PDF
  
- **PlantUML Online**: https://www.plantuml.com/plantuml
  - Paste content from `architecture_diagram.puml`
  - Export as PNG/SVG/PDF

### For Editing

All formats are text-based and can be edited in any text editor:
- Modify nodes, connections, labels
- Adjust colors, styles, layout
- Re-render after changes

## Color Legend

- **Blue (#4A90E2)** - Agents (Super Agent, Thinking Agent, Genie Agents, SQL Agents)
- **Green (#50C878)** - Data Stores (Vector Search Index, Delta Tables)
- **Orange (#F5A623)** - Processes (Search, Synthesis, Merge operations)
- **Red (#E94B3C)** - Decision Points (Clarity check, routing decisions)
- **Purple (#9B59B6)** - Pipeline Components (Table metadata, VS index, exports)
- **Gray (#BDC3C7)** - Future Components (Caching system)
- **Orange (#FFA500)** - Integration (MLflow)

## Mermaid Diagram

```mermaid
graph TB
    %% Main Entry Point
    User[User Query] --> SuperAgent[Super Agent<br/>Main Orchestrator]
    
    %% Super Agent connections
    SuperAgent --> ThinkingAgent[Thinking & Planning Agent<br/>Query Analysis & Breakdown]
    SuperAgent --> ClarificationLoop{Question<br/>Clear?}
    
    %% Clarification flow
    ClarificationLoop -->|Vague| Clarify[Request Clarification<br/>Provide Choices]
    Clarify --> User
    ClarificationLoop -->|Clear| ThinkingAgent
    
    %% Thinking Agent Process
    ThinkingAgent --> BreakDown[Break Down Query<br/>into Sub-tasks]
    BreakDown --> VSSearch[Vector Search Index Tool<br/>Find Relevant Genie Agents]
    
    %% Vector Search Index
    VSIndex[(Vector Search Index<br/>Enriched Genie Space Metadata)] --> VSSearch
    
    %% Decision Points
    VSSearch --> DecisionSingle{Single<br/>Genie Agent?}
    
    %% Single Agent Path
    DecisionSingle -->|Yes| SingleGenie[Call Single Genie Agent]
    SingleGenie --> GenieAgent1[Genie Agent<br/>Patients/Medications/Diagnoses/etc.]
    GenieAgent1 --> ReturnSingle[Return:<br/>thinking_result<br/>sql_result<br/>answer_result]
    ReturnSingle --> SuperAgent
    
    %% Multiple Agents Path
    DecisionSingle -->|No| DecisionJoin{Row-wise<br/>Join Needed?}
    
    %% Join Required - Fast Route
    DecisionJoin -->|Yes - Fast Route| SQLSynthesisFast[SQL Synthesis Agent<br/>Assembly SQL from Metadata]
    SQLSynthesisFast --> SQLExecFast[SQL Execution Agent<br/>Execute on Delta Tables]
    SQLExecFast --> DeltaTables[(Delta Tables<br/>Behind Genie Agents)]
    SQLExecFast --> ReturnFast[Return Fast Result]
    ReturnFast --> SuperAgent
    
    %% Join Required - Slow Route
    DecisionJoin -->|Yes - Slow Route| ParallelCall[Parallel Async Calls<br/>to Multiple Genie Agents]
    ParallelCall --> GenieAgent2A[Genie Agent A<br/>Sub-question 1]
    ParallelCall --> GenieAgent2B[Genie Agent B<br/>Sub-question 2]
    GenieAgent2A --> CollectSQL[Collect sql_results]
    GenieAgent2B --> CollectSQL
    CollectSQL --> SQLSynthesisSlow[SQL Synthesis Agent<br/>Assembly SQL from Results]
    SQLSynthesisSlow --> SQLExecSlow[SQL Execution Agent<br/>Execute on Delta Tables]
    SQLExecSlow --> DeltaTables
    SQLExecSlow --> ReturnSlow[Return Slow Result]
    ReturnSlow --> SuperAgent
    
    %% No Join Required - Verbal Merge
    DecisionJoin -->|No - Verbal Merge| MultiCall[Call Multiple Genie Agents<br/>in Parallel]
    MultiCall --> GenieAgent3A[Genie Agent A<br/>Sub-question 1]
    MultiCall --> GenieAgent3B[Genie Agent B<br/>Sub-question 2]
    GenieAgent3A --> VerbalMerge[Verbal Merge<br/>Integrate Answers]
    GenieAgent3B --> VerbalMerge
    VerbalMerge --> ReturnMerged[Return:<br/>integrated answer<br/>separate sql_results]
    ReturnMerged --> SuperAgent
    
    %% Final Output
    SuperAgent --> FinalAnswer[Final Answer to User]
    FinalAnswer --> User
    
    %% Supporting Pipelines Section
    subgraph Pipeline1["Pipeline 1: Table Metadata Update"]
        TMU1[Sample Column Values]
        TMU2[Build Value Dictionary]
        TMU3[Enrich Metadata]
        TMU4[Save to Delta Table]
        TMU1 --> TMU2
        TMU2 --> TMU3
        TMU3 --> TMU4
    end
    
    subgraph Pipeline2["Pipeline 2: Vector Search Index Generation"]
        VS1[Parse Genie Space JSON]
        VS2[Get Baseline Docs]
        VS3[Enrich with Table Metadata]
        VS4[Build VS Index]
        VS1 --> VS2
        VS2 --> VS3
        VS3 --> VS4
        VS4 --> VSIndex
    end
    
    subgraph Pipeline3["Pipeline 3: Genie Space Export"]
        GSE1[Export Genie Spaces]
        GSE2[Generate space.json]
        GSE1 --> GSE2
    end
    
    %% Pipeline dependencies
    GSE2 --> VS1
    TMU4 --> VS3
    
    %% Future Components (Caching)
    subgraph FutureCaching["Future: Caching System"]
        Cache1[Full-text Cache]
        Cache2[Parameterized SQL Cache]
        Cache3[Semantic Cache]
    end
    
    %% MLflow Integration
    MLflow[MLflow<br/>Logging & Deployment]
    SuperAgent -.logs.-> MLflow
    ThinkingAgent -.logs.-> MLflow
    GenieAgent1 -.logs.-> MLflow
    
    %% Styling
    classDef agentClass fill:#4A90E2,stroke:#2E5C8A,stroke-width:2px,color:#fff
    classDef dataClass fill:#50C878,stroke:#2E7D4E,stroke-width:2px,color:#fff
    classDef processClass fill:#F5A623,stroke:#C17D11,stroke-width:2px,color:#fff
    classDef decisionClass fill:#E94B3C,stroke:#A33428,stroke-width:2px,color:#fff
    classDef pipelineClass fill:#9B59B6,stroke:#6C3483,stroke-width:2px,color:#fff
    
    class SuperAgent,ThinkingAgent,GenieAgent1,GenieAgent2A,GenieAgent2B,GenieAgent3A,GenieAgent3B agentClass
    class VSIndex,DeltaTables dataClass
    class SQLSynthesisFast,SQLExecFast,SQLSynthesisSlow,SQLExecSlow,BreakDown,VSSearch processClass
    class DecisionSingle,DecisionJoin,ClarificationLoop decisionClass
    class TMU1,TMU2,TMU3,TMU4,VS1,VS2,VS3,VS4,GSE1,GSE2 pipelineClass
```

## Notes

- All agents are logged via MLflow for tracking and deployment
- The system follows the LangGraph ResponsesAgent pattern from Super_Agent.ipynb
- Build order: Pipeline 3 → Pipeline 1 → Pipeline 2 → Multi-Agent System
- Fast route provides quick responses while slow route ensures accuracy

