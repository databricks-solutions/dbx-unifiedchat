import { useCallback, useMemo, useRef, useState } from 'react';
import ReactEChartsCore from 'echarts-for-react/lib/core';
import * as echarts from 'echarts/core';
import { BarChart, LineChart, ScatterChart, PieChart } from 'echarts/charts';
import {
  GridComponent,
  TooltipComponent,
  LegendComponent,
  ToolboxComponent,
  DataZoomComponent,
  TitleComponent,
} from 'echarts/components';
import { SVGRenderer } from 'echarts/renderers';

echarts.use([
  BarChart,
  LineChart,
  ScatterChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  ToolboxComponent,
  DataZoomComponent,
  TitleComponent,
  SVGRenderer,
]);

interface SeriesSpec {
  field: string;
  name: string;
  format?: 'currency' | 'number' | 'percent';
}

interface ChartConfig {
  chartType: string;
  title?: string;
  xAxisField?: string;
  groupByField?: string;
  series: SeriesSpec[];
  toolbox?: boolean;
}

export interface ChartSpec {
  config: ChartConfig;
  chartData: Record<string, unknown>[];
  downloadData?: Record<string, unknown>[];
  totalRows?: number;
  aggregated?: boolean;
  aggregationNote?: string | null;
}

const CHART_TYPES = ['bar', 'line', 'scatter', 'pie'] as const;

function fmtCurrency(v: number): string {
  return v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
}

function fmtAxis(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v}`;
}

function buildOption(spec: ChartSpec, overrideType?: string): echarts.EChartsOption {
  const { config, chartData } = spec;
  const type = overrideType ?? config.chartType ?? 'bar';
  const xField = config.xAxisField ?? '';
  const groupBy = config.groupByField;

  if (type === 'pie' && config.series.length > 0) {
    const field = config.series[0].field;
    return {
      title: { text: config.title, left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)' },
      legend: { bottom: 0, type: 'scroll' },
      series: [{
        type: 'pie',
        radius: ['30%', '65%'],
        data: chartData.map((r) => ({ name: String(r[xField] ?? ''), value: Number(r[field] ?? 0) })),
        emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.5)' } },
      }],
    };
  }

  const xValues = chartData.map((r) => String(r[xField] ?? ''));

  let seriesList: echarts.EChartsOption['series'];
  if (groupBy) {
    const groups = [...new Set(chartData.map((r) => String(r[groupBy] ?? '')))];
    seriesList = config.series.flatMap((s) =>
      groups.map((g) => ({
        name: `${s.name} (${g})`,
        type,
        data: chartData.filter((r) => String(r[groupBy]) === g).map((r) => Number(r[s.field] ?? 0)),
        emphasis: { focus: 'series' as const },
      })),
    );
  } else {
    seriesList = config.series.map((s) => ({
      name: s.name,
      type,
      data: chartData.map((r) => Number(r[s.field] ?? 0)),
      emphasis: { focus: 'series' as const },
    }));
  }

  const hasCurrency = config.series.some((s) => s.format === 'currency');

  return {
    title: { text: config.title, left: 'center', textStyle: { fontSize: 14 } },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      valueFormatter: hasCurrency ? (v) => fmtCurrency(Number(v)) : undefined,
    },
    legend: { bottom: 0, type: 'scroll' },
    grid: { left: '3%', right: '4%', bottom: '15%', top: '15%', containLabel: true },
    xAxis: { type: 'category', data: xValues, axisLabel: { rotate: xValues.length > 8 ? 30 : 0, interval: 0 } },
    yAxis: { type: 'value', axisLabel: hasCurrency ? { formatter: (v: number) => fmtAxis(v) } : undefined },
    dataZoom: chartData.length > 15 ? [{ type: 'slider', bottom: 25 }] : undefined,
    toolbox: config.toolbox ? { feature: { saveAsImage: {}, restore: {}, dataView: { readOnly: true } } } : undefined,
    series: seriesList,
  };
}

function toCsv(data: Record<string, unknown>[]): string {
  if (!data.length) return '';
  const cols = Object.keys(data[0]);
  const escape = (v: unknown) => {
    const s = v == null ? '' : String(v);
    return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rows = [cols.map(escape).join(',')];
  for (const row of data) {
    rows.push(cols.map((c) => escape(row[c])).join(','));
  }
  return rows.join('\n');
}

export function InteractiveChart({ spec }: { spec: ChartSpec }) {
  const [chartType, setChartType] = useState<string>(spec.config.chartType ?? 'bar');
  const chartRef = useRef<ReactEChartsCore>(null);

  const option = useMemo(() => buildOption(spec, chartType), [spec, chartType]);

  const handleReset = useCallback(() => {
    setChartType(spec.config.chartType ?? 'bar');
    chartRef.current?.getEchartsInstance()?.dispatchAction({ type: 'restore' });
  }, [spec.config.chartType]);

  const handleDownloadCsv = useCallback(() => {
    const data = spec.downloadData ?? spec.chartData;
    const csv = toCsv(data);
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'results.csv';
    a.click();
    URL.revokeObjectURL(url);
  }, [spec]);

  return (
    <div className="my-4 rounded-lg border border-zinc-200 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-900">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {CHART_TYPES.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setChartType(t)}
            className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
              chartType === t
                ? 'bg-blue-600 text-white'
                : 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-800 dark:text-zinc-300 dark:hover:bg-zinc-700'
            }`}
          >
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
        <button
          type="button"
          onClick={handleReset}
          className="rounded px-2.5 py-1 text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200"
        >
          Reset
        </button>
        <button
          type="button"
          onClick={handleDownloadCsv}
          className="ml-auto rounded bg-green-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-green-700"
        >
          Download CSV
        </button>
      </div>

      <ReactEChartsCore
        ref={chartRef}
        echarts={echarts}
        option={option}
        style={{ height: 400, width: '100%' }}
        opts={{ renderer: 'svg' }}
        notMerge
      />

      {spec.aggregated && spec.aggregationNote && (
        <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
          {spec.aggregationNote}
          {spec.totalRows && spec.downloadData
            ? ` — CSV contains ${Math.min(spec.downloadData.length, spec.totalRows)} of ${spec.totalRows} rows`
            : ''}
        </p>
      )}
    </div>
  );
}
