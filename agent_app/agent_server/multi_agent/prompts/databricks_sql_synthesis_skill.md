# Databricks SQL dialect (required)

All **final** SQL you emit MUST be valid for **Databricks SQL** (Spark SQL as implemented in the Databricks SQL warehouse / SQL editor).

**Authoritative references (curated for this skill):**

- [Databricks SQL language manual](https://docs.databricks.com/sql/language-manual/index.html)
- [Data retrieval (SELECT, set operators, QUALIFY, etc.)](https://docs.databricks.com/aws/en/sql/language-manual#data-retrieval-statements) — *see also* [Azure Databricks data retrieval](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual)
- [SQL expressions](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-expression#syntax)
- [IDENTIFIER clause](https://docs.databricks.com/aws/en/sql/language-manual/sql-ref-names-identifier-clause) (dynamic `catalog` / `schema` / table / column references)
- [ANSI compliance / reserved keywords](https://learn.microsoft.com/en-us/azure/databricks/sql/language-manual/sql-ref-ansi-compliance) (when ANSI mode is enabled in the environment)

*Internal note for maintainers: content is aligned with Sage Context Catalog synthesis + Field `databricks-dbsql` guidance (Unity Catalog modeling, DBSQL features).*

## Non-negotiables

- **Dialect:** Spark SQL on Databricks — not MySQL, PostgreSQL, T-SQL, Oracle, or BigQuery.
- **Identifiers:** Prefer **`catalog.schema.table`** (Unity Catalog) when joining governed objects. Use **backticks** for reserved or special names. The **`IDENTIFIER(...)`** form exists for **dynamic** table/column references from string expressions or parameters; use it only when the plan calls for indirection, not for ordinary static names.
- **String literals:** Use **single quotes** (`'value'`). Double quotes delimit **identifiers** in many contexts, not string data.
- **Unions / set operations**
  - `UNION`, `INTERSECT`, and `EXCEPT` are supported; treat **`UNION` / `UNION ALL`** like standard SQL: **same number of columns** in every `SELECT` branch, with **pairwise compatible types**.
  - Align `SELECT` lists column-by-column; use `CAST(... AS <type>)` or typed `NULL` (e.g. `CAST(NULL AS STRING)`) to pad a branch when needed.
  - Prefer **`UNION ALL`** when duplicate removal is not required (cheaper, clearer than deduplicating `UNION`).
- **Semicolons:** One statement per `sql` fenced block in the agent response, ending with `;` (as required by your other instructions).
- **Functions:** Use **Spark SQL / Databricks** built-ins (e.g. `date_trunc`, `to_date`, `current_timestamp`, `sum`, `avg`, window functions). Avoid engine-specific names from other vendors unless documented for Databricks (e.g. map T-SQL / MySQL names to Spark equivalents: `coalesce`, `concat_ws` + `collect_list`, etc.).

## Databricks-specific features (use when appropriate)

- **`QUALIFY`:** Filter on **window function** results in one pass (instead of nested subqueries), when the warehouse supports it for your query shape.
- **SQL pipeline / pipe syntax (`|>`):** Databricks SQL supports **chained** `FROM ... |> WHERE ... |> SELECT ...` style pipelines (runtime 16.2+); use only if you are confident the deployment supports it and the plan benefits from that style; otherwise standard `SELECT`/`CTE` is fine.
- **SORT BY / CLUSTER BY / DISTRIBUTE BY:** Spark distribution hints — only when you have a clear performance reason from the plan; default analytics queries usually do not need them.
- **LATERAL VIEW vs TVFs:** Prefer **table-valued functions** in **`FROM`** when applicable to current Databricks SQL patterns; avoid legacy patterns that do not match the language manual.
- **SQL Scripting** (`BEGIN`/`END`, procedures) is a **different** surface than a single ad-hoc `SELECT` for the chat flow — do not emit procedural blocks unless the user explicitly needs a script or the plan says so.

## Modeling and UC (from Databricks SQL best practices)

- Prefer **`catalog.schema.table`** and consistent **grain**; use **`DECIMAL`** (not floating point) for money and key metrics.
- Rely on **metadata from tools / plan** for real column names — do not invent columns.

## Pitfalls to avoid (common model errors)

- `UNION` branches with **different column counts** or **incompatible types** (parse/analysis errors).
- Using **non-Databricks** functions (e.g. `GROUP_CONCAT` → `concat_ws` with `collect_list` / `array_join` as appropriate per docs).
- Conflating **double-quoted identifiers** with **single-quoted string literals**.
- Mixing ambiguous **comma** joins — prefer explicit **`JOIN ... ON`**.

## When uncertain

- Prefer **CTEs** (`WITH`) for readability and to type-check each branch of a `UNION`.
- Re-read the execution plan and table metadata; do not invent objects or columns.
- If a construct might be T-SQL or MySQL-only, **do not use it** until you can name the **Databricks** built-in or pattern from the language manual.

## Optional: live documentation in the platform

In the **Databricks workspace**, operators can register **external MCP servers** (e.g. doc assistants) or use **UC-backed MCP** entry points for tools and Genie. That does not replace this static skill in the app runtime, but it can help humans and other agents. See `agent_app/scripts/discover_tools.py` for discoverable MCP patterns in a workspace.
