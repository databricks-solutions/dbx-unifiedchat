"use client";

import {
	AllCommunityModule,
	type CellFocusedEvent,
	type CellClassParams,
	type ColDef,
	type FilterChangedEvent,
	type GridApi,
	type GridReadyEvent,
	type ICellRendererParams,
	ModuleRegistry,
	type RowDoubleClickedEvent,
	type SortChangedEvent,
	type ValueFormatterParams,
	type ValueGetterParams,
	themeQuartz,
} from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import {
	ChevronDown,
	Columns3,
	Copy,
	Download,
	Eye,
	EyeOff,
	HelpCircle,
	Info,
	Keyboard,
	Maximize2,
	Minimize2,
	Pin,
	PinOff,
	RefreshCcw,
	Ruler,
	Search,
	Sigma,
	Sparkles,
	TableProperties,
	Thermometer,
} from "lucide-react";
import { useTheme } from "next-themes";
import {
	type KeyboardEvent as ReactKeyboardEvent,
	type MouseEvent as ReactMouseEvent,
	type ReactNode,
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";

import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuPortal,
	DropdownMenuSeparator,
	DropdownMenuSub,
	DropdownMenuSubContent,
	DropdownMenuSubTrigger,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import {
	Tooltip,
	TooltipContent,
	TooltipProvider,
	TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

import {
	COLUMN_KIND_LABELS,
	type ColumnKind,
	type ColumnMeta,
	type NormalizedTable,
	type PersistedTableState,
	type TableData,
	type TableDataRow,
	type TotalsMode,
	buildDelimited,
	buildTotalsRow,
	clearTableState,
	coerceValue,
	computeColumnMeta,
	downloadBlob,
	formatValueByKind,
	heatmapBackground,
	loadTableState,
	makeStateKey,
	normalizeTableData,
	parseNodesTable,
	saveTableState,
} from "./paginated-table-utils";

// Register community features once. Safe to call multiple times.
ModuleRegistry.registerModules([AllCommunityModule]);

// -----------------------------------------------------------------------------
// Theme
// -----------------------------------------------------------------------------

const agTheme = themeQuartz
	.withParams(
		{
			accentColor: "#3f3f46",
			backgroundColor: "#ffffff",
			foregroundColor: "#27272a",
			headerBackgroundColor: "#fafafa",
			headerTextColor: "#71717a",
			borderColor: "#e4e4e7",
			oddRowBackgroundColor: "rgba(244, 244, 245, 0.35)",
			rowHoverColor: "rgba(244, 244, 245, 0.7)",
			selectedRowBackgroundColor: "rgba(63, 63, 70, 0.08)",
			fontSize: 13,
			headerFontSize: 11,
			headerFontWeight: 600,
			spacing: 6,
			wrapperBorderRadius: 0,
		},
		"light",
	)
	.withParams(
		{
			accentColor: "#a1a1aa",
			backgroundColor: "#18181b",
			foregroundColor: "#e4e4e7",
			headerBackgroundColor: "rgba(39, 39, 42, 0.6)",
			headerTextColor: "#a1a1aa",
			borderColor: "#3f3f46",
			oddRowBackgroundColor: "rgba(39, 39, 42, 0.25)",
			rowHoverColor: "rgba(63, 63, 70, 0.4)",
			selectedRowBackgroundColor: "rgba(161, 161, 170, 0.15)",
			fontSize: 13,
			headerFontSize: 11,
			headerFontWeight: 600,
			spacing: 6,
			wrapperBorderRadius: 0,
		},
		"dark",
	);

// -----------------------------------------------------------------------------
// Density
// -----------------------------------------------------------------------------

type Density = "compact" | "comfortable" | "spacious";

const DENSITY_CONFIG: Record<
	Density,
	{ rowHeight: number; headerHeight: number }
> = {
	compact: { rowHeight: 28, headerHeight: 30 },
	comfortable: { rowHeight: 34, headerHeight: 34 },
	spacious: { rowHeight: 42, headerHeight: 38 },
};

const DENSITY_LABELS: Record<Density, string> = {
	compact: "Compact",
	comfortable: "Comfortable",
	spacious: "Spacious",
};

const TOTALS_LABELS: Record<TotalsMode, string> = {
	none: "No totals",
	sum: "Sum",
	avg: "Average",
	min: "Min",
	max: "Max",
};

const TOTALS_MODES: TotalsMode[] = ["none", "sum", "avg", "min", "max"];

const DEFAULT_PAGE_SIZE = 10;
const PAGE_SIZE_OPTIONS = [10, 25, 50, 100];

type LtmPredictionState =
	| { status: "idle" }
	| { status: "loading"; targetColumn: string; nTrain: number }
	| {
			status: "success";
			targetColumn: string;
			heldOutRowLabel: string;
			actual: unknown;
			prediction: unknown;
			nTrain: number;
			provider?: string;
			modelCheckpoint?: string;
	  }
	| { status: "error"; error: string };

type TabularPredictResponse = {
	success: boolean;
	provider?: string;
	model_checkpoint?: string;
	n_train?: number;
	predictions?: Array<{ prediction: unknown; probabilities?: Record<string, number> }>;
	error?: string;
};

// -----------------------------------------------------------------------------
// Small styled bits
// -----------------------------------------------------------------------------

const toolbarBtnClass =
	"inline-flex items-center gap-1 rounded-md border border-transparent px-2 py-1 text-xs font-medium text-zinc-600 transition-colors hover:border-zinc-200 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40 dark:text-zinc-300 dark:hover:border-zinc-700 dark:hover:bg-zinc-900";

const primaryBtnClass =
	"inline-flex items-center gap-1 rounded-md border border-zinc-300 bg-white px-2.5 py-1 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800";

// -----------------------------------------------------------------------------
// Custom cell renderer: "open detail" chevron
// -----------------------------------------------------------------------------

function DetailCellRenderer(params: ICellRendererParams) {
	if (params.node.rowPinned) return null;
	const context = params.context as {
		onOpenDetail?: (row: TableDataRow) => void;
	};
	return (
		<button
			type="button"
			onClick={(e) => {
				e.stopPropagation();
				context.onOpenDetail?.(params.data as TableDataRow);
			}}
			className="inline-flex h-5 w-5 items-center justify-center rounded text-zinc-400 transition-colors hover:bg-zinc-200 hover:text-zinc-700 dark:hover:bg-zinc-700 dark:hover:text-zinc-100"
			aria-label="Show row details"
		>
			<Info className="h-3.5 w-3.5" />
		</button>
	);
}

// -----------------------------------------------------------------------------
// Main component
// -----------------------------------------------------------------------------

export function PaginatedTable(
	props: Record<string, unknown> & { tableData?: TableData },
) {
	const { children, tableData } = props;

	const parsed = useMemo<NormalizedTable>(() => {
		if (tableData) return normalizeTableData(tableData);
		return parseNodesTable(children as ReactNode);
	}, [children, tableData]);

	const columnMeta = useMemo<Record<string, ColumnMeta>>(
		() => computeColumnMeta(parsed.rows, parsed.columns),
		[parsed.rows, parsed.columns],
	);

	const { resolvedTheme } = useTheme();
	const themeMode: "light" | "dark" =
		resolvedTheme === "dark" ? "dark" : "light";

	const stateKey = useMemo(
		() => makeStateKey(parsed.title, parsed.columns),
		[parsed.title, parsed.columns],
	);

	const [isExpanded, setIsExpanded] = useState(true);
	const [isSqlVisible, setIsSqlVisible] = useState(false);
	const [sqlCopied, setSqlCopied] = useState(false);
	const [isFullscreen, setIsFullscreen] = useState(false);
	const [quickFilter, setQuickFilter] = useState("");
	const [density, setDensity] = useState<Density>("comfortable");
	const [heatmap, setHeatmap] = useState(false);
	const [showFilters, setShowFilters] = useState(false);
	const [totalsMode, setTotalsMode] = useState<TotalsMode>("none");
	const [selectedCount, setSelectedCount] = useState(0);
	const [detailRow, setDetailRow] = useState<TableDataRow | null>(null);
	const [focusedCell, setFocusedCell] = useState<{
		rowIndex: number;
		colId: string;
		rowPinned: "top" | "bottom" | null;
	} | null>(null);
	const [gridHasFocus, setGridHasFocus] = useState(false);
	const [copiedCell, setCopiedCell] = useState(false);
	const [copiedTsv, setCopiedTsv] = useState(false);
	const [visibleRowsAfterFilter, setVisibleRowsAfterFilter] = useState<number>(
		parsed.totalRows,
	);
	const [ltmPrediction, setLtmPrediction] = useState<LtmPredictionState>({
		status: "idle",
	});

	const gridApiRef = useRef<GridApi | null>(null);
	const copyCellButtonRef = useRef<HTMLButtonElement | null>(null);
	const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
	const hasLoadedStateRef = useRef(false);

	// Reset transient UI when the table identity changes.
	// biome-ignore lint/correctness/useExhaustiveDependencies: reset UI state when the table identity changes
	useEffect(() => {
		setIsExpanded(true);
		setIsSqlVisible(false);
		setSqlCopied(false);
		setIsFullscreen(false);
		setQuickFilter("");
		setDetailRow(null);
		setFocusedCell(null);
		setGridHasFocus(false);
		setCopiedCell(false);
		setSelectedCount(0);
		setLtmPrediction({ status: "idle" });
		hasLoadedStateRef.current = false;
	}, [stateKey]);

	// Load persisted state from localStorage once (on mount or key change).
	useEffect(() => {
		const persisted = loadTableState(stateKey);
		if (persisted) {
			if (persisted.density) setDensity(persisted.density);
			if (persisted.heatmap !== undefined) setHeatmap(persisted.heatmap);
			if (persisted.showFilters !== undefined) setShowFilters(persisted.showFilters);
			if (persisted.totalsMode) setTotalsMode(persisted.totalsMode);
		}
	}, [stateKey]);

	const persistNow = useCallback(
		(patch: Partial<PersistedTableState>) => {
			const api = gridApiRef.current;
			const base: PersistedTableState = {
				density,
				heatmap,
				showFilters,
				totalsMode,
			};
			if (api) {
				try {
					base.columnState = api.getColumnState();
					base.filterModel = api.getFilterModel();
				} catch {
					// API may not be ready; ignore.
				}
			}
			saveTableState(stateKey, { ...base, ...patch });
		},
		[density, heatmap, showFilters, totalsMode, stateKey],
	);

	const schedulePersist = useCallback(() => {
		if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
		saveTimerRef.current = setTimeout(() => {
			persistNow({});
		}, 250);
	}, [persistNow]);

	// Persist non-grid-API settings when they change. The captured values
	// inside `persistNow` already reflect these, but we depend on them
	// explicitly so the effect fires when they change.
	// biome-ignore lint/correctness/useExhaustiveDependencies: persistNow reads density/heatmap/totalsMode via closure
	useEffect(() => {
		if (!hasLoadedStateRef.current) return;
		persistNow({});
	}, [density, heatmap, showFilters, totalsMode, persistNow]);

	// ---------------------------------------------------------------------------
	// Column defs
	// ---------------------------------------------------------------------------

	const columnDefs = useMemo<ColDef[]>(() => {
		const detailCol: ColDef = {
			colId: "__detail",
			headerName: "",
			width: 36,
			minWidth: 36,
			maxWidth: 36,
			pinned: "left",
			resizable: false,
			sortable: false,
			filter: false,
			floatingFilter: false,
			suppressHeaderMenuButton: true,
			suppressMovable: true,
			lockPinned: true,
			lockPosition: true,
			cellRenderer: DetailCellRenderer,
		};

		const dataCols: ColDef[] = parsed.columns.map((column) => {
			const m = columnMeta[column];
			const kind: ColumnKind = m?.kind ?? "text";
			const isNumeric =
				kind === "integer" ||
				kind === "number" ||
				kind === "currency" ||
				kind === "percent";
			const isSortable = true;
			const filterName =
				kind === "date"
					? "agDateColumnFilter"
					: isNumeric
						? "agNumberColumnFilter"
						: "agTextColumnFilter";

			const valueGetter =
				kind === "text"
					? undefined
					: (params: ValueGetterParams) => {
							const raw = params.data?.[column];
							// Preserve the pre-computed totals aggregate value.
							if (params.node?.rowPinned) return raw;
							return coerceValue(raw, kind);
						};

			const valueFormatter = (params: ValueFormatterParams) => {
				if (params.value == null || params.value === "") return "";
				if (params.node?.rowPinned && kind === "text") {
					return String(params.value);
				}
				return formatValueByKind(params.value, kind);
			};

			const cellStyle = (params: CellClassParams) => {
				if (params.node?.rowPinned) {
					return {
						fontWeight: 600,
						backgroundColor:
							themeMode === "dark"
								? "rgba(39, 39, 42, 0.85)"
								: "rgba(244, 244, 245, 0.85)",
					} as Record<string, string | number>;
				}
				if (heatmap && isNumeric) {
					const value =
						typeof params.value === "number"
							? params.value
							: parseNumericSafe(params.value);
					if (value == null || m?.min == null || m?.max == null) {
						return { backgroundColor: "transparent" } as Record<string, string>;
					}
					const bg = heatmapBackground(value, m.min, m.max, themeMode);
					if (bg) return { backgroundColor: bg } as Record<string, string>;
				}
				// Explicitly clear the background so previously applied heatmap
				// styles do not stick after toggling the feature off.
				return { backgroundColor: "transparent" } as Record<string, string>;
			};

			return {
				field: column,
				headerName: column,
				headerTooltip: `${column} · ${COLUMN_KIND_LABELS[kind]}`,
				filter: filterName,
				floatingFilter: true,
				sortable: isSortable,
				unSortIcon: true,
				resizable: true,
				type: isNumeric ? "rightAligned" : undefined,
				cellDataType: isNumeric ? "number" : kind === "date" ? "date" : "text",
				valueGetter,
				valueFormatter,
				getQuickFilterText: (params) =>
					formatValueByKind(params.data?.[column], kind),
				tooltipValueGetter: (params) => {
					const raw = params.data?.[column];
					if (raw == null) return "";
					const formatted = formatValueByKind(raw, kind);
					return formatted || String(raw);
				},
				cellStyle,
			} satisfies ColDef;
		});

		return [detailCol, ...dataCols];
	}, [parsed.columns, columnMeta, heatmap, themeMode]);

	const defaultColDef = useMemo<ColDef>(
		() => ({
			sortable: true,
			unSortIcon: true,
			resizable: true,
			filter: true,
			floatingFilter: true,
			minWidth: 120,
			flex: 1,
			suppressHeaderMenuButton: false,
		}),
		[],
	);

	// ---------------------------------------------------------------------------
	// Totals row (pinned bottom)
	// ---------------------------------------------------------------------------

	const pinnedBottomRowData = useMemo(() => {
		if (totalsMode === "none") return undefined;
		const row = buildTotalsRow(
			parsed.rows,
			columnMeta,
			parsed.columns,
			totalsMode,
		);
		return row ? [row] : undefined;
	}, [parsed.rows, parsed.columns, columnMeta, totalsMode]);

	// ---------------------------------------------------------------------------
	// Grid handlers
	// ---------------------------------------------------------------------------

	const gridContext = useMemo(
		() => ({ onOpenDetail: (row: TableDataRow) => setDetailRow(row) }),
		[],
	);

	const handleGridReady = useCallback(
		(event: GridReadyEvent) => {
			gridApiRef.current = event.api;
			const persisted = loadTableState(stateKey);
			if (persisted?.columnState) {
				try {
					event.api.applyColumnState({
						// biome-ignore lint/suspicious/noExplicitAny: persisted shape validated by AG Grid
						state: persisted.columnState as any,
						applyOrder: true,
					});
				} catch {
					// ignore; state may be stale
				}
			}
			if (persisted?.filterModel) {
				try {
					// biome-ignore lint/suspicious/noExplicitAny: persisted shape validated by AG Grid
					event.api.setFilterModel(persisted.filterModel as any);
				} catch {
					// ignore
				}
			}
			if (!persisted?.columnState) {
				event.api.sizeColumnsToFit();
			}
			setVisibleRowsAfterFilter(event.api.getDisplayedRowCount());
			hasLoadedStateRef.current = true;
		},
		[stateKey],
	);

	const handleColumnEvent = useCallback(() => {
		schedulePersist();
	}, [schedulePersist]);

	const handleSortChanged = useCallback(
		(_event: SortChangedEvent) => {
			schedulePersist();
		},
		[schedulePersist],
	);

	const handleFilterChanged = useCallback(
		(event: FilterChangedEvent) => {
			setVisibleRowsAfterFilter(event.api.getDisplayedRowCount());
			schedulePersist();
		},
		[schedulePersist],
	);

	const handleSelectionChanged = useCallback(() => {
		const api = gridApiRef.current;
		if (!api) return;
		setSelectedCount(api.getSelectedRows().length);
	}, []);

	const handleCellFocused = useCallback((event: CellFocusedEvent) => {
		if (event.rowIndex == null || !event.column) {
			setFocusedCell(null);
			setGridHasFocus(false);
			return;
		}
		const activeElement = document.activeElement as HTMLElement | null;
		if (activeElement?.closest(".ag-selection-checkbox, .ag-header-select-all")) {
			setFocusedCell(null);
			setGridHasFocus(true);
			return;
		}
		const colId =
			typeof event.column === "string"
				? event.column
				: event.column.getColId();
		setGridHasFocus(true);
		setFocusedCell({
			rowIndex: event.rowIndex,
			colId,
			rowPinned: event.rowPinned ?? null,
		});
	}, []);

	const handleRowDoubleClicked = useCallback((event: RowDoubleClickedEvent) => {
		if (event.rowPinned) return;
		// Ignore interactions that originated on the checkbox column or buttons.
		const target = (event.event?.target as HTMLElement | undefined) ?? null;
		if (target?.closest('input[type="checkbox"], button, a')) return;
		if (event.data) setDetailRow(event.data as TableDataRow);
	}, []);

	// Quick filter → grid option.
	useEffect(() => {
		const api = gridApiRef.current;
		if (!api) return;
		api.setGridOption("quickFilterText", quickFilter);
		setVisibleRowsAfterFilter(api.getDisplayedRowCount());
	}, [quickFilter]);

	useEffect(() => {
		const api = gridApiRef.current;
		if (!api || !isExpanded) return;
		try {
			api.refreshCells({ force: true });
		} catch {
			// The grid may have unmounted between renders; ignore stale API errors.
		}
	}, [heatmap, themeMode, isExpanded]);

	useEffect(() => {
		if (!isExpanded) {
			gridApiRef.current = null;
		}
	}, [isExpanded]);

	// ---------------------------------------------------------------------------
	// Toolbar actions
	// ---------------------------------------------------------------------------

	const handleToggleExpanded = useCallback(() => {
		setIsExpanded((expanded) => !expanded);
	}, []);
	const handleToggleSql = useCallback(() => {
		setIsSqlVisible((visible) => !visible);
	}, []);
	const handleCopySql = useCallback(() => {
		if (!parsed.sql) return;
		navigator.clipboard.writeText(parsed.sql).then(() => {
			setSqlCopied(true);
			setTimeout(() => setSqlCopied(false), 2000);
		});
	}, [parsed.sql]);
	const handleDownloadSql = useCallback(() => {
		if (!parsed.sql) return;
		downloadBlob(parsed.sqlFilename, parsed.sql, "text/sql;charset=utf-8;");
	}, [parsed.sql, parsed.sqlFilename]);

	const handleCopyFocusedCell = useCallback(() => {
		const api = gridApiRef.current;
		if (!api) return;
		const position = api.getFocusedCell();
		const focusedColId =
			typeof position?.column === "string"
				? position.column
				: position?.column?.getColId();
		const rowIndex = position?.rowIndex ?? focusedCell?.rowIndex;
		const rowPinned = position?.rowPinned ?? focusedCell?.rowPinned ?? null;
		const colId = focusedColId ?? focusedCell?.colId ?? null;
		if (rowIndex == null || !colId || colId === "__detail") return;
		const row =
			rowPinned === "bottom"
				? pinnedBottomRowData?.[rowIndex]
				: rowPinned === "top"
					? undefined
					: ((api.getDisplayedRowAtIndex(rowIndex)?.data as TableDataRow | undefined) ??
						undefined);
		if (!row) return;
		const raw = row[colId];
		const kind = columnMeta[colId]?.kind ?? "text";
		const value =
			raw == null || raw === "" ? "" : formatValueByKind(raw, kind) || String(raw);
		void navigator.clipboard.writeText(value).then(() => {
			setCopiedCell(true);
			setTimeout(() => setCopiedCell(false), 1600);
			setFocusedCell(null);
			setGridHasFocus(false);
		});
	}, [columnMeta, focusedCell, pinnedBottomRowData]);

	const handleFitColumns = useCallback(() => {
		gridApiRef.current?.autoSizeAllColumns();
	}, []);

	const handleResetView = useCallback(() => {
		const api = gridApiRef.current;
		if (!api) return;
		api.resetColumnState();
		api.setFilterModel(null);
		setQuickFilter("");
		setDensity("comfortable");
		setHeatmap(false);
		setShowFilters(false);
		setTotalsMode("none");
		clearTableState(stateKey);
		api.sizeColumnsToFit();
		setVisibleRowsAfterFilter(api.getDisplayedRowCount());
	}, [stateKey]);

	const getExportFilename = useCallback(
		(suffix: string) => {
			const base = parsed.filename.endsWith(".csv")
				? parsed.filename.slice(0, -4)
				: parsed.filename;
			return `${base}${suffix}`;
		},
		[parsed.filename],
	);

	const exportRows = useCallback(
		(source: "all" | "filtered" | "selected", includeHeader: boolean) => {
			const api = gridApiRef.current;
			const suffix =
				source === "selected" ? "-selected" : source === "all" ? "-all" : "";
			if (source === "all") {
				// Bypass grid filters/sort: dump the raw source rows.
				const csv = buildDelimited(
					parsed.columns,
					parsed.rows,
					",",
					includeHeader,
				);
				if (!csv) return;
				downloadBlob(
					`${getExportFilename(suffix)}.csv`,
					csv,
					"text/csv;charset=utf-8;",
				);
				return;
			}
			if (!api) return;
			api.exportDataAsCsv({
				fileName: getExportFilename(suffix),
				onlySelected: source === "selected",
				skipColumnHeaders: !includeHeader,
			});
		},
		[parsed.columns, parsed.rows, getExportFilename],
	);

	const getDisplayedRows = useCallback((): TableDataRow[] => {
		const api = gridApiRef.current;
		if (!api) return parsed.rows;
		const rows: TableDataRow[] = [];
		api.forEachNodeAfterFilterAndSort((node) => {
			if (node.rowPinned || !node.data) return;
			rows.push(node.data as TableDataRow);
		});
		return rows;
	}, [parsed.rows]);

	const handlePredictHeldOutRow = useCallback(async () => {
		const displayedRows = getDisplayedRows();
		if (displayedRows.length < 2) {
			setLtmPrediction({
				status: "error",
				error: "Need at least two rows: one held-out row and one labeled context row.",
			});
			return;
		}

		const selectedRows = gridApiRef.current?.getSelectedRows() as
			| TableDataRow[]
			| undefined;
		const heldOutRow =
			selectedRows?.length === 1
				? selectedRows[0]
				: displayedRows[displayedRows.length - 1];
		const targetColumn = chooseLtmRegressionTarget(
			parsed.columns,
			columnMeta,
			displayedRows,
			heldOutRow,
		);

		if (!targetColumn) {
			setLtmPrediction({
				status: "error",
				error:
					"No numeric target column was found for the held-out-row demo. Try a table with spend, cost, amount, rate, or count columns.",
			});
			return;
		}

		const featureColumns = parsed.columns.filter((column) => column !== targetColumn);
		const trainRows = displayedRows
			.filter((row) => row !== heldOutRow)
			.map((row) =>
				buildLtmRequestRow(row, parsed.columns, columnMeta, targetColumn, true),
			)
			.filter((row) => row[targetColumn] != null);

		if (trainRows.length < 1) {
			setLtmPrediction({
				status: "error",
				error: `No labeled context rows remain after holding out ${targetColumn}.`,
			});
			return;
		}

		const predictRows = [
			buildLtmRequestRow(heldOutRow, parsed.columns, columnMeta, targetColumn, false),
		];

		setLtmPrediction({
			status: "loading",
			targetColumn,
			nTrain: trainRows.length,
		});

		try {
			const response = await fetch("/api/tabular/predict", {
				method: "POST",
				credentials: "include",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({
					task: "regression",
					feature_columns: featureColumns,
					target_column: targetColumn,
					train_rows: trainRows,
					predict_rows: predictRows,
				}),
			});

			const payload = (await response.json()) as TabularPredictResponse;
			if (!response.ok || !payload.success) {
				throw new Error(payload.error || `TabICLv2 request failed (${response.status})`);
			}

			setLtmPrediction({
				status: "success",
				targetColumn,
				heldOutRowLabel: describeHeldOutRow(heldOutRow, parsed.columns, targetColumn),
				actual: coerceForLtm(heldOutRow[targetColumn], columnMeta[targetColumn]?.kind),
				prediction: payload.predictions?.[0]?.prediction,
				nTrain: payload.n_train ?? trainRows.length,
				provider: payload.provider,
				modelCheckpoint: payload.model_checkpoint,
			});
		} catch (error) {
			setLtmPrediction({
				status: "error",
				error: error instanceof Error ? error.message : String(error),
			});
		}
	}, [columnMeta, getDisplayedRows, parsed.columns]);

	const handleCopyTsv = useCallback(() => {
		const api = gridApiRef.current;
		let content = "";
		if (api && selectedCount > 0) {
			content = buildDelimited(
				parsed.columns,
				api.getSelectedRows() as TableDataRow[],
				"\t",
				true,
			);
		} else if (api) {
			try {
				content = api.getDataAsCsv({ columnSeparator: "\t" }) ?? "";
			} catch {
				content = "";
			}
		}
		if (!content) {
			content = buildDelimited(parsed.columns, parsed.rows, "\t", true);
		}
		if (!content) return;
		navigator.clipboard.writeText(content).then(() => {
			setCopiedTsv(true);
			setTimeout(() => setCopiedTsv(false), 1600);
		});
	}, [parsed.columns, parsed.rows, selectedCount]);

	const copyColumnValues = useCallback(
		(column: string) => {
			const rows = getDisplayedRows();
			const kind = columnMeta[column]?.kind ?? "text";
			const content = [column]
				.concat(
					rows.map((row) => {
						const raw = row[column];
						return raw == null || raw === ""
							? ""
							: formatValueByKind(raw, kind) || String(raw);
					}),
				)
				.join("\n");
			if (!content) return;
			void navigator.clipboard.writeText(content);
		},
		[columnMeta, getDisplayedRows],
	);

	const handleTableKeyDownCapture = useCallback(
		(event: ReactKeyboardEvent<HTMLDivElement>) => {
			if ((!event.metaKey && !event.ctrlKey) || event.altKey || event.shiftKey) {
				return;
			}
			if (event.key.toLowerCase() !== "c") return;
			const target = event.target as HTMLElement | null;
			if (
				target?.closest(
					'input, textarea, [contenteditable="true"], [role="textbox"]',
				)
			) {
				return;
			}
			if (!target?.closest('[role="grid"], .ag-root, .ag-root-wrapper')) {
				return;
			}
			const api = gridApiRef.current;
			if (!api) return;
			const focused = api.getFocusedCell();
			const focusedColId =
				typeof focused?.column === "string"
					? focused.column
					: focused?.column?.getColId();
			const hasFocusedCopyTarget =
				(focused?.rowIndex != null || focusedCell?.rowIndex != null) &&
				(focusedColId ?? focusedCell?.colId) !== "__detail";
			if (hasFocusedCopyTarget) {
				event.preventDefault();
				handleCopyFocusedCell();
				return;
			}
			if (selectedCount > 0) {
				event.preventDefault();
				handleCopyTsv();
			}
		},
		[focusedCell, handleCopyFocusedCell, handleCopyTsv, selectedCount],
	);

	const handleTableMouseDownCapture = useCallback(
		(event: ReactMouseEvent<HTMLDivElement>) => {
			const target = event.target as HTMLElement | null;
			if (!target) return;
			if (target.closest(".ag-selection-checkbox, .ag-header-select-all")) {
				setFocusedCell(null);
				setGridHasFocus(true);
				return;
			}
			if (target.closest('[role="grid"], .ag-root, .ag-root-wrapper')) return;
			if (copyCellButtonRef.current?.contains(target)) return;
			setFocusedCell(null);
			setGridHasFocus(false);
		},
		[],
	);

	const handleTableFocusCapture = useCallback(
		(event: React.FocusEvent<HTMLDivElement>) => {
			const target = event.target as HTMLElement | null;
			if (target?.closest('[role="grid"], .ag-root, .ag-root-wrapper')) {
				setGridHasFocus(true);
				return;
			}
			if (!copyCellButtonRef.current?.contains(target)) {
				setFocusedCell(null);
			}
			setGridHasFocus(false);
		},
		[],
	);

	const handleTableBlurCapture = useCallback(
		(event: React.FocusEvent<HTMLDivElement>) => {
			const target = event.target as HTMLElement | null;
			const relatedTarget = event.relatedTarget as HTMLElement | null;
			const blurredFromGrid = target?.closest('[role="grid"], .ag-root, .ag-root-wrapper');
			const nextInsideGrid = relatedTarget?.closest('[role="grid"], .ag-root, .ag-root-wrapper');
			if (!blurredFromGrid || nextInsideGrid) return;
			if (copyCellButtonRef.current?.contains(relatedTarget)) return;
			setFocusedCell(null);
			setGridHasFocus(false);
		},
		[],
	);

	// ---------------------------------------------------------------------------
	// Column visibility / pin helpers
	// ---------------------------------------------------------------------------

	// Column visibility/pin state lives in the AG Grid column state, not React
	// state, so we use a tick counter to force the menu to re-read it after
	// toggles. These helpers are intentionally not memoized — they're only used
	// inside the Columns menu render.
	const [, setColumnVisibilityTick] = useState(0);
	const forceRerenderHeader = useCallback(
		() => setColumnVisibilityTick((n) => n + 1),
		[],
	);

	const isColumnVisible = (column: string): boolean => {
		const api = gridApiRef.current;
		if (!api) return true;
		const state = api.getColumnState().find((c) => c.colId === column);
		return !state || !state.hide;
	};

	const getColumnPinned = (column: string): "left" | "right" | null => {
		const api = gridApiRef.current;
		if (!api) return null;
		const state = api.getColumnState().find((c) => c.colId === column);
		return (state?.pinned as "left" | "right" | null | undefined) ?? null;
	};

	const toggleColumnVisibility = useCallback(
		(column: string, visible: boolean) => {
			const api = gridApiRef.current;
			if (!api) return;
			api.setColumnsVisible([column], visible);
			forceRerenderHeader();
		},
		[forceRerenderHeader],
	);

	const pinColumn = useCallback(
		(column: string, side: "left" | "right" | null) => {
			const api = gridApiRef.current;
			if (!api) return;
			api.applyColumnState({
				state: [{ colId: column, pinned: side }],
			});
			forceRerenderHeader();
		},
		[forceRerenderHeader],
	);

	// ---------------------------------------------------------------------------
	// Render helpers
	// ---------------------------------------------------------------------------

	const hasData = parsed.columns.length > 0 && parsed.rows.length > 0;
	const totalRows = parsed.totalRows;
	const subtitle =
		totalRows === 0
			? "No rows"
			: parsed.isPreview
				? `${parsed.previewRowCount} preview rows shown`
				: visibleRowsAfterFilter !== totalRows
					? `${visibleRowsAfterFilter} of ${totalRows} rows (filtered)`
					: `${totalRows} row${totalRows === 1 ? "" : "s"} available`;

	const densityCfg = DENSITY_CONFIG[density];

	return (
		<TooltipProvider delayDuration={300}>
			<div
				data-streamdown="table-wrapper"
				data-ag-theme-mode={themeMode}
				onKeyDownCapture={handleTableKeyDownCapture}
				onFocusCapture={handleTableFocusCapture}
				onBlurCapture={handleTableBlurCapture}
				onMouseDownCapture={handleTableMouseDownCapture}
				className={cn(
					"my-3 overflow-hidden rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-900",
					isFullscreen &&
						"fixed inset-4 z-50 my-0 flex flex-col overflow-hidden shadow-2xl",
				)}
			>
				{/* Title bar */}
				<div className="flex items-center justify-between gap-3 border-b border-zinc-200 bg-zinc-50/80 px-3 py-2.5 dark:border-zinc-700 dark:bg-zinc-800/60">
					<div className="min-w-0">
						<div className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
							{parsed.title ?? "Results"}
						</div>
						<div className="text-xs text-zinc-500 dark:text-zinc-400">
							{subtitle}
							{selectedCount > 0 ? (
								<span className="ml-2 text-zinc-900 dark:text-zinc-100">
									· {selectedCount} selected
								</span>
							) : null}
						</div>
					</div>
					<div className="flex shrink-0 items-center gap-1.5">
						<button
							type="button"
							onClick={handleToggleExpanded}
							className={toolbarBtnClass}
						>
							{isExpanded ? "Collapse" : "Expand"}
						</button>
						{parsed.sql ? (
							<button
								type="button"
								onClick={handleToggleSql}
								className={primaryBtnClass}
							>
								{isSqlVisible ? "Hide SQL" : "Show SQL"}
							</button>
						) : null}
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									ref={copyCellButtonRef}
									type="button"
									onClick={handleCopyFocusedCell}
									disabled={
										!hasData ||
										!gridHasFocus ||
										!focusedCell ||
										focusedCell.colId === "__detail"
									}
									className={primaryBtnClass}
								>
									<Copy className="h-3.5 w-3.5" />
									{copiedCell ? "Cell copied" : "Copy cell"}
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								Copy the currently focused cell
							</TooltipContent>
						</Tooltip>
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={handleCopyTsv}
									disabled={!hasData}
									className={primaryBtnClass}
								>
									<Copy className="h-3.5 w-3.5" />
									{copiedTsv
										? "Copied"
										: selectedCount > 0
											? `Copy rows (${selectedCount})`
											: "Copy TSV"}
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								{selectedCount > 0
									? "Copy selected rows as tab-separated text"
									: "Copy current view as tab-separated text (paste into Excel/Sheets)"}
							</TooltipContent>
						</Tooltip>

						{/* Export split: primary click downloads filtered, caret opens menu */}
						<div className="flex items-stretch">
							<button
								type="button"
								onClick={() => exportRows("filtered", true)}
								disabled={!hasData}
								className={cn(primaryBtnClass, "rounded-r-none border-r-0")}
							>
								<Download className="h-3.5 w-3.5" />
								Download CSV
							</button>
							<DropdownMenu>
								<DropdownMenuTrigger asChild>
									<button
										type="button"
										disabled={!hasData}
										aria-label="Export options"
										className={cn(primaryBtnClass, "rounded-l-none px-1.5")}
									>
										<ChevronDown className="h-3.5 w-3.5" />
									</button>
								</DropdownMenuTrigger>
								<DropdownMenuContent align="end">
									<DropdownMenuLabel>Export as CSV</DropdownMenuLabel>
									<DropdownMenuItem
										onClick={() => exportRows("filtered", true)}
									>
										Filtered + sorted rows
									</DropdownMenuItem>
									<DropdownMenuItem
										onClick={() => exportRows("selected", true)}
										disabled={selectedCount === 0}
									>
										Selected rows only ({selectedCount})
									</DropdownMenuItem>
									<DropdownMenuItem onClick={() => exportRows("all", true)}>
										All rows (ignore filter/sort)
									</DropdownMenuItem>
									<DropdownMenuSeparator />
									<DropdownMenuLabel>Without header row</DropdownMenuLabel>
									<DropdownMenuItem
										onClick={() => exportRows("filtered", false)}
									>
										Filtered + sorted rows
									</DropdownMenuItem>
									<DropdownMenuItem
										onClick={() => exportRows("selected", false)}
										disabled={selectedCount === 0}
									>
										Selected rows only ({selectedCount})
									</DropdownMenuItem>
									<DropdownMenuItem onClick={() => exportRows("all", false)}>
										All rows
									</DropdownMenuItem>
								</DropdownMenuContent>
							</DropdownMenu>
						</div>

						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={() => setIsFullscreen((v) => !v)}
									className={toolbarBtnClass}
									aria-label={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
								>
									{isFullscreen ? (
										<Minimize2 className="h-3.5 w-3.5" />
									) : (
										<Maximize2 className="h-3.5 w-3.5" />
									)}
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								{isFullscreen ? "Exit fullscreen" : "Fullscreen"}
							</TooltipContent>
						</Tooltip>
					</div>
				</div>

				{/* SQL panel */}
				{isSqlVisible ? (
					<div className="border-b border-zinc-200 bg-zinc-50/50 dark:border-zinc-700 dark:bg-zinc-800/30">
						<div className="flex items-center justify-between gap-3 px-3 py-2">
							<div className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
								SQL
							</div>
							<div className="flex items-center gap-2">
								<button
									type="button"
									onClick={handleCopySql}
									className="rounded px-2 py-0.5 text-xs font-medium text-zinc-600 hover:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-700"
								>
									{sqlCopied ? "✓ Copied" : "Copy"}
								</button>
								<button
									type="button"
									onClick={handleDownloadSql}
									className="rounded px-2 py-0.5 text-xs font-medium text-zinc-600 hover:bg-zinc-200 dark:text-zinc-300 dark:hover:bg-zinc-700"
								>
									Download
								</button>
							</div>
						</div>
						<pre className="overflow-x-auto bg-zinc-50 p-3 text-xs leading-relaxed text-zinc-800 dark:bg-zinc-900 dark:text-zinc-200">
							<code>{parsed.sql}</code>
						</pre>
					</div>
				) : null}

				{/* Toolbar */}
				{isExpanded && hasData ? (
					<div className="flex flex-wrap items-center gap-1.5 border-b border-zinc-200 bg-zinc-50/40 px-3 py-1.5 dark:border-zinc-700 dark:bg-zinc-800/20">
						{/* Quick filter */}
						<div className="relative flex items-center">
							<Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-zinc-400" />
							<input
								type="text"
								value={quickFilter}
								onChange={(e) => setQuickFilter(e.target.value)}
								placeholder="Search all columns…"
								className="w-44 rounded-md border border-zinc-200 bg-white py-1 pl-7 pr-2 text-xs text-zinc-700 placeholder-zinc-400 shadow-sm focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200"
							/>
						</div>

						{/* Density */}
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<button type="button" className={toolbarBtnClass}>
									<Ruler className="h-3.5 w-3.5" />
									{DENSITY_LABELS[density]}
									<ChevronDown className="h-3 w-3 opacity-60" />
								</button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="start">
								{(["compact", "comfortable", "spacious"] as Density[]).map(
									(d) => (
										<DropdownMenuItem key={d} onClick={() => setDensity(d)}>
											{d === density ? "✓ " : "   "}
											{DENSITY_LABELS[d]}
										</DropdownMenuItem>
									),
								)}
							</DropdownMenuContent>
						</DropdownMenu>

						{/* Columns menu */}
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<button type="button" className={toolbarBtnClass}>
									<Columns3 className="h-3.5 w-3.5" />
									Columns
									<ChevronDown className="h-3 w-3 opacity-60" />
								</button>
							</DropdownMenuTrigger>
							<DropdownMenuContent
								align="start"
								className="max-h-80 overflow-y-auto"
							>
								<DropdownMenuLabel>Visible columns</DropdownMenuLabel>
								{parsed.columns.map((column) => {
									const visible = isColumnVisible(column);
									const pinned = getColumnPinned(column);
									return (
										<DropdownMenuSub key={column}>
											<DropdownMenuSubTrigger
												onClick={(e) => {
													// Click on the item toggles visibility without opening submenu.
													e.preventDefault();
													toggleColumnVisibility(column, !visible);
												}}
											>
												{visible ? (
													<Eye className="h-3.5 w-3.5 text-zinc-600 dark:text-zinc-300" />
												) : (
													<EyeOff className="h-3.5 w-3.5 text-zinc-400" />
												)}
												<span
													className={cn(
														"truncate",
														!visible && "text-zinc-400 line-through",
													)}
												>
													{column}
												</span>
												{pinned ? (
													<Pin className="ml-auto h-3 w-3 text-blue-500" />
												) : null}
											</DropdownMenuSubTrigger>
											<DropdownMenuPortal>
												<DropdownMenuSubContent>
													<DropdownMenuItem
														onClick={() =>
															toggleColumnVisibility(column, !visible)
														}
													>
														{visible ? "Hide column" : "Show column"}
													</DropdownMenuItem>
													<DropdownMenuItem
														onClick={() => copyColumnValues(column)}
													>
														<Copy className="h-3.5 w-3.5" /> Copy column values
													</DropdownMenuItem>
													<DropdownMenuSeparator />
													<DropdownMenuItem
														onClick={() => pinColumn(column, "left")}
													>
														<Pin className="h-3.5 w-3.5" /> Pin left
														{pinned === "left" ? " ✓" : ""}
													</DropdownMenuItem>
													<DropdownMenuItem
														onClick={() => pinColumn(column, "right")}
													>
														<Pin className="h-3.5 w-3.5 rotate-180" /> Pin right
														{pinned === "right" ? " ✓" : ""}
													</DropdownMenuItem>
													<DropdownMenuItem
														onClick={() => pinColumn(column, null)}
														disabled={!pinned}
													>
														<PinOff className="h-3.5 w-3.5" /> Unpin
													</DropdownMenuItem>
												</DropdownMenuSubContent>
											</DropdownMenuPortal>
										</DropdownMenuSub>
									);
								})}
								<DropdownMenuSeparator />
								<DropdownMenuItem
									onClick={() => {
										for (const c of parsed.columns) {
											toggleColumnVisibility(c, true);
										}
									}}
								>
									Show all
								</DropdownMenuItem>
							</DropdownMenuContent>
						</DropdownMenu>

						{/* Fit columns */}
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={handleFitColumns}
									className={toolbarBtnClass}
								>
									<TableProperties className="h-3.5 w-3.5" />
									Fit
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								Auto-size all columns to fit their content
							</TooltipContent>
						</Tooltip>

						{/* Floating filters */}
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={() => setShowFilters((value) => !value)}
									className={cn(
										toolbarBtnClass,
										showFilters &&
											"border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
									)}
								>
									Filters
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								{showFilters ? "Hide column filter row" : "Show column filter row"}
							</TooltipContent>
						</Tooltip>

						{/* Totals */}
						<DropdownMenu>
							<DropdownMenuTrigger asChild>
								<button type="button" className={toolbarBtnClass}>
									<Sigma className="h-3.5 w-3.5" />
									{totalsMode === "none" ? "Totals" : TOTALS_LABELS[totalsMode]}
									<ChevronDown className="h-3 w-3 opacity-60" />
								</button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="start">
								<DropdownMenuLabel>Footer row</DropdownMenuLabel>
								{TOTALS_MODES.map((m) => (
									<DropdownMenuItem key={m} onClick={() => setTotalsMode(m)}>
										{m === totalsMode ? "✓ " : "   "}
										{TOTALS_LABELS[m]}
									</DropdownMenuItem>
								))}
							</DropdownMenuContent>
						</DropdownMenu>

						{/* Heatmap */}
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={() => setHeatmap((v) => !v)}
									className={cn(
										toolbarBtnClass,
										heatmap &&
											"border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-950/50 dark:text-blue-300",
									)}
								>
									<Thermometer className="h-3.5 w-3.5" />
									Heatmap
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								Color numeric cells on a min→max scale
							</TooltipContent>
						</Tooltip>

						{/* TabICLv2 held-out row demo */}
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={handlePredictHeldOutRow}
									disabled={
										!hasData ||
										parsed.rows.length < 2 ||
										ltmPrediction.status === "loading"
									}
									className={toolbarBtnClass}
								>
									<Sparkles className="h-3.5 w-3.5" />
									{ltmPrediction.status === "loading"
										? "Predicting..."
										: "Predict held-out row"}
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom" className="max-w-xs">
								Use TabICLv2 to hold out one row, predict a numeric target from
								the remaining rows, then compare prediction vs actual.
							</TooltipContent>
						</Tooltip>

						{/* Reset */}
						<Tooltip>
							<TooltipTrigger asChild>
								<button
									type="button"
									onClick={handleResetView}
									className={toolbarBtnClass}
								>
									<RefreshCcw className="h-3.5 w-3.5" />
									Reset
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom">
								Clear sort, filters, column widths, visibility, and pinning
							</TooltipContent>
						</Tooltip>

						{/* Keyboard hints */}
						<Tooltip delayDuration={100}>
							<TooltipTrigger asChild>
								<button
									type="button"
									className={cn(toolbarBtnClass, "ml-auto")}
									aria-label="Keyboard shortcuts"
								>
									<Keyboard className="h-3.5 w-3.5" />
									<HelpCircle className="h-3 w-3 opacity-60" />
								</button>
							</TooltipTrigger>
							<TooltipContent side="bottom" className="max-w-xs p-0">
								<div className="p-3 text-xs leading-5">
									<div className="mb-1 font-semibold">Keyboard shortcuts</div>
									<ul className="space-y-1">
										<li>
											<kbd className="rounded bg-zinc-200 px-1 text-[10px] dark:bg-zinc-700">
												↑ ↓ ← →
											</kbd>{" "}
											Navigate cells
										</li>
										<li>
											<kbd className="rounded bg-zinc-200 px-1 text-[10px] dark:bg-zinc-700">
												Shift
											</kbd>
											+click header: add secondary sort
										</li>
										<li>
											<kbd className="rounded bg-zinc-200 px-1 text-[10px] dark:bg-zinc-700">
												Ctrl/⌘+C
											</kbd>{" "}
											Copy focused cell / selected rows
										</li>
										<li>Drag header edges to resize columns</li>
										<li>Drag headers to reorder columns</li>
										<li>Double-click row to open details</li>
									</ul>
								</div>
							</TooltipContent>
						</Tooltip>
					</div>
				) : null}

				{isExpanded && ltmPrediction.status !== "idle" ? (
					<div className="border-b border-zinc-200 bg-blue-50/60 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-blue-950/20 dark:text-zinc-200">
						<div className="flex flex-wrap items-center justify-between gap-2">
							<div>
								<span className="font-semibold">TabICLv2 held-out prediction</span>
								{ltmPrediction.status === "loading" ? (
									<span className="ml-2 text-zinc-500 dark:text-zinc-400">
										Predicting {ltmPrediction.targetColumn} from{" "}
										{ltmPrediction.nTrain} context rows...
									</span>
								) : null}
								{ltmPrediction.status === "error" ? (
									<span className="ml-2 text-red-600 dark:text-red-300">
										{ltmPrediction.error}
									</span>
								) : null}
								{ltmPrediction.status === "success" ? (
									<span className="ml-2">
										Target <code>{ltmPrediction.targetColumn}</code> for{" "}
										{ltmPrediction.heldOutRowLabel}: predicted{" "}
										<span className="font-semibold">
											{formatLtmValue(ltmPrediction.prediction)}
										</span>
										, actual{" "}
										<span className="font-semibold">
											{formatLtmValue(ltmPrediction.actual)}
										</span>{" "}
										({ltmPrediction.nTrain} context rows
										{ltmPrediction.provider ? `, ${ltmPrediction.provider}` : ""})
									</span>
								) : null}
							</div>
							<button
								type="button"
								onClick={() => setLtmPrediction({ status: "idle" })}
								className="rounded px-2 py-0.5 font-medium text-zinc-500 hover:bg-blue-100 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-blue-900/40 dark:hover:text-zinc-100"
							>
								Clear
							</button>
						</div>
					</div>
				) : null}

				{/* Grid */}
				{isExpanded ? (
					hasData ? (
						<div className={cn(isFullscreen && "flex-1 min-h-0")}>
							<AgGridReact
								theme={agTheme}
								rowData={parsed.rows}
								columnDefs={columnDefs}
								defaultColDef={defaultColDef}
								cellSelection
								domLayout={isFullscreen ? "normal" : "autoHeight"}
								containerStyle={
									isFullscreen ? { width: "100%", height: "100%" } : undefined
								}
								pagination
								paginationPageSize={DEFAULT_PAGE_SIZE}
								paginationPageSizeSelector={PAGE_SIZE_OPTIONS}
								rowSelection={{
									mode: "multiRow",
									checkboxes: true,
									headerCheckbox: true,
									enableClickSelection: false,
								}}
								rowHeight={densityCfg.rowHeight}
								headerHeight={densityCfg.headerHeight}
								floatingFiltersHeight={showFilters ? densityCfg.headerHeight : 0}
								pinnedBottomRowData={pinnedBottomRowData}
								tooltipShowDelay={300}
								animateRows={false}
								context={gridContext}
								onGridReady={handleGridReady}
								onCellFocused={handleCellFocused}
								onColumnMoved={handleColumnEvent}
								onColumnResized={handleColumnEvent}
								onColumnPinned={handleColumnEvent}
								onColumnVisible={handleColumnEvent}
								onSortChanged={handleSortChanged}
								onFilterChanged={handleFilterChanged}
								onSelectionChanged={handleSelectionChanged}
								onRowDoubleClicked={handleRowDoubleClicked}
							/>
						</div>
					) : (
						<div className="px-3 py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
							No rows
						</div>
					)
				) : null}
			</div>

			{/* Fullscreen backdrop (purely decorative — the wrapper itself is the modal) */}
			{isFullscreen ? (
				<button
					type="button"
					className="fixed inset-0 z-40 cursor-default bg-black/50"
					onClick={() => setIsFullscreen(false)}
					aria-label="Exit fullscreen"
				/>
			) : null}

			{/* Row detail side panel */}
			<Sheet
				open={detailRow !== null}
				onOpenChange={(open) => {
					if (!open) setDetailRow(null);
				}}
			>
				<SheetContent
					side="right"
					className="w-full overflow-y-auto sm:max-w-md"
				>
					<SheetTitle>Row details</SheetTitle>
					{detailRow ? (
						<div className="mt-4 space-y-2">
							{parsed.columns.map((column) => {
								const m = columnMeta[column];
								const kind = m?.kind ?? "text";
								const raw = detailRow[column];
								const formatted =
									raw == null || raw === "" ? "" : formatValueByKind(raw, kind);
								return (
									<div
										key={column}
										className="flex flex-col gap-0.5 border-b border-zinc-200 pb-2 text-sm last:border-b-0 dark:border-zinc-800"
									>
										<div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
											<span>{column}</span>
											<span className="text-zinc-300 dark:text-zinc-600">
												·
											</span>
											<span className="normal-case">
												{COLUMN_KIND_LABELS[kind]}
											</span>
										</div>
										<div className="break-words text-zinc-800 dark:text-zinc-200">
											{formatted || <span className="text-zinc-400">—</span>}
										</div>
									</div>
								);
							})}
						</div>
					) : null}
				</SheetContent>
			</Sheet>
		</TooltipProvider>
	);
}

// -----------------------------------------------------------------------------
// Local helpers
// -----------------------------------------------------------------------------

function parseNumericSafe(value: unknown): number | null {
	if (typeof value === "number") return Number.isFinite(value) ? value : null;
	if (typeof value !== "string") return null;
	const num = Number(value.replace(/,/g, "").replace(/^[$€£¥₹]\s?/, ""));
	return Number.isFinite(num) ? num : null;
}

function chooseLtmRegressionTarget(
	columns: string[],
	columnMeta: Record<string, ColumnMeta>,
	rows: TableDataRow[],
	heldOutRow: TableDataRow,
): string | null {
	const candidates = columns
		.map((column) => {
			const kind = columnMeta[column]?.kind;
			if (!kind || !isNumericKind(kind)) return null;
			if (coerceForLtm(heldOutRow[column], kind) == null) return null;
			const labeledRows = rows.filter(
				(row) => row !== heldOutRow && coerceForLtm(row[column], kind) != null,
			);
			if (labeledRows.length < 1) return null;
			return {
				column,
				score: scoreLtmTargetColumn(column, kind),
			};
		})
		.filter((item): item is { column: string; score: number } => item !== null)
		.sort((a, b) => b.score - a.score);

	return candidates[0]?.column ?? null;
}

function isNumericKind(kind: ColumnKind): boolean {
	return (
		kind === "integer" ||
		kind === "number" ||
		kind === "currency" ||
		kind === "percent"
	);
}

function scoreLtmTargetColumn(column: string, kind: ColumnKind): number {
	const lower = column.toLowerCase();
	let score = kind === "currency" ? 60 : kind === "number" ? 50 : kind === "percent" ? 45 : 30;
	if (/average|avg|mean|per[_\s-]?patient/.test(lower)) score += 80;
	if (/spend|spending|cost|amount|paid|payment|allowed|charge/.test(lower)) score += 60;
	if (/rate|ratio|percent|share/.test(lower)) score += 35;
	if (/score|risk|index/.test(lower)) score += 25;
	if (/count|total|number|qty|quantity|utilization/.test(lower)) score -= 15;
	if (/(^|_)id$|identifier|uuid/.test(lower)) score -= 1000;
	return score;
}

function buildLtmRequestRow(
	row: TableDataRow,
	columns: string[],
	columnMeta: Record<string, ColumnMeta>,
	targetColumn: string,
	includeTarget: boolean,
): TableDataRow {
	const result: TableDataRow = {};
	for (const column of columns) {
		if (column === targetColumn && !includeTarget) continue;
		result[column] = coerceForLtm(row[column], columnMeta[column]?.kind);
	}
	return result;
}

function coerceForLtm(value: unknown, kind: ColumnKind | undefined): unknown {
	if (kind && isNumericKind(kind)) {
		const coerced = coerceValue(value, kind);
		return typeof coerced === "number" && Number.isFinite(coerced) ? coerced : null;
	}
	if (value == null || value === "") return null;
	if (value instanceof Date) return value.toISOString();
	return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
		? value
		: String(value);
}

function describeHeldOutRow(
	row: TableDataRow,
	columns: string[],
	targetColumn: string,
): string {
	const labelColumns = columns.filter((column) => {
		if (column === targetColumn) return false;
		const lower = column.toLowerCase();
		return /state|type|category|segment|class|plan|insurance|drug|diabetes/.test(lower);
	});
	const parts = (labelColumns.length > 0 ? labelColumns : columns.filter((c) => c !== targetColumn))
		.slice(0, 3)
		.map((column) => `${column}=${String(row[column] ?? "NA")}`);
	return parts.length > 0 ? parts.join(", ") : "selected row";
}

function formatLtmValue(value: unknown): string {
	if (typeof value === "number") {
		return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
	}
	if (value == null || value === "") return "NA";
	return String(value);
}
