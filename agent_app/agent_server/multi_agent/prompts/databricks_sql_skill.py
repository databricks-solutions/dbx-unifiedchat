"""
Static Databricks SQL skill text for SQL synthesis agents.

Loaded from `databricks_sql_synthesis_skill.md` beside this module, with a small
in-process fallback if the file is missing (e.g. unusual packaging).

Sourcing (issue #31):
- **Static skill (default):** Markdown beside this file — curated to match the
  Databricks SQL / Spark SQL language manual, Sage Context Catalog summaries, and
  Field Engineering **databricks-dbsql**-style best practices (UC naming, DBSQL
  features). Update this file when product syntax shifts; re-sync with
  [language manual](https://docs.databricks.com/sql/language-manual/index.html).
- **MCP (optional, workspace):** The app does not call external doc MCPs during
  synthesis by default. For always-live docs, operators can register **external
  MCP** or Databricks native MCP entry points; see
  `agent_app/scripts/discover_tools.py` in this repo (and product docs on UC /
  Genie MCP). That path complements — but does not replace — this static prompt.

See: https://github.com/databricks-solutions/dbx-unifiedchat/issues/31
"""

from pathlib import Path

# Minimal fallback if the markdown file is not on disk; keep in sync with the file when possible.
_FALLBACK_DATABRICKS_SQL_SYNTHESIS_SKILL = """
## DATABRICKS SQL (REQUIRED DIALECT)

All final SQL must be valid **Databricks / Spark SQL** (Databricks SQL warehouse).
- `catalog.schema.table` where appropriate; **single-quoted** string literals.
- `UNION` / `UNION ALL`: same column count and compatible types per branch; prefer `UNION ALL` when deduplication is not required.
- **No T-SQL** patterns: e.g. no `CROSS APPLY` — use subqueries/CTEs/joins instead. After `GROUP BY`, do not `ORDER BY` ungrouped base columns; use grouped keys, aggregates, or output aliases.
- Use Spark SQL / Databricks built-ins; `QUALIFY` and `IDENTIFIER()` per language manual.
- https://docs.databricks.com/sql/language-manual/index.html
""".strip()


def load_databricks_sql_synthesis_skill() -> str:
    md = Path(__file__).resolve().parent / "databricks_sql_synthesis_skill.md"
    if md.is_file():
        text = md.read_text(encoding="utf-8").strip()
        if text:
            return text
    return _FALLBACK_DATABRICKS_SQL_SYNTHESIS_SKILL
