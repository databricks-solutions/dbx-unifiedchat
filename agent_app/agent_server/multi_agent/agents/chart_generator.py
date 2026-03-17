"""
Chart Generator for Multi-Agent System

Three-stage pipeline:
  1. LLM generates config only (~300 bytes) from a 50-row sample
  2. Python assembles real data with aggregation (<=30 chart points, <=200 download rows)
  3. Size guard ensures total JSON < 50KB
"""

import json
import logging
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from langchain_core.runnables import Runnable

logger = logging.getLogger(__name__)

MAX_CHART_POINTS = 30
MAX_DOWNLOAD_ROWS = 200
MAX_JSON_BYTES = 50_000
SAMPLE_ROWS_FOR_LLM = 50


def _json_default(o: Any) -> Any:
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


class ChartGenerator:
    """Generates ECharts-compatible chart specs from query result data."""

    def __init__(self, llm: Runnable):
        self.llm = llm

    def generate_chart(
        self,
        columns: List[str],
        data: List[Dict[str, Any]],
        original_query: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        End-to-end: LLM config -> Python assembly -> size guard.
        Returns the final chart payload or None if not plottable / on error.
        """
        if not data or not columns:
            return None

        try:
            config = self._get_llm_config(columns, data, original_query)
            if config is None or not config.get("plottable", False):
                return None

            chart_data, aggregated, agg_note = self._assemble_data(columns, data, config)
            if not chart_data:
                return None

            download_data = data[:MAX_DOWNLOAD_ROWS]

            payload = {
                "config": {
                    "chartType": config.get("chartType", "bar"),
                    "title": config.get("title", ""),
                    "xAxisField": config.get("xAxisField"),
                    "groupByField": config.get("groupByField"),
                    "series": config.get("series", []),
                    "toolbox": True,
                },
                "chartData": chart_data,
                "downloadData": download_data,
                "totalRows": len(data),
                "aggregated": aggregated,
                "aggregationNote": agg_note,
            }

            payload = self._size_guard(payload)
            return payload

        except Exception as e:
            logger.warning(f"ChartGenerator error: {e}")
            return None

    # ------------------------------------------------------------------
    # Stage 1: LLM config
    # ------------------------------------------------------------------

    def _get_llm_config(
        self,
        columns: List[str],
        data: List[Dict[str, Any]],
        original_query: str,
    ) -> Optional[Dict[str, Any]]:
        sample = data[:SAMPLE_ROWS_FOR_LLM]
        sample_json = json.dumps(sample, default=_json_default)
        if len(sample_json) > 4000:
            sample_json = sample_json[:4000] + "..."

        prompt = f"""You are a data-visualization expert. Given a query result, decide how to chart it.

User query: {original_query}
Columns: {columns}
Total rows: {len(data)}
Sample data ({len(sample)} rows):
{sample_json}

Return ONLY valid JSON (no markdown, no explanation):
{{
  "plottable": true/false,
  "chartType": "bar"|"line"|"scatter"|"pie",
  "title": "short chart title",
  "xAxisField": "column_for_x_axis",
  "groupByField": "column_for_grouping" or null,
  "series": [
    {{"field": "numeric_column", "name": "Display Name", "format": "currency"|"number"|"percent"}}
  ],
  "sortBy": {{"field": "col", "order": "desc"}} or null,
  "aggregation": null or {{"type": "topN", "n": 20, "metric": "col", "otherLabel": "Other"}}
}}

Rules:
- plottable=false ONLY for single scalars, all-text, or no numeric dimension
- High row count is NEVER a reason to skip; specify aggregation instead
- aggregation types: topN, timeBucket, histogram, frequency
- Keep series to <=3 fields
"""
        try:
            content = ""
            for chunk in self.llm.stream(prompt):
                if chunk.content:
                    content += chunk.content
            content = content.strip()

            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                return json.loads(match.group())
            return json.loads(content)
        except Exception as e:
            logger.warning(f"ChartGenerator LLM parse error: {e}")
            return None

    # ------------------------------------------------------------------
    # Stage 2: Python assembly
    # ------------------------------------------------------------------

    def _assemble_data(
        self,
        columns: List[str],
        data: List[Dict[str, Any]],
        config: Dict[str, Any],
    ) -> tuple[List[Dict], bool, Optional[str]]:
        """Returns (chart_data, aggregated, aggregation_note)."""
        aggregation = config.get("aggregation")
        sort_by = config.get("sortBy")

        if aggregation:
            chart_data, note = self._apply_aggregation(data, config, aggregation)
            return chart_data[:MAX_CHART_POINTS], True, note

        working = list(data)
        if sort_by:
            field = sort_by.get("field", "")
            desc = sort_by.get("order", "desc") == "desc"
            try:
                working.sort(key=lambda r: _numeric(r.get(field, 0)), reverse=desc)
            except Exception:
                pass

        if len(working) > MAX_CHART_POINTS:
            note = f"Showing first {MAX_CHART_POINTS} of {len(working)} rows"
            return working[:MAX_CHART_POINTS], True, note

        return working, False, None

    def _apply_aggregation(
        self,
        data: List[Dict[str, Any]],
        config: Dict[str, Any],
        aggregation: Dict[str, Any],
    ) -> tuple[List[Dict], str]:
        agg_type = aggregation.get("type", "topN")
        metric = aggregation.get("metric", "")
        x_field = config.get("xAxisField", "")

        if agg_type == "topN":
            n = aggregation.get("n", 20)
            other_label = aggregation.get("otherLabel", "Other")
            return self._agg_top_n(data, x_field, metric, config.get("series", []), n, other_label)

        if agg_type == "frequency":
            field = aggregation.get("field", x_field)
            top_n = aggregation.get("topN", 20)
            counts: Dict[str, int] = defaultdict(int)
            for row in data:
                counts[str(row.get(field, ""))] += 1
            sorted_items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
            chart_data = [{field: k, "count": v} for k, v in sorted_items[:top_n]]
            note = f"Top {top_n} of {len(counts)} unique values by frequency"
            return chart_data, note

        return data[:MAX_CHART_POINTS], f"Showing first {MAX_CHART_POINTS} rows"

    def _agg_top_n(
        self,
        data: List[Dict],
        x_field: str,
        metric: str,
        series: List[Dict],
        n: int,
        other_label: str,
    ) -> tuple[List[Dict], str]:
        groups: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        series_fields = [s["field"] for s in series] if series else ([metric] if metric else [])

        for row in data:
            key = str(row.get(x_field, ""))
            for f in series_fields:
                groups[key][f] += _numeric(row.get(f, 0))

        sort_field = metric or (series_fields[0] if series_fields else "")
        sorted_keys = sorted(groups.keys(), key=lambda k: groups[k].get(sort_field, 0), reverse=True)

        top_keys = sorted_keys[:n]
        rest_keys = sorted_keys[n:]

        chart_data = [{x_field: k, **{f: groups[k][f] for f in series_fields}} for k in top_keys]

        if rest_keys:
            other = {x_field: other_label}
            for f in series_fields:
                other[f] = sum(groups[k][f] for k in rest_keys)
            chart_data.append(other)

        note = f"Top {n} of {len(groups)} categories by {sort_field}"
        return chart_data, note

    # ------------------------------------------------------------------
    # Stage 3: Size guard
    # ------------------------------------------------------------------

    def _size_guard(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        raw = json.dumps(payload, default=_json_default)
        if len(raw.encode()) <= MAX_JSON_BYTES:
            return payload

        logger.warning(f"Chart payload {len(raw.encode())}B exceeds {MAX_JSON_BYTES}B, trimming downloadData")
        dl = payload.get("downloadData", [])
        while dl and len(json.dumps(payload, default=_json_default).encode()) > MAX_JSON_BYTES:
            dl = dl[: len(dl) // 2]
            payload["downloadData"] = dl

        if len(json.dumps(payload, default=_json_default).encode()) > MAX_JSON_BYTES:
            payload.pop("downloadData", None)
            logger.warning("Dropped downloadData entirely to meet size limit")

        return payload


def _numeric(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, Decimal):
        return float(val)
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
