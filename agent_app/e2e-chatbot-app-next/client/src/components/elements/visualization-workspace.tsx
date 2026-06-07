'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BarChart3,
  ChevronDown,
  ChevronUp,
  Plus,
  Sparkles,
  Table2,
  WandSparkles,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';

import { PaginatedTable } from './paginated-table';
import { HeldOutPredictionPanel } from './held-out-prediction-panel';
import { InteractiveChart } from './interactive-chart';
import { useMessageId } from './message-context';
import {
  createBuilderStateFromChart,
  getChartBuilderUiConfig,
  getWorkspaceFields,
  materializeChartSpecFromBuilder,
  normalizeBuilderStateForChartType,
  restoreSavedCharts,
  validateBuilderState,
  type ChartBuilderState,
  type ChartSpec,
  type ChartWorkspace,
} from './chart-spec';

type VisualizationWorkspaceProps = {
  workspace: ChartWorkspace;
};

type AskChartState = {
  open: boolean;
  chartIndex: number;
  mode: 'replace' | 'add';
  prompt: string;
  isSubmitting: boolean;
  error: string;
};

type ChartHistoryEntry = {
  initial: ChartSpec;
  past: ChartSpec[];
  future: ChartSpec[];
};

type ChartHistoryState = Record<string, ChartHistoryEntry>;

function loadSavedCharts(key: string | null, fallback: ChartSpec[]): ChartSpec[] {
  if (!key) return fallback;
  try {
    const raw = localStorage.getItem(key);
    if (raw) return restoreSavedCharts(raw, fallback);
  } catch { /* ignore corrupt data */ }
  return fallback;
}

function getChartId(chart: ChartSpec, workspaceId: string, index = 0) {
  return chart.meta?.chartId ?? `${workspaceId}-${index}`;
}

function buildInitialChartHistory(charts: ChartSpec[], workspaceId: string): ChartHistoryState {
  return Object.fromEntries(
    charts.map((chart, index) => [
      getChartId(chart, workspaceId, index),
      { initial: chart, past: [], future: [] },
    ]),
  );
}

export function VisualizationWorkspace({ workspace }: VisualizationWorkspaceProps) {
  const messageId = useMessageId();
  const prefersTablePreview = useMemo(
    () => workspace.charts.some((chart) => chart.meta?.source === 'fallback' || chart.meta?.fallbackApplied),
    [workspace.charts],
  );
  const storageKey = messageId
    ? `viz-ws-${messageId}-${workspace.workspaceId}`
    : null;
  const initialCharts = useMemo(
    () => loadSavedCharts(storageKey, workspace.charts),
    [storageKey, workspace.charts],
  );

  const [isExpanded, setIsExpanded] = useState(prefersTablePreview);
  const [isTableVisible, setIsTableVisible] = useState(prefersTablePreview);
  const [isPredictVisible, setIsPredictVisible] = useState(false);
  const [charts, setCharts] = useState<ChartSpec[]>(initialCharts);
  const [chartHistory, setChartHistory] = useState<ChartHistoryState>(
    () => buildInitialChartHistory(initialCharts, workspace.workspaceId),
  );
  const [builderOpen, setBuilderOpen] = useState(false);
  const [builderChartIndex, setBuilderChartIndex] = useState(0);
  const [builderState, setBuilderState] = useState<ChartBuilderState>(
    createBuilderStateFromChart(workspace.charts[0], workspace),
  );
  const [askChart, setAskChart] = useState<AskChartState>({
    open: false,
    chartIndex: 0,
    mode: 'replace',
    prompt: '',
    isSubmitting: false,
    error: '',
  });
  const askPanelRef = useRef<HTMLDivElement>(null);

  const modified = useRef(false);
  const persistCharts = useCallback(
    (updater: ChartSpec[] | ((prev: ChartSpec[]) => ChartSpec[])) => {
      modified.current = true;
      setCharts(updater);
    },
    [],
  );

  const hydratedWorkspace = useMemo(
    () => ({
      ...workspace,
      charts,
      fields: getWorkspaceFields(workspace),
    }),
    [charts, workspace],
  );

  const replaceChart = useCallback((chartIndex: number, nextChart: ChartSpec) => {
    const currentChart = charts[chartIndex];
    if (!currentChart) return;
    const chartId = getChartId(currentChart, workspace.workspaceId, chartIndex);

    persistCharts((existing) => existing.map((chart, index) => (
      index === chartIndex ? nextChart : chart
    )));
    setChartHistory((existing) => {
      const entry = existing[chartId] ?? { initial: currentChart, past: [], future: [] };
      return {
        ...existing,
        [chartId]: {
          ...entry,
          past: [...entry.past, currentChart],
          future: [],
        },
      };
    });
  }, [charts, persistCharts, workspace.workspaceId]);

  const addChart = useCallback((nextChart: ChartSpec) => {
    const chartId = getChartId(nextChart, workspace.workspaceId, charts.length);
    persistCharts((existing) => [...existing, nextChart]);
    setChartHistory((existing) => ({
      ...existing,
      [chartId]: {
        initial: nextChart,
        past: [],
        future: [],
      },
    }));
  }, [charts.length, persistCharts, workspace.workspaceId]);

  const updateChartType = useCallback((chartIndex: number, nextType: string) => {
    const currentChart = charts[chartIndex];
    if (!currentChart || currentChart.config.chartType === nextType) return;
    const nextBuilder = normalizeBuilderStateForChartType(
      {
        ...createBuilderStateFromChart(currentChart, hydratedWorkspace),
        chartType: nextType as ChartSpec['config']['chartType'],
      },
      getWorkspaceFields(hydratedWorkspace),
    );
    const nextChart = materializeChartSpecFromBuilder(
      hydratedWorkspace,
      nextBuilder,
      currentChart,
      'manual',
    );
    replaceChart(chartIndex, {
      ...nextChart,
      meta: {
        ...nextChart.meta,
        source: 'manual',
        rationale: 'Switched chart type from toolbar controls.',
      },
    });
  }, [charts, hydratedWorkspace, replaceChart]);

  const undoChart = useCallback((chartIndex: number) => {
    const currentChart = charts[chartIndex];
    if (!currentChart) return;
    const chartId = getChartId(currentChart, workspace.workspaceId, chartIndex);
    const entry = chartHistory[chartId];
    const previousChart = entry?.past.at(-1);
    if (!entry || !previousChart) return;

    persistCharts((existing) => existing.map((chart, index) => (
      index === chartIndex ? previousChart : chart
    )));
    setChartHistory((existing) => ({
      ...existing,
      [chartId]: {
        ...entry,
        past: entry.past.slice(0, -1),
        future: [currentChart, ...entry.future],
      },
    }));
  }, [chartHistory, charts, persistCharts, workspace.workspaceId]);

  const redoChart = useCallback((chartIndex: number) => {
    const currentChart = charts[chartIndex];
    if (!currentChart) return;
    const chartId = getChartId(currentChart, workspace.workspaceId, chartIndex);
    const entry = chartHistory[chartId];
    const nextChart = entry?.future[0];
    if (!entry || !nextChart) return;

    persistCharts((existing) => existing.map((chart, index) => (
      index === chartIndex ? nextChart : chart
    )));
    setChartHistory((existing) => ({
      ...existing,
      [chartId]: {
        ...entry,
        past: [...entry.past, currentChart],
        future: entry.future.slice(1),
      },
    }));
  }, [chartHistory, charts, persistCharts, workspace.workspaceId]);

  const resetChart = useCallback((chartIndex: number) => {
    const currentChart = charts[chartIndex];
    if (!currentChart) return;
    const chartId = getChartId(currentChart, workspace.workspaceId, chartIndex);
    const entry = chartHistory[chartId];
    if (!entry) return;
    const sameAsInitial = JSON.stringify(currentChart) === JSON.stringify(entry.initial);
    if (sameAsInitial) return;

    persistCharts((existing) => existing.map((chart, index) => (
      index === chartIndex ? entry.initial : chart
    )));
    setChartHistory((existing) => ({
      ...existing,
      [chartId]: {
        ...entry,
        past: [...entry.past, currentChart],
        future: [],
      },
    }));
  }, [chartHistory, charts, persistCharts, workspace.workspaceId]);
  useEffect(() => {
    if (!modified.current || !storageKey) return;
    try { localStorage.setItem(storageKey, JSON.stringify(charts)); } catch {}
  }, [charts, storageKey]);

  const currentChart = charts[builderChartIndex] ?? charts[0];
  const previewChart = useMemo(() => {
    try {
      return materializeChartSpecFromBuilder(
        hydratedWorkspace,
        builderState,
        currentChart,
        'manual',
      );
    } catch {
      return currentChart;
    }
  }, [builderState, currentChart, hydratedWorkspace]);

  const openBuilder = (chartIndex: number, mode: 'replace' | 'add') => {
    const chart = charts[chartIndex] ?? charts[0];
    setBuilderChartIndex(chartIndex);
    setBuilderState(
      normalizeBuilderStateForChartType(
        createBuilderStateFromChart(chart, hydratedWorkspace, mode),
        getWorkspaceFields(hydratedWorkspace),
      ),
    );
    setBuilderOpen(true);
  };

  const applyBuilder = () => {
    const validation = validateBuilderState(builderState, hydratedWorkspace);
    if (!validation.valid) return;
    const nextChart = materializeChartSpecFromBuilder(
      hydratedWorkspace,
      builderState,
      currentChart,
      'manual',
    );

    if (builderState.mode === 'add') addChart(nextChart);
    else replaceChart(builderChartIndex, nextChart);
    setBuilderOpen(false);
  };

  const duplicateChart = (chartIndex: number) => {
    const source = charts[chartIndex];
    if (!source) return;
    const duplicate = {
      ...source,
      meta: {
        ...source.meta,
        chartId: `${workspace.workspaceId}-copy-${Date.now()}`,
        source: 'manual' as const,
        rationale: 'Duplicated from an existing chart in the visualization workspace.',
      },
    };
    addChart(duplicate);
  };

  const removeChart = (chartIndex: number) => {
    const currentChart = charts[chartIndex];
    if (!currentChart) return;
    const chartId = getChartId(currentChart, workspace.workspaceId, chartIndex);
    persistCharts((existing) => existing.filter((_, index) => index !== chartIndex));
    setChartHistory((existing) => {
      const nextHistory = { ...existing };
      delete nextHistory[chartId];
      return nextHistory;
    });
  };

  const openAskChart = (chartIndex: number, mode: 'replace' | 'add') => {
    setAskChart((current) => ({
      ...current,
      open: true,
      chartIndex,
      mode,
      isSubmitting: false,
      error: '',
    }));
    requestAnimationFrame(() =>
      askPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }),
    );
  };

  const askAboutChartSelection = useCallback((chartIndex: number, prompt: string) => {
    setAskChart((current) => ({
      ...current,
      open: true,
      chartIndex,
      mode: 'replace',
      prompt,
      isSubmitting: false,
      error: '',
    }));
    requestAnimationFrame(() =>
      askPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }),
    );
  }, []);

  const submitAskChart = async () => {
    if (!askChart.prompt.trim()) {
      setAskChart((current) => ({ ...current, error: 'Describe the chart you want.' }));
      return;
    }

    const targetChart = charts[askChart.chartIndex] ?? charts[0];
    setAskChart((current) => ({ ...current, isSubmitting: true, error: '' }));

    try {
      const response = await fetch('/api/chart-workspaces/rechart', {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          workspace: hydratedWorkspace,
          existingChart: targetChart,
          request: askChart.prompt,
          mode: askChart.mode,
        }),
      });

      if (!response.ok) {
        throw new Error(`Chart request failed (${response.status})`);
      }

      const payload = await response.json();

      let nextChart: ChartSpec;

      if (payload.chart) {
        nextChart = payload.chart as ChartSpec;
      } else {
        const nextBuilderState = {
          ...createBuilderStateFromChart(targetChart, hydratedWorkspace, askChart.mode),
          ...(payload.overrides ?? {}),
          mode: askChart.mode,
        } as ChartBuilderState;
        const validation = validateBuilderState(nextBuilderState, hydratedWorkspace);
        if (!validation.valid) {
          throw new Error(validation.issues[0] ?? 'The requested chart combination is not valid.');
        }
        nextChart = materializeChartSpecFromBuilder(
          hydratedWorkspace,
          nextBuilderState,
          targetChart,
          'natural-language',
        );
      }

      if (askChart.mode === 'replace') {
        nextChart = {
          ...nextChart,
          meta: {
            ...nextChart.meta,
            chartId: targetChart.meta?.chartId,
          },
        };
        replaceChart(askChart.chartIndex, nextChart);
      } else {
        addChart(nextChart);
      }
      setAskChart((current) => ({ ...current, open: false, isSubmitting: false }));
    } catch (error) {
      setAskChart((current) => ({
        ...current,
        isSubmitting: false,
        error: error instanceof Error ? error.message : 'Unable to create that chart.',
      }));
    }
  };

  return (
    <div className="my-4 rounded-xl border border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-950">
      <button
        type="button"
        onClick={() => setIsExpanded((current) => !current)}
        className="flex w-full cursor-pointer items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-900"
      >
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-300">
          <BarChart3 className="h-5 w-5" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {workspace.title}
          </div>
          <div className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
            {charts.length} chart{charts.length === 1 ? '' : 's'} • {workspace.table.totalRows ?? workspace.table.rows.length} row{(workspace.table.totalRows ?? workspace.table.rows.length) === 1 ? '' : 's'}
            {workspace.sourceMeta?.rowGrainHint ? ` • ${workspace.sourceMeta.rowGrainHint}` : ''}
          </div>
        </div>
        <div className="hidden items-center gap-2 md:flex">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={(event) => {
              event.stopPropagation();
              openBuilder(0, 'add');
              setIsExpanded(true);
            }}
          >
            <Plus className="h-4 w-4" />
            Add chart
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={(event) => {
              event.stopPropagation();
              openAskChart(charts.length, 'add');
              setIsExpanded(true);
            }}
          >
            <WandSparkles className="h-4 w-4" />
            Ask chart
          </Button>
        </div>
        {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
      </button>

      {isExpanded && (
        <div className="border-t border-zinc-200 px-4 py-4 dark:border-zinc-800">
          <div className="space-y-4">
            {charts.map((chart, index) => (
              <div key={chart.meta?.chartId ?? `${workspace.workspaceId}-${index}`}>
                {askChart.open && askChart.chartIndex === index && (
                  <AskChartPanel
                    ref={askPanelRef}
                    askChart={askChart}
                    setAskChart={setAskChart}
                    onSubmit={submitAskChart}
                  />
                )}
                <InteractiveChart
                  spec={chart}
                  onOpenCustomizer={() => openBuilder(index, 'replace')}
                  onOpenPrompt={() => openAskChart(index, 'replace')}
                  onAskAboutSelection={(prompt) => askAboutChartSelection(index, prompt)}
                  onChangeChartType={(nextType) => updateChartType(index, nextType)}
                  onUndo={() => undoChart(index)}
                  onRedo={() => redoChart(index)}
                  onReset={() => resetChart(index)}
                  canUndo={(chartHistory[getChartId(chart, workspace.workspaceId, index)]?.past.length ?? 0) > 0}
                  canRedo={(chartHistory[getChartId(chart, workspace.workspaceId, index)]?.future.length ?? 0) > 0}
                  onDuplicate={() => duplicateChart(index)}
                  onRemove={() => removeChart(index)}
                  canRemove={charts.length > 1}
                />
              </div>
            ))}
            {askChart.open && askChart.chartIndex >= charts.length && (
              <AskChartPanel
                ref={askPanelRef}
                askChart={askChart}
                setAskChart={setAskChart}
                onSubmit={submitAskChart}
              />
            )}
          </div>

          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => openBuilder(0, 'add')}
            >
              <Plus className="h-4 w-4" />
              Add another chart
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => openAskChart(charts.length, 'add')}
            >
              <WandSparkles className="h-4 w-4" />
              Ask chart
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setIsTableVisible((current) => !current)}
            >
              <Table2 className="h-4 w-4" />
              {isTableVisible ? 'Hide table' : 'Show table'}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setIsPredictVisible((current) => !current)}
            >
              <Sparkles className="h-4 w-4" />
              {isPredictVisible ? 'Hide prediction' : 'Predict held-out rows'}
            </Button>
          </div>

          {isTableVisible && (
            <div className="mt-4">
              <PaginatedTable tableData={workspace.table} />
            </div>
          )}

          {isPredictVisible && (
            <div className="mt-4">
              <HeldOutPredictionPanel tableData={workspace.table} />
            </div>
          )}
        </div>
      )}

      <ChartBuilderSheet
        open={builderOpen}
        previewChart={previewChart}
        builderState={builderState}
        setBuilderState={setBuilderState}
        fields={getWorkspaceFields(hydratedWorkspace)}
        validation={validateBuilderState(builderState, hydratedWorkspace)}
        onOpenChange={setBuilderOpen}
        onApply={applyBuilder}
      />
    </div>
  );
}

export default VisualizationWorkspace;

import { forwardRef } from 'react';

const AskChartPanel = forwardRef<
  HTMLDivElement,
  {
    askChart: AskChartState;
    setAskChart: React.Dispatch<React.SetStateAction<AskChartState>>;
    onSubmit: () => void;
  }
>(function AskChartPanel({ askChart, setAskChart, onSubmit }, ref) {
  return (
    <div
      ref={ref}
      className="mb-4 rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900"
    >
      <div className="mb-2 flex items-center justify-between">
        <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
          Natural-language chart request
        </div>
        <select
          value={askChart.mode}
          onChange={(event) =>
            setAskChart((current) => ({
              ...current,
              mode: event.target.value as 'replace' | 'add',
            }))
          }
          className="rounded-md border border-zinc-200 bg-white px-2 py-1 text-xs dark:border-zinc-700 dark:bg-zinc-950"
        >
          <option value="replace">Replace chart</option>
          <option value="add">Add chart</option>
        </select>
      </div>
      <Textarea
        value={askChart.prompt}
        onChange={(event) =>
          setAskChart((current) => ({ ...current, prompt: event.target.value, error: '' }))
        }
        placeholder="Example: Make this a monthly line chart with service_month on X, paid_amount on Y, and color by benefit_type."
      />
      {askChart.error && (
        <p className="mt-2 text-xs text-red-600 dark:text-red-400">{askChart.error}</p>
      )}
      <div className="mt-3 flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          onClick={onSubmit}
          disabled={askChart.isSubmitting}
        >
          {askChart.isSubmitting ? 'Creating chart...' : 'Generate chart'}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setAskChart((current) => ({ ...current, open: false, error: '' }))}
        >
          Cancel
        </Button>
      </div>
    </div>
  );
});

function ChartBuilderSheet({
  open,
  previewChart,
  builderState,
  setBuilderState,
  fields,
  validation,
  onOpenChange,
  onApply,
}: {
  open: boolean;
  previewChart: ChartSpec;
  builderState: ChartBuilderState;
  setBuilderState: React.Dispatch<React.SetStateAction<ChartBuilderState>>;
  fields: ReturnType<typeof getWorkspaceFields>;
  validation: ReturnType<typeof validateBuilderState>;
  onOpenChange: (open: boolean) => void;
  onApply: () => void;
}) {
  const dimensionFields = fields.filter((field) => field.kind !== 'numeric' || field.role === 'dimension' || field.role === 'time');
  const numericFields = fields.filter((field) => field.kind === 'numeric' && field.role !== 'id');
  const ui = getChartBuilderUiConfig(builderState.chartType);
  const xAxisFields = ui.xAxisKind === 'numeric'
    ? numericFields
    : (dimensionFields.length ? [...dimensionFields, ...numericFields] : numericFields);
  const groupByFields = dimensionFields.filter((field) => field.name !== builderState.xAxisField);
  const selectedXAxisField = fields.find((field) => field.name === builderState.xAxisField);
  const showNumericBins = selectedXAxisField?.kind === 'numeric' && builderState.chartType !== 'scatter';

  const updateBuilder = <Key extends keyof ChartBuilderState>(
    key: Key,
    value: ChartBuilderState[Key],
  ) => {
    setBuilderState((current) => {
      const next = { ...current, [key]: value };
      return key === 'chartType' || key === 'xAxisField'
        ? normalizeBuilderStateForChartType(next, fields)
        : next;
    });
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="w-full overflow-y-auto p-0 sm:max-w-[560px]">
        <div className="flex h-full flex-col">
          <div className="border-b border-zinc-200 px-5 py-4 dark:border-zinc-800">
            <SheetTitle>Customize chart</SheetTitle>
            <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">
              Builder-style controls for field mapping, data shaping, and readability settings.
            </p>
          </div>

          <div className="flex-1 overflow-y-auto px-5 py-4">
            <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-800 dark:bg-zinc-900">
              <div className="mb-2 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
                Preview
              </div>
              <InteractiveChart spec={previewChart} />
            </div>

            <div className="mt-4 space-y-4">
              <BuilderSection title="Widget">
                <BuilderRow label="Mode">
                  <select
                    value={builderState.mode}
                    onChange={(event) => updateBuilder('mode', event.target.value as 'replace' | 'add')}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="replace">Replace chart</option>
                    <option value="add">Add chart</option>
                  </select>
                </BuilderRow>
                <BuilderRow label="Title">
                  <Input
                    value={builderState.title}
                    onChange={(event) => updateBuilder('title', event.target.value)}
                  />
                </BuilderRow>
                <BuilderRow label="Description">
                  <Textarea
                    value={builderState.description}
                    onChange={(event) => updateBuilder('description', event.target.value)}
                    className="min-h-[72px]"
                  />
                </BuilderRow>
              </BuilderSection>

              <BuilderSection title="Visualization">
                <BuilderRow label="Chart type">
                  <select
                    value={builderState.chartType}
                    onChange={(event) => updateBuilder('chartType', event.target.value as ChartBuilderState['chartType'])}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="bar">Bar</option>
                    <option value="line">Line</option>
                    <option value="histogram">Histogram</option>
                    <option value="area">Area</option>
                    <option value="stackedBar">Stacked bar</option>
                    <option value="normalizedStackedBar">100% stacked bar</option>
                    <option value="stackedArea">Stacked area</option>
                    <option value="scatter">Scatter</option>
                    <option value="pie">Pie</option>
                    <option value="heatmap">Heatmap</option>
                    <option value="boxplot">Boxplot</option>
                    <option value="dualAxis">Dual axis</option>
                  </select>
                </BuilderRow>
              </BuilderSection>

              <BuilderSection title="Fields">
                <BuilderRow label="X axis">
                  <FieldSelect
                    value={builderState.xAxisField}
                    onChange={(value) => updateBuilder('xAxisField', value)}
                    fields={xAxisFields}
                    allowEmpty={Boolean(ui.allowEmptyXAxis)}
                  />
                </BuilderRow>
                {showNumericBins && (
                <BuilderRow label="Numeric bins">
                  <Input
                    type="number"
                    min={2}
                    max={100}
                    value={builderState.xAxisBinCount ?? ''}
                    placeholder="No bucketing"
                    onChange={(event) =>
                      updateBuilder('xAxisBinCount', event.target.value ? Number(event.target.value) : null)
                    }
                  />
                </BuilderRow>
                )}
                {ui.showYAxis && (
                <BuilderRow label={ui.yAxisLabel}>
                  <FieldSelect
                    value={builderState.yAxisField}
                    onChange={(value) => updateBuilder('yAxisField', value)}
                    fields={numericFields}
                  />
                </BuilderRow>
                )}
                {ui.showSecondaryYAxis && (
                <BuilderRow label="Secondary Y axis">
                  <FieldSelect
                    value={builderState.secondaryYAxisField}
                    onChange={(value) => updateBuilder('secondaryYAxisField', value)}
                    fields={numericFields}
                    allowEmpty
                  />
                </BuilderRow>
                )}
                {ui.showGroupBy && (
                <BuilderRow label={ui.groupByLabel}>
                  <FieldSelect
                    value={builderState.groupByField}
                    onChange={(value) => updateBuilder('groupByField', value)}
                    fields={groupByFields}
                    allowEmpty
                  />
                </BuilderRow>
                )}
                {ui.showZAxis && (
                <BuilderRow label={ui.zAxisLabel}>
                  <FieldSelect
                    value={builderState.zAxisField}
                    onChange={(value) => updateBuilder('zAxisField', value)}
                    fields={numericFields}
                    allowEmpty
                  />
                </BuilderRow>
                )}
              </BuilderSection>

              <BuilderSection title="Data">
                {ui.showAggregation && (
                <BuilderRow label="Aggregation">
                  <select
                    value={builderState.aggregation}
                    onChange={(event) => updateBuilder('aggregation', event.target.value as ChartBuilderState['aggregation'])}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="sum">Sum</option>
                    <option value="avg">Average</option>
                    <option value="count">Count</option>
                    <option value="min">Min</option>
                    <option value="max">Max</option>
                  </select>
                </BuilderRow>
                )}
                {ui.showTimeBucket && (
                <BuilderRow label="Time grain">
                  <select
                    value={builderState.timeBucket}
                    onChange={(event) => updateBuilder('timeBucket', event.target.value as ChartBuilderState['timeBucket'])}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="none">No bucketing</option>
                    <option value="day">Day</option>
                    <option value="week">Week</option>
                    <option value="month">Month</option>
                    <option value="quarter">Quarter</option>
                    <option value="year">Year</option>
                  </select>
                </BuilderRow>
                )}
                {ui.showTopN && (
                <BuilderRow label="Top N">
                  <Input
                    type="number"
                    min={1}
                    value={builderState.topN ?? ''}
                    onChange={(event) =>
                      updateBuilder('topN', event.target.value ? Number(event.target.value) : null)
                    }
                  />
                </BuilderRow>
                )}
                {ui.showSort && (
                <BuilderRow label="Sort">
                  <select
                    value={builderState.sortDirection}
                    onChange={(event) => updateBuilder('sortDirection', event.target.value as 'asc' | 'desc')}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="asc">Ascending</option>
                    <option value="desc">Descending</option>
                  </select>
                </BuilderRow>
                )}
              </BuilderSection>

              <BuilderSection title="Style">
                <BuilderRow label="Palette">
                  <select
                    value={builderState.palette}
                    onChange={(event) => updateBuilder('palette', event.target.value)}
                    className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  >
                    <option value="default">Default</option>
                    <option value="cool">Cool</option>
                    <option value="warm">Warm</option>
                    <option value="neutral">Neutral</option>
                  </select>
                </BuilderRow>
                <BuilderRow label="Single color">
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={builderState.color || '#2563eb'}
                      onChange={(event) => updateBuilder('color', event.target.value)}
                      className="h-10 w-14 rounded-md border border-input bg-background p-1"
                    />
                    <Input
                      value={builderState.color}
                      onChange={(event) => updateBuilder('color', event.target.value)}
                      placeholder="#2563eb"
                    />
                  </div>
                </BuilderRow>
                <ToggleRow
                  label="Legend"
                  checked={builderState.showLegend}
                  onChange={(checked) => updateBuilder('showLegend', checked)}
                />
                <ToggleRow
                  label="Labels"
                  checked={builderState.showLabels}
                  onChange={(checked) => updateBuilder('showLabels', checked)}
                />
                <ToggleRow
                  label="Grid lines"
                  checked={builderState.showGridLines}
                  onChange={(checked) => updateBuilder('showGridLines', checked)}
                />
                {ui.showSmoothLines && (
                <ToggleRow
                  label="Smooth lines"
                  checked={builderState.smoothLines}
                  onChange={(checked) => updateBuilder('smoothLines', checked)}
                />
                )}
                <ToggleRow
                  label="Title"
                  checked={builderState.showTitle}
                  onChange={(checked) => updateBuilder('showTitle', checked)}
                />
                <ToggleRow
                  label="Description"
                  checked={builderState.showDescription}
                  onChange={(checked) => updateBuilder('showDescription', checked)}
                />
                <BuilderRow label="X label rotation">
                  <Input
                    type="number"
                    min={0}
                    max={90}
                    value={builderState.xAxisLabelRotation}
                    onChange={(event) => updateBuilder('xAxisLabelRotation', Number(event.target.value))}
                  />
                </BuilderRow>
                <BuilderRow label="Y label rotation">
                  <Input
                    type="number"
                    min={0}
                    max={90}
                    value={builderState.yAxisLabelRotation}
                    onChange={(event) => updateBuilder('yAxisLabelRotation', Number(event.target.value))}
                  />
                </BuilderRow>
              </BuilderSection>
            </div>
          </div>

          <div className="border-t border-zinc-200 px-5 py-4 dark:border-zinc-800">
            {!validation.valid && (
              <div className="mb-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/40 dark:text-amber-200">
                {validation.issues[0]}
              </div>
            )}
            <div className="flex items-center justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
                Cancel
              </Button>
              <Button type="button" onClick={onApply} disabled={!validation.valid}>
                Apply chart
              </Button>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}

function BuilderSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
      <h3 className="mb-3 text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        {title}
      </h3>
      <div className="space-y-3">{children}</div>
    </section>
  );
}

function BuilderRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <div className="mb-1 text-xs font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
        {label}
      </div>
      {children}
    </label>
  );
}

function ToggleRow({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border border-zinc-200 px-3 py-2 dark:border-zinc-800">
      <span className="text-sm text-zinc-700 dark:text-zinc-300">{label}</span>
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
    </div>
  );
}

function FieldSelect({
  value,
  onChange,
  fields,
  allowEmpty = false,
}: {
  value: string;
  onChange: (value: string) => void;
  fields: Array<{ name: string; label: string }>;
  allowEmpty?: boolean;
}) {
  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className="h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
    >
      {allowEmpty && <option value="">None</option>}
      {fields.map((field) => (
        <option key={field.name} value={field.name}>
          {field.label}
        </option>
      ))}
    </select>
  );
}
