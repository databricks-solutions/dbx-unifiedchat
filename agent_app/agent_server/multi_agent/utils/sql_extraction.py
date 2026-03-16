"""
SQL Extraction Utilities for Multi-Query Support

This module provides utilities for extracting SQL queries from LLM-generated
content that may contain multiple queries in various formats:
  - Multiple ```sql code blocks (each treated as a separate query)
  - A single code block containing multiple ';'-separated queries
  - Leading comment lines (-- ...) used as query labels / titles
  - Raw SQL without code fences

Functions:
  - extract_all_sql_queries(content) -- main extraction entry point
  - extract_sql_queries_from_agent_result(result, agent_name) -- high-level helper
  - _split_multi_query_block(block) -- internal semicolon splitter
"""

import re
from typing import List, Tuple


# SQL keywords used to detect whether a text block contains actual SQL
SQL_KEYWORDS = {
    'SELECT', 'WITH', 'INSERT', 'UPDATE', 'DELETE',
    'CREATE', 'DROP', 'ALTER', 'MERGE', 'REPLACE',
}


def _split_multi_query_block(block: str) -> Tuple[List[str], List[str]]:
    """
    Split a single SQL block that may contain multiple semicolon-separated
    queries into individual queries and their leading-comment labels.
    
    Strategy:
      1. Split the block on ';' (the standard SQL statement terminator).
      2. For each resulting segment, extract any leading SQL comment lines
         (lines starting with '--') as the query label / title.
         The first leading comment line becomes the label text (without '--').
      3. Only keep segments that contain real SQL keywords.
    
    Args:
        block: A SQL string, possibly containing multiple ';'-separated statements,
               each optionally preceded by comment-line labels such as:
                 -- QUERY 1: Most Common Diagnoses
                 -- Patient counts by year
                 -- Top procedures
    
    Returns:
        Tuple of (queries, labels) where:
          - queries:  list of individual SQL query strings (with trailing ';')
          - labels:   list of label strings aligned by index. Empty string when
                      no leading comment was found for a query.
    """
    raw_segments = block.split(';')
    
    queries: List[str] = []
    labels: List[str] = []
    
    for segment in raw_segments:
        segment = segment.strip()
        if not segment:
            continue
        
        # Does this segment contain actual SQL?
        segment_upper = segment.upper()
        if not any(kw in segment_upper for kw in SQL_KEYWORDS):
            continue
        
        # Walk lines: collect leading comment lines, find where SQL body starts
        lines = segment.split('\n')
        leading_comments: List[str] = []
        sql_start_idx = 0
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('--'):
                # Strip the '--' prefix and any surrounding whitespace
                comment_text = stripped.lstrip('-').strip()
                if comment_text:
                    leading_comments.append(comment_text)
            elif stripped == '':
                # Skip blank lines between comments and SQL body
                continue
            else:
                # First non-comment, non-blank line -> SQL body starts here
                sql_start_idx = i
                break
        else:
            # Every line was a comment or blank -> no actual SQL
            continue
        
        sql_text = '\n'.join(lines[sql_start_idx:]).strip()
        if not sql_text:
            continue
        
        # Use the first leading comment as the label / title
        label = leading_comments[0] if leading_comments else ""
        
        queries.append(sql_text.rstrip(';').strip() + ';')
        labels.append(label)
    
    return queries, labels


def extract_all_sql_queries(content: str) -> Tuple[List[str], List[str]]:
    """
    Extract all SQL queries from content, with support for:
      - Multiple ```sql code blocks (each treated as a separate query)
      - A single code block containing multiple ';'-separated queries
      - Leading comment lines (-- ...) used as query labels / titles
      - Raw SQL without code fences
    
    Args:
        content: The text content containing SQL (possibly in markdown code blocks)
        
    Returns:
        Tuple of (sql_queries, query_labels) where:
          - sql_queries:  list of individual SQL query strings
          - query_labels: list of label strings aligned with queries
    """
    raw_blocks: List[str] = []
    
    # 1. Find all ```sql blocks (case-insensitive)
    sql_pattern = r'```sql\s*(.*?)\s*```'
    matches = re.findall(sql_pattern, content, re.IGNORECASE | re.DOTALL)
    
    if matches:
        raw_blocks.extend([m.strip() for m in matches if m.strip()])
    else:
        # 2. Fallback: generic code blocks that look like SQL
        generic_pattern = r'```\s*(.*?)\s*```'
        matches = re.findall(generic_pattern, content, re.DOTALL)
        for match in matches:
            match = match.strip()
            if match and any(kw in match.upper() for kw in SQL_KEYWORDS):
                raw_blocks.append(match)
    
    # 3. Last resort: treat the raw content itself as SQL (no code fences)
    if not raw_blocks and any(kw in content.upper() for kw in ['SELECT', 'FROM']):
        raw_blocks = [content.strip()]
    
    # 4. Split each block on ';' to extract individual queries + labels
    all_queries: List[str] = []
    all_labels: List[str] = []
    for block in raw_blocks:
        queries, labels = _split_multi_query_block(block)
        all_queries.extend(queries)
        all_labels.extend(labels)
    
    return all_queries, all_labels


def extract_sql_queries_from_agent_result(
    result: dict,
    agent_name: str = "agent"
) -> Tuple[List[str], List[str]]:
    """
    Extract SQL queries and labels from agent result dictionary.
    
    This helper provides a simple, robust extraction strategy:
      1. Try result['sql'] field first (primary source)
      2. Try result['explanation'] field if sql is empty (fallback)
      3. Try combined content as last resort
    
    Takes first non-empty result, delegating all parsing complexity to
    extract_all_sql_queries() which handles:
      - Markdown code fences
      - Semicolon splitting
      - Label extraction from comments
      - Multiple query detection
    
    Args:
        result: Agent result dict with 'sql' and/or 'explanation' fields
        agent_name: Name for logging (e.g., 'sql_synthesis_table')
    
    Returns:
        Tuple of (queries, labels):
          - queries: List of individual SQL query strings
          - labels: List of label strings (from leading comments)
          Returns ([], []) if extraction fails
    
    Example:
        result = {
            "sql": "-- Query 1\\nSELECT...; -- Query 2\\nSELECT...;",
            "explanation": "Here are the queries...",
            "has_sql": True
        }
        queries, labels = extract_sql_queries_from_agent_result(result, "table_agent")
        # Returns: (["SELECT...", "SELECT..."], ["Query 1", "Query 2"])
    """
    sql_query = result.get("sql", "")
    explanation = result.get("explanation", "")
    
    # Attempt 1: Extract from sql field (primary source)
    if sql_query:
        queries, labels = extract_all_sql_queries(sql_query)
        if queries:
            print(f"✓ [{agent_name}] Extracted {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} from 'sql' field")
            return queries, labels
    
    # Attempt 2: Extract from explanation (fallback)
    if explanation:
        queries, labels = extract_all_sql_queries(explanation)
        if queries:
            print(f"✓ [{agent_name}] Extracted {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} from 'explanation' field")
            return queries, labels
    
    # Attempt 3: Try combined content (last resort)
    if sql_query or explanation:
        combined = f"{explanation}\n\n{sql_query}" if explanation and sql_query else (explanation or sql_query)
        queries, labels = extract_all_sql_queries(combined)
        if queries:
            print(f"✓ [{agent_name}] Extracted {len(queries)} quer{'y' if len(queries) == 1 else 'ies'} from combined content")
            return queries, labels
    
    # No SQL found
    print(f"⚠ [{agent_name}] No SQL queries extracted from result")
    return [], []


__all__ = [
    "SQL_KEYWORDS",
    "extract_all_sql_queries",
    "extract_sql_queries_from_agent_result",
]
