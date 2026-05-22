# DBX-UnifiedChat: A code-first multi-agent NL-to-SQL solution for cross-domain analytics

**An open-source Databricks App that combines Genie, Vector Search, Lakebase, and LangGraph to answer complex questions spanning multiple data domains—with explainable SQL, fast first response, and production-grade observability.**

---

## Summary

- **Production-ready NL-to-SQL chat on Databricks.** DBX-UnifiedChat is an open-source [Databricks App](https://docs.databricks.com/dev-tools/databricks-apps/) and multi-agent runtime that uses Genie, Vector Search, Lakebase, MLflow, DBSQL, Unity Catalog Functions, Model Serving, and Databricks Asset Bundles to turn natural language into governed, executable SQL.
- **Field-validated on real clinical analytics.** At the University of Kansas Medical Center (KUMC), the team exercised the solution on the C3OD tumor-outcome use case over several months of iterative field validation. In that setting, reviewers observed answer accuracy above 85% on curated evaluation questions—not a universal benchmark, but a sustained result on a demanding cross-domain workload.
- **Built for speed on complex questions.** Through planning-driven routing, parallel Genie execution, multi-step instructed retrieval, metadata caching, pre-warm, and multithreaded agent orchestration, the field team observed roughly 1–2 second time-to-first-token on typical turns and completion times for complex cross-domain queries that were often one-third to one-half of what they saw with no/low-code custom agent alternatives in the same environment.

---

## Why cross-domain NL-to-SQL is hard

Most natural-language analytics tools work well when a question maps cleanly to a single Genie space or a handful of related tables. Real enterprise questions rarely stop there. A clinician or analyst might need tumor outcomes from one domain, enrollment criteria from another, and lab values from a third—then ask for a comparison that only makes sense once those pieces are joined correctly.

That complexity shows up in three ways:

1. **Routing** — The system must decide which domains matter, whether to use table metadata or Genie spaces, and how to decompose the question.
2. **Retrieval** — Schema context is large; pulling the wrong columns or missing join keys produces confident but wrong SQL.
3. **Execution and trust** — Users need the SQL, an explanation, and a way to validate before acting on an answer.

Genie, OneChat, and Agent Bricks each address parts of this story. DBX-UnifiedChat is a code-first, field-engineered pattern that composes those platform capabilities with explicit planning, dual synthesis routes, and an opinionated validation UI—similar in spirit to other field-built agent systems such as [MemEx](https://www.databricks.com/blog/memex-programmable-scratchpad-llm-agents), [AiChemy](https://www.databricks.com/blog/aichemy-next-generation-agent-mcp-skills-and-custom-data-drug-discovery), and the [multi-agent audience intelligence](https://www.databricks.com/blog/multi-agent-approach-audience-intelligence) approach, but tuned for governed cross-domain SQL on the Lakehouse.

---

## What is DBX-UnifiedChat?

[DBX-UnifiedChat](https://github.com/databricks-solutions/dbx-unifiedchat) is an open-source reference implementation from Databricks Field Solutions. It packages a LangGraph multi-agent backend, a Next.js chat UI, and a Databricks Asset Bundle so teams can deploy a full NL-to-SQL application—not just a notebook prototype.

Where it fits on the platform:


| Approach             | Best for                                  | DBX-UnifiedChat difference                                                                                    |
| -------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Genie spaces**     | Domain-scoped self-serve analytics        | Orchestrates multiple spaces and table routes in one conversation                                             |
| **OneChat**          | Unified chat across Databricks            | Adds explicit planning, dual SQL synthesis paths, and validation UX                                           |
| **Agent Bricks MAS** | Composable multi-agent apps with low code | Offers a code-first, extensible agent graph for teams that need custom routing and retrieval                  |
| **Custom agents**    | Fully bespoke workflows                   | Provides an accelerator with ETL, Vector Search index build, Lakebase state, and MLflow tracing already wired |


The supported deployment surface lives under `agent_app/`: one bundle deploys the app, metadata prep jobs, shared Lakebase infrastructure, and validation workflows.

---

## Architecture at a glance

The runtime follows a simple path: user question → Databricks App UI → MLflow AgentServer → LangGraph orchestration → Databricks services → streamed answer with SQL artifacts.

Figure 1: High-level multi-agent architecture

*Figure 1. High-level architecture: a supervisor coordinates planning, dual SQL synthesis routes, execution, and summarization over Databricks platform services.*

Specialized agents handle distinct responsibilities:

- **Supervisor** — Front-door orchestration and handoffs
- **Thinking & planning** — Query analysis, clarification, and execution plans
- **SQL synthesis (table route)** — Cross-table SQL via Unity Catalog Functions and Vector Search metadata
- **SQL synthesis (Genie route)** — Parallel Genie space queries with merged results
- **SQL execution** — Warehouse execution and result extraction
- **Summarize** — Streaming natural-language answers plus structured table/chart outputs

Figure 2: DBX-UnifiedChat on the Databricks Data Intelligence Platform

*Figure 2. DBX-UnifiedChat on the Databricks Data Intelligence Platform. **Build-time** (top): a single DAB entry point — `./scripts/deploy.sh` from local terminal or CI, or `deploy_notebook.py` from the workspace web terminal — runs the metadata flywheel and deploys the App + MLflow AgentServer. **Run-time** (middle): every user question flows through the LangGraph multi-agent pipeline. **Shared platform services** (bottom): runtime agents call into a shared service pool (some 1:1, some called by multiple agents), all governed by Unity Catalog and grounded in Delta Lake on cloud storage.*

Lakebase stores short- and long-term conversation state. MLflow captures traces for every turn, linking UI feedback back to experiments for continuous improvement.

---

## Three techniques that move the needle

### 1. Planning-driven route selection

Before any SQL is generated, a planning agent inspects the question, conversation history, and available domains. It emits an execution plan that selects either the **table route** (metadata-driven SQL synthesis) or the **Genie route** (space-specific NL-to-SQL via Genie agents). Clarification turns are handled in-graph so ambiguous questions do not silently produce bad SQL.

This mirrors patterns seen in other production agent systems: decompose first, then execute with the right tools—rather than asking a single monolithic prompt to do everything.

### 2. Multi-step instructed retrieval on the table route

When the plan selects the table route, the SQL synthesis agent does not dump the entire schema into context. Instead it uses a toolkit of Unity Catalog Functions—`get_space_summary`, `get_table_overview`, `get_column_detail`, and related helpers—to retrieve metadata step by step until it has enough to draft SQL. A reflection pass applies space-specific instructions before finalizing query blocks with explanations.

Figure 3: Multi-step instructed retrieval

*Figure 3. Table route: UC Functions retrieve metadata incrementally until the agent can draft, reflect on, and finalize SQL.*

Vector Search backs semantic retrieval over enriched Genie and table documentation, keeping prompts focused and token-efficient.

### 3. Parallel Genie kickoff on the Genie route

For questions that span multiple Genie spaces, the Genie-route synthesis agent builds tools only for relevant spaces, then invokes them in parallel—either through LangGraph tool-calling orchestration or direct `RunnableParallel` execution when the plan is fully specified. Per-space answers merge into a single synthesis result for downstream execution.

Figure 4: Parallel Genie route execution

*Figure 4. Genie route: planning assigns a per-space sub-question plan, then Genie agents run in parallel before results merge.*

In field testing at KUMC, this parallel kickoff—combined with agent pre-warm on startup and periodic keep-warm against Lakebase—contributed to the observed latency improvements on multi-domain C3OD questions compared with sequential no/low-code agent flows in the same workspace.

---

## Engineering for speed, accuracy, and cost efficiency

Behind the three headline techniques sits a layer of code-level optimizations in `agent_app/agent_server/` that move the needle on production qualities. The tables below summarize what's in the runtime today.

### Speed


| Technique                             | How it works                                                                                                                                                                                         | Why it matters                                                                                                          |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Compile-once workflow reuse           | The LangGraph workflow is compiled at module import and cached behind an idle-aware lock; a long-lived Lakebase `CheckpointSaver` is entered once and reused across requests.                        | Subsequent turns skip recompile and connection setup, slashing the warm-path cost relative to per-request compilation.  |
| Startup pre-warm + periodic keep-warm | A daemon `agent-keep-warm` thread eagerly compiles the workflow on startup and refreshes the Lakebase connection plus Genie space-context cache at a configurable interval.                          | The first user turn—and turns after idle—don't pay scale-to-zero cold-start latency.                                    |
| Recoverable-error retry               | Transient DB/SSL errors (admin shutdown, broken pipe, SSL drop) are detected and trigger a single transparent workflow-app reset + retry.                                                            | Flaky checkpointer connections don't surface to users, removing a class of perceived failures.                          |
| Parallel Genie execution              | `SQLSynthesisGenieAgent` exposes an `invoke_parallel_genie_agents` tool built on `RunnableParallel`; the prompt steers the agent to this path by default.                                            | Multi-space questions issue Genie calls simultaneously instead of serially.                                             |
| Parallel SQL execution                | `SQLExecutionAgent.execute_sql_parallel` runs multi-query plans on a `ThreadPoolExecutor` (default 4 workers) with per-thread connections; single-query plans take a fast path that skips threading. | Multi-query synthesis completes in roughly the time of the slowest query, not their sum.                                |
| Parallel clarification fan-out        | `classify_query_type` (irrelevant / meta) and `check_clarity` (context summary + clarity check) run as parallel subgraph nodes and fan in at `merge_classification`.                                 | The intent + clarity gate costs one wall-clock LLM round-trip instead of two.                                           |
| Token streaming for fast TTFT         | Summarize and meta-answer use `llm.stream(...)` and emit `text_delta` custom events through LangGraph's stream writer; the runtime logs TTFT and TTCL per turn.                                      | Users see tokens as soon as generation begins, keeping perceived latency low even when full synthesis is still running. |


### Accuracy


| Technique                                      | How it works                                                                                                                                                                                                                                                                   | Why it matters                                                                                                     |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------ |
| Planning-driven dual-route synthesis           | A dedicated planning node inspects the question, runs vector search over space summaries, and emits an explicit plan choosing the table route (UC-function metadata) or the Genie route (multi-space agents); the UI can force a route.                                        | Routing is explicit and traceable rather than buried inside one monolithic prompt.                                 |
| Multi-step instructed retrieval (table route)  | The table-route synthesis agent uses a UC-function toolkit (`get_space_summary`, `get_table_overview`, `get_column_detail`, `get_space_instructions`) to pull metadata incrementally.                                                                                          | Prompts stay focused and grounded; the model doesn't drown in irrelevant schema or hallucinate joins.              |
| In-graph clarification with structured options | `ClarificationAgent` uses `with_structured_output(TypedDict)` for irrelevant / meta / clear / unclear decisions and pauses the graph via LangGraph `interrupt()` when a question is ambiguous; sensitivity is tunable per turn (`off` … `on`).                                 | Ambiguous questions never silently produce bad SQL; users can dial how often the agent asks back.                  |
| Context-summary handoff between turns          | `check_clarity` produces a one-sentence `context_summary` that downstream nodes consume in place of the raw multi-turn transcript.                                                                                                                                             | Follow-ups get high-signal context without re-feeding the entire conversation history.                             |
| Retry + sequential continuation loops          | `_build_loop_prompt_prefix` re-enters synthesis with `loop_reason="retry"` (only failed queries regenerate, successful results carry forward) or `loop_reason="sequential_next"` (next sub-question, with prior results as data context, plus a `NO_MORE_QUERIES` early-exit). | Failures don't restart the whole plan, and later queries can learn from earlier results.                           |
| Row-grain hinting                              | `_infer_row_grain_hint` annotates execution results when columns suggest diagnosis-, procedure-, or coverage-level detail rows.                                                                                                                                                | The summarizer doesn't double-count patient-level metrics across repeated detail rows.                             |
| PII guardrail postfix                          | When `count_only` is set, every query is appended with an explicit "report patient count only" directive before reaching the workflow.                                                                                                                                         | A simple, auditable guardrail against PII leakage at the request boundary.                                         |
| Per-node MLflow tracing                        | Manual spans wrap every LangGraph node with smart input/output snapshots (truncated messages, sample rows, SQL preview); turn metadata carries `chat.thread_id`, `chat.request_kind`, `chat.retry_attempt`.                                                                    | User feedback in the UI links directly back to the responsible node, making accuracy regressions easy to diagnose. |


### Cost efficiency


| Technique                                 | How it works                                                                                                                                                                                                                             | Why it matters                                                                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| Per-agent LLM endpoints                   | `LLMConfig` lets each agent (clarification, planning, table synthesis, Genie synthesis, execution, summarize, chart, code-lookup) point to its own endpoint; lightweight gates default to smaller models, orchestration stays on Sonnet. | Spend matches the difficulty of each step instead of running every call on the most expensive model.                 |
| Module-level agent + LLM connection pools | `PlanningAgent` and `SQLSynthesisTableAgent` instances, `ChatDatabricks` clients (keyed by endpoint/temperature/max-tokens), and Genie agent wrappers are all cached at module scope.                                                    | Per-request init overhead is paid once; warm requests reuse hot clients.                                             |
| Vector-search and space-context caches    | Vector-search results are cached per `thread_id` in a bounded `OrderedDict` with a 10-minute TTL; the Genie space-summary table is cached for 30 minutes behind a single-flight lock.                                                    | Follow-up turns avoid redundant embedding calls and SQL Warehouse queries; concurrent requests don't duplicate work. |
| Chart result cache                        | The `/api/rechart` endpoint pulls from an in-memory store of full result sets (UUID-keyed, 30-minute TTL) populated during initial chart generation.                                                                                     | Chart re-renders don't re-execute SQL against the warehouse.                                                         |
| Minimal-state extraction                  | Each node calls a small `extract_*_context` helper that pulls only the fields it needs from `AgentState`.                                                                                                                                | LLM prompts stay compact, reducing per-call token spend.                                                             |
| Message-history truncation                | `truncate_message_history` keeps only the last 5 conversation turns plus system messages before passing to the summarizer.                                                                                                               | Long sessions don't blow up summarize-step token usage.                                                              |
| Trace payload truncation                  | `_trace_state_snapshot` previews messages to ~400 chars, samples results to 5 rows, and caps SQL preview at 2000 chars before emitting MLflow spans.                                                                                     | Trace storage costs stay bounded without losing the signal needed for debugging.                                     |
| Structured-output LLMs for screening      | Clarification gates use `with_structured_output(TypedDict)` for irrelevance, clarity, and continuation checks.                                                                                                                           | Short JSON responses replace free-text completion, cutting output tokens on every gate.                              |
| Per-agent model-usage telemetry           | `track_agent_model_usage` records which endpoint each agent invokes and how many times; cache hit/miss counters are exposed via `get_cache_stats()`.                                                                                     | Cost analysis is data-driven; teams can downshift specific agents when telemetry justifies it.                       |


---

## Validation, explainability, and traceability

Trustworthy analytics requires more than a fluent summary. Every answer path is designed to expose:

- **Generated SQL** with natural-language explanation
- **Execution results** in a paginated AG Grid table and optional chart workspace
- **Validation accordion** for reviewers to inspect and curate SQL before sharing
- **Thumbs up/down feedback** persisted against MLflow traces
- **One-click trace links** from the UI into the associated MLflow experiment

Figure 5: Validation and observability loop

*Figure 5. Validation loop: SQL, explanation, execution, user feedback, and MLflow traces form a closed observability cycle.*

This closed loop supports the kind of iterative field validation the KUMC team ran on C3OD—where domain experts could review SQL, leave feedback, and trace failures without leaving the app.

---

## User experience built for analysts and reviewers

The chat UI is not an afterthought. It is part of the solution:

Figure 6: UI experience map

*Figure 6. UI experience map: streaming responses, data visualization, validation, feedback, traces, navigation, and agent settings.*

- **Thinking and action collapsibles** show intermediate reasoning without cluttering the main answer
- **Streaming summaries** deliver fast time-to-first-token while longer synthesis runs
- **Visualization workspace** renders interactive charts alongside tabular results
- **Shareable threads and turn navigation** support review workflows across teams
- **Agent settings panel** exposes runtime configuration without redeploying code

See the [annotated UI walkthrough](https://github.com/databricks-solutions/dbx-unifiedchat/blob/main/docs/UI/UI_tutorial_annotated.png) in the repository for a visual tour.

---

## Two paths to adoption

### Path 1: Deploy as a Solutions Accelerator

Teams with Genie spaces, a SQL warehouse, and Unity Catalog metadata can deploy DBX-UnifiedChat as a starting point:

```bash
git clone https://github.com/databricks-solutions/dbx-unifiedchat.git
cd dbx-unifiedchat/agent_app
./scripts/deploy.sh --target dev --run-job full --start-app
```

The bundle runs metadata export, enrichment, Vector Search index build, Lakebase bootstrap, and app deployment from a single entry point. Customize Genie space IDs, catalog settings, and LLM endpoints in `databricks.yml`, then iterate locally with hot reload.

### Path 2: Extend into enterprise multi-agent chat

Because the agent graph is code-first LangGraph—not a black-box configuration—teams can add agents, tools, and routes for adjacent use cases: cohort building, metric explanation, operational dashboards, or handoffs to downstream MCP tools. Lakebase checkpoints and MLflow tracing carry forward as you extend the graph.

---

## Getting started and next steps

DBX-UnifiedChat is available now as open source under the Databricks License:

- **Repository:** [github.com/databricks-solutions/dbx-unifiedchat](https://github.com/databricks-solutions/dbx-unifiedchat)
- **Architecture docs:** [docs/ARCHITECTURE.md](https://github.com/databricks-solutions/dbx-unifiedchat/blob/main/docs/ARCHITECTURE.md)
- **Deployment guide:** run `./scripts/deploy.sh` from `agent_app/` or use the workspace-native deploy notebook

If you are evaluating NL-to-SQL for cross-domain analytics, start with a bounded domain pair, wire your Genie spaces and metadata ETL, and use the validation UI plus MLflow traces to build a curated question set—exactly the workflow that produced the KUMC field results described above.

---

## References

- [Instructed Retriever: Unlocking System-Level Reasoning in Search Agents](https://www.databricks.com/blog/instructed-retriever-unlocking-system-level-reasoning-search-agents) — a multi-step instructed retrieval pattern for table route
- [MemEx: A Programmable Scratchpad for LLM Agents](https://www.databricks.com/blog/memex-programmable-scratchpad-llm-agents) — programmable memory and tool orchestration patterns for production agents
- [AiChemy: Next-generation agent with MCP, skills and custom data for drug discovery](https://www.databricks.com/blog/aichemy-next-generation-agent-mcp-skills-and-custom-data-drug-discovery) — composing platform primitives into domain-specific agent systems
- [A multi-agent approach to audience intelligence](https://www.databricks.com/blog/multi-agent-approach-audience-intelligence) — parallel specialist agents coordinated for complex analytic questions
- [DBX-UnifiedChat repository](https://github.com/databricks-solutions/dbx-unifiedchat) — source, deployment bundle, and documentation

