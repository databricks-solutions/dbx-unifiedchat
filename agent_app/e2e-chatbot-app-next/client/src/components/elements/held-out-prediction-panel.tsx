"use client";

import { Search, Sparkles, Target, Wand2 } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
	type ColumnKind,
	type ColumnMeta,
	type NormalizedTable,
	type TableData,
	type TableDataRow,
	coerceValue,
	computeColumnMeta,
	formatValueByKind,
	normalizeTableData,
} from "./paginated-table-utils";

// -----------------------------------------------------------------------------
// Types
// -----------------------------------------------------------------------------

type LtmTask = "regression" | "classification";

type TabularPrediction = {
	prediction: unknown;
	probabilities?: Record<string, number>;
};

type TabularPredictResponse = {
	success: boolean;
	provider?: string;
	model_checkpoint?: string;
	n_train?: number;
	predictions?: TabularPrediction[];
	error?: string;
};

type TargetResult =
	| {
			status: "ok";
			task: LtmTask;
			nTrain: number;
			provider?: string;
			modelCheckpoint?: string;
			predictions: unknown[];
	  }
	| { status: "error"; task: LtmTask; error: string };

type RunResults = {
	heldOutIndexes: number[];
	featureColumns: string[];
	targetColumns: string[];
	byTarget: Record<string, TargetResult>;
};

type RunState =
	| { status: "idle" }
	| { status: "loading" }
	| { status: "success"; results: RunResults }
	| { status: "error"; error: string };

type ColMode = "fit" | "compact";
type RowMode = "comfortable" | "compact";

type Density = {
	headerPadY: string;
	colPadX: string;
	rowPadY: string;
	valueClass: string;
};

// -----------------------------------------------------------------------------
// Component
// -----------------------------------------------------------------------------

export function HeldOutPredictionPanel({ tableData }: { tableData: TableData }) {
	const parsed = useMemo<NormalizedTable>(
		() => normalizeTableData(tableData),
		[tableData],
	);
	const columnMeta = useMemo<Record<string, ColumnMeta>>(
		() => computeColumnMeta(parsed.rows, parsed.columns),
		[parsed.rows, parsed.columns],
	);

	const defaultTarget = useMemo(() => {
		const scored = parsed.columns
			.filter((column) => isNumericKind(columnMeta[column]?.kind))
			.map((column) => ({
				column,
				score: scoreLtmTargetColumn(column, columnMeta[column]?.kind ?? "number"),
			}))
			.sort((a, b) => b.score - a.score);
		return scored[0]?.column ?? null;
	}, [parsed.columns, columnMeta]);

	const [targetColumns, setTargetColumns] = useState<Set<string>>(
		() => new Set(defaultTarget ? [defaultTarget] : []),
	);
	const [featureColumns, setFeatureColumns] = useState<Set<string>>(
		() => new Set(parsed.columns.filter((column) => column !== defaultTarget)),
	);
	const [heldOutIndexes, setHeldOutIndexes] = useState<Set<number>>(() =>
		parsed.rows.length > 0 ? new Set([0]) : new Set(),
	);
	const [rowQuery, setRowQuery] = useState("");
	const [run, setRun] = useState<RunState>({ status: "idle" });

	// Selecting a column as a target removes it from the feature set; clearing a
	// target makes it available as a feature again (but does not auto-select it).
	const toggleTarget = useCallback((column: string) => {
		setTargetColumns((prev) => {
			const next = new Set(prev);
			if (next.has(column)) next.delete(column);
			else next.add(column);
			return next;
		});
		setFeatureColumns((prev) => {
			const next = new Set(prev);
			next.delete(column);
			return next;
		});
	}, []);

	const toggleFeature = useCallback((column: string) => {
		setFeatureColumns((prev) => {
			const next = new Set(prev);
			if (next.has(column)) next.delete(column);
			else next.add(column);
			return next;
		});
	}, []);

	const selectAllTargets = useCallback(() => {
		setTargetColumns(new Set(parsed.columns));
		setFeatureColumns(new Set());
	}, [parsed.columns]);

	const clearTargets = useCallback(() => {
		setTargetColumns(new Set());
	}, []);

	const selectAllFeatures = useCallback(() => {
		setFeatureColumns(
			new Set(parsed.columns.filter((column) => !targetColumns.has(column))),
		);
	}, [parsed.columns, targetColumns]);

	const clearFeatures = useCallback(() => {
		setFeatureColumns(new Set());
	}, []);

	const featureCandidates = useMemo(
		() => parsed.columns.filter((column) => !targetColumns.has(column)),
		[parsed.columns, targetColumns],
	);

	const filteredRowIndexes = useMemo(() => {
		const query = rowQuery.trim().toLowerCase();
		if (!query) return parsed.rows.map((_, index) => index);
		return parsed.rows
			.map((_, index) => index)
			.filter((index) => {
				const row = parsed.rows[index];
				return parsed.columns.some((column) => {
					const formatted = formatValueByKind(
						row[column],
						columnMeta[column]?.kind ?? "text",
					);
					return formatted.toLowerCase().includes(query);
				});
			});
	}, [rowQuery, parsed.rows, parsed.columns, columnMeta]);

	const allFilteredSelected =
		filteredRowIndexes.length > 0 &&
		filteredRowIndexes.every((index) => heldOutIndexes.has(index));

	const toggleAllFiltered = useCallback(() => {
		setHeldOutIndexes((prev) => {
			const next = new Set(prev);
			const everySelected =
				filteredRowIndexes.length > 0 &&
				filteredRowIndexes.every((index) => next.has(index));
			if (everySelected) {
				for (const index of filteredRowIndexes) next.delete(index);
			} else {
				for (const index of filteredRowIndexes) next.add(index);
			}
			return next;
		});
	}, [filteredRowIndexes]);

	const toggleRow = useCallback((index: number) => {
		setHeldOutIndexes((prev) => {
			const next = new Set(prev);
			if (next.has(index)) next.delete(index);
			else next.add(index);
			return next;
		});
	}, []);

	const clearHeldOut = useCallback(() => {
		setHeldOutIndexes(new Set());
	}, []);

	const selectedFeatures = useMemo(
		() => parsed.columns.filter((column) => featureColumns.has(column)),
		[parsed.columns, featureColumns],
	);
	const selectedTargets = useMemo(
		() => parsed.columns.filter((column) => targetColumns.has(column)),
		[parsed.columns, targetColumns],
	);
	const contextRowCount = parsed.rows.length - heldOutIndexes.size;

	const canRun =
		selectedTargets.length > 0 &&
		selectedFeatures.length > 0 &&
		heldOutIndexes.size > 0 &&
		contextRowCount > 0 &&
		run.status !== "loading";

	const runPrediction = useCallback(async () => {
		const features = parsed.columns.filter((column) => featureColumns.has(column));
		const targets = parsed.columns.filter((column) => targetColumns.has(column));
		const heldOut = [...heldOutIndexes].sort((a, b) => a - b);

		if (
			features.length === 0 ||
			targets.length === 0 ||
			heldOut.length === 0 ||
			parsed.rows.length - heldOut.length < 1
		) {
			return;
		}

		setRun({ status: "loading" });

		const predictBaseRows = heldOut.map((index) => parsed.rows[index]);

		const entries = await Promise.all(
			targets.map(async (target): Promise<[string, TargetResult]> => {
				const kind = columnMeta[target]?.kind;
				const task = detectTask(kind);

				const trainRows = parsed.rows
					.filter((_, index) => !heldOutIndexes.has(index))
					.map((row) => buildLtmRequestRow(row, features, columnMeta, target, true))
					.filter((row) => row[target] != null);

				if (trainRows.length < 1) {
					return [
						target,
						{
							status: "error",
							task,
							error: `No labeled context rows remain for "${target}".`,
						},
					];
				}

				const predictRows = predictBaseRows.map((row) =>
					buildLtmRequestRow(row, features, columnMeta, target, false),
				);

				try {
					const response = await fetch("/api/tabular/predict", {
						method: "POST",
						credentials: "include",
						headers: { "Content-Type": "application/json" },
						body: JSON.stringify({
							task,
							feature_columns: features,
							target_column: target,
							train_rows: trainRows,
							predict_rows: predictRows,
						}),
					});

					const payload = (await response.json()) as TabularPredictResponse;
					if (!response.ok || !payload.success) {
						throw new Error(
							payload.error || `TabICLv2 request failed (${response.status})`,
						);
					}

					return [
						target,
						{
							status: "ok",
							task,
							nTrain: payload.n_train ?? trainRows.length,
							provider: payload.provider,
							modelCheckpoint: payload.model_checkpoint,
							predictions: (payload.predictions ?? []).map((p) => p.prediction),
						},
					];
				} catch (error) {
					return [
						target,
						{
							status: "error",
							task,
							error: error instanceof Error ? error.message : String(error),
						},
					];
				}
			}),
		);

		setRun({
			status: "success",
			results: {
				heldOutIndexes: heldOut,
				featureColumns: features,
				targetColumns: targets,
				byTarget: Object.fromEntries(entries),
			},
		});
	}, [parsed.columns, parsed.rows, featureColumns, targetColumns, heldOutIndexes, columnMeta]);

	return (
		<div className="rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-700 dark:bg-zinc-900">
			{/* Header */}
			<div className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-200 bg-zinc-50/60 px-4 py-3 dark:border-zinc-700 dark:bg-zinc-800/40">
				<div className="flex items-center gap-2">
					<div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-50 text-blue-600 dark:bg-blue-950 dark:text-blue-300">
						<Sparkles className="h-4 w-4" />
					</div>
					<div>
						<div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
							Held-out prediction (TabICLv2)
						</div>
						<div className="text-xs text-zinc-500 dark:text-zinc-400">
							Hold out rows, pick features and targets, then compare predicted vs
							actual.
						</div>
					</div>
				</div>
				<div className="flex items-center gap-2">
					{run.status === "success" ? (
						<Button
							type="button"
							variant="ghost"
							size="sm"
							onClick={() => setRun({ status: "idle" })}
						>
							Clear results
						</Button>
					) : null}
					<Button
						type="button"
						size="sm"
						onClick={runPrediction}
						disabled={!canRun}
					>
						<Wand2 className="h-4 w-4" />
						{run.status === "loading" ? "Predicting..." : "Run prediction"}
					</Button>
				</div>
			</div>

			{/* Config */}
			<div className="grid gap-4 p-4 lg:grid-cols-3">
				<ConfigSection
					title="Target columns"
					hint={`${selectedTargets.length} selected`}
					action={
						<SelectAllControls
							onSelectAll={selectAllTargets}
							onClear={clearTargets}
							allSelected={selectedTargets.length === parsed.columns.length}
							noneSelected={selectedTargets.length === 0}
						/>
					}
				>
					<div className="max-h-56 space-y-1 overflow-y-auto pr-1">
						{parsed.columns.map((column) => {
							const task = detectTask(columnMeta[column]?.kind);
							const selected = targetColumns.has(column);
							return (
								<label
									key={column}
									className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
								>
									<input
										type="checkbox"
										checked={selected}
										onChange={() => toggleTarget(column)}
										className="accent-amber-600"
									/>
									<Target className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
									<span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-200">
										{column}
									</span>
									<TaskBadge task={task} />
								</label>
							);
						})}
					</div>
				</ConfigSection>

				<ConfigSection
					title="Feature columns"
					hint={`${selectedFeatures.length} selected`}
					action={
						<SelectAllControls
							onSelectAll={selectAllFeatures}
							onClear={clearFeatures}
							allSelected={
								featureCandidates.length > 0 &&
								selectedFeatures.length === featureCandidates.length
							}
							noneSelected={selectedFeatures.length === 0}
						/>
					}
				>
					<div className="max-h-56 space-y-1 overflow-y-auto pr-1">
						{featureCandidates.length === 0 ? (
							<div className="px-2 py-1 text-xs text-zinc-400">
								All columns are selected as targets.
							</div>
						) : null}
						{featureCandidates.map((column) => {
							const selected = featureColumns.has(column);
							return (
								<label
									key={column}
									className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
								>
									<input
										type="checkbox"
										checked={selected}
										onChange={() => toggleFeature(column)}
										className="accent-blue-600"
									/>
									<span className="min-w-0 flex-1 truncate text-zinc-800 dark:text-zinc-200">
										{column}
									</span>
									<span className="text-[11px] uppercase tracking-wide text-zinc-400">
										{columnMeta[column]?.kind ?? "text"}
									</span>
								</label>
							);
						})}
					</div>
				</ConfigSection>

				<ConfigSection
					title="Hold-out rows"
					hint={`${heldOutIndexes.size} held out · ${contextRowCount} context`}
					action={
						<button
							type="button"
							onClick={clearHeldOut}
							disabled={heldOutIndexes.size === 0}
							className="rounded px-1.5 py-0.5 text-[11px] font-medium text-zinc-500 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800"
						>
							Clear
						</button>
					}
				>
					<div className="relative mb-2 flex items-center">
						<Search className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-zinc-400" />
						<input
							type="text"
							value={rowQuery}
							onChange={(event) => setRowQuery(event.target.value)}
							placeholder="Search rows…"
							className="w-full rounded-md border border-zinc-200 bg-white py-1 pl-7 pr-2 text-xs text-zinc-700 placeholder-zinc-400 shadow-sm focus:border-zinc-400 focus:outline-none dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
						/>
					</div>
					<div className="max-h-44 overflow-auto rounded-md border border-zinc-200 dark:border-zinc-700">
						<table className="w-full border-collapse text-xs">
							<thead className="sticky top-0 bg-zinc-50 dark:bg-zinc-800">
								<tr>
									<th className="w-8 px-2 py-1 text-left">
										<input
											type="checkbox"
											checked={allFilteredSelected}
											onChange={toggleAllFiltered}
											aria-label="Select all rows"
										/>
									</th>
									<th className="px-2 py-1 text-left font-medium text-zinc-500">
										#
									</th>
									{parsed.columns.slice(0, 4).map((column) => (
										<th
											key={column}
											className="px-2 py-1 text-left font-medium text-zinc-500"
										>
											{column}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								{filteredRowIndexes.map((index) => {
									const row = parsed.rows[index];
									const selected = heldOutIndexes.has(index);
									return (
										<tr
											key={index}
											className={cn(
												"cursor-pointer border-t border-zinc-100 dark:border-zinc-800",
												selected
													? "bg-blue-50 dark:bg-blue-950/30"
													: "hover:bg-zinc-50 dark:hover:bg-zinc-800/50",
											)}
											tabIndex={0}
											onClick={() => toggleRow(index)}
											onKeyDown={(event) => {
												if (event.key === "Enter" || event.key === " ") {
													event.preventDefault();
													toggleRow(index);
												}
											}}
										>
											<td className="px-2 py-1">
												<input
													type="checkbox"
													checked={selected}
													onChange={() => toggleRow(index)}
													onClick={(event) => event.stopPropagation()}
													aria-label={`Hold out row ${index + 1}`}
												/>
											</td>
											<td className="px-2 py-1 text-zinc-400">{index + 1}</td>
											{parsed.columns.slice(0, 4).map((column) => (
												<td
													key={column}
													className="max-w-[10rem] truncate px-2 py-1 text-zinc-700 dark:text-zinc-300"
												>
													{formatValueByKind(
														row[column],
														columnMeta[column]?.kind ?? "text",
													) || "—"}
												</td>
											))}
										</tr>
									);
								})}
							</tbody>
						</table>
					</div>
				</ConfigSection>
			</div>

			{/* Results */}
			{run.status === "loading" ? (
				<div className="border-t border-zinc-200 px-4 py-6 text-center text-sm text-zinc-500 dark:border-zinc-700 dark:text-zinc-400">
					Running TabICLv2 in-context inference for {selectedTargets.length} target
					{selectedTargets.length === 1 ? "" : "s"}…
				</div>
			) : null}

			{run.status === "success" ? (
				<ResultsView results={run.results} parsed={parsed} columnMeta={columnMeta} />
			) : null}
		</div>
	);
}

export default HeldOutPredictionPanel;

// -----------------------------------------------------------------------------
// Results view
// -----------------------------------------------------------------------------

function ResultsView({
	results,
	parsed,
	columnMeta,
}: {
	results: RunResults;
	parsed: NormalizedTable;
	columnMeta: Record<string, ColumnMeta>;
}) {
	const { heldOutIndexes, featureColumns, targetColumns, byTarget } = results;

	const failedTargets = targetColumns.filter(
		(target) => byTarget[target]?.status === "error",
	);

	const provider = (() => {
		for (const target of targetColumns) {
			const result = byTarget[target];
			if (result?.status === "ok" && result.provider) return result.provider;
		}
		return undefined;
	})();
	const nContext = (() => {
		for (const target of targetColumns) {
			const result = byTarget[target];
			if (result?.status === "ok") return result.nTrain;
		}
		return undefined;
	})();

	const [colMode, setColMode] = useState<ColMode>("fit");
	const [rowMode, setRowMode] = useState<RowMode>("comfortable");

	const density: Density = {
		headerPadY: "py-1.5",
		colPadX: colMode === "compact" ? "px-2" : "px-3",
		rowPadY: rowMode === "compact" ? "py-1" : "py-2.5",
		valueClass:
			colMode === "compact" ? "max-w-[10rem] truncate" : "whitespace-nowrap",
	};

	return (
		<div className="border-t border-zinc-200 dark:border-zinc-700">
			{/* Legend + layout controls */}
			<div className="flex flex-wrap items-center justify-between gap-3 px-4 py-2 text-[11px] text-zinc-500 dark:text-zinc-400">
				<div className="flex flex-wrap items-center gap-3">
					<LegendSwatch className="bg-blue-100 dark:bg-blue-950/50" label="Feature" />
					<LegendSwatch className="bg-amber-100 dark:bg-amber-950/40" label="Actual" />
					<LegendSwatch
						className="bg-violet-100 dark:bg-violet-950/40"
						label="Predicted"
					/>
					<LegendSwatch
						className="bg-emerald-100 dark:bg-emerald-950/40"
						label="Close / match"
					/>
					<LegendSwatch
						className="bg-rose-100 dark:bg-rose-950/40"
						label="Far / mismatch"
					/>
				</div>
				<div className="flex items-center gap-3">
					<DensityToggle
						label="Columns"
						value={colMode}
						options={[
							{ value: "fit", label: "Fit" },
							{ value: "compact", label: "Compact" },
						]}
						onChange={setColMode}
					/>
					<DensityToggle
						label="Rows"
						value={rowMode}
						options={[
							{ value: "comfortable", label: "Fit" },
							{ value: "compact", label: "Compact" },
						]}
						onChange={setRowMode}
					/>
				</div>
			</div>

			{failedTargets.length > 0 ? (
				<div className="mx-4 mb-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-300">
					{failedTargets.map((target) => {
						const result = byTarget[target];
						return (
							<div key={target}>
								<span className="font-semibold">{target}</span>:{" "}
								{result?.status === "error" ? result.error : "failed"}
							</div>
						);
					})}
				</div>
			) : null}

			<div className="overflow-x-auto px-4 pb-3">
				<table className="w-full border-collapse text-xs">
					<thead>
						<tr>
							<th
								rowSpan={2}
								className={cn(
									"border-b border-zinc-200 text-left font-medium text-zinc-500 dark:border-zinc-700",
									density.colPadX,
									density.headerPadY,
								)}
							>
								#
							</th>
							{/* Results first (leftmost): one group per target */}
							{targetColumns.map((target) => {
								const result = byTarget[target];
								return (
									<th
										key={target}
										colSpan={3}
										className={cn(
											"border-b border-l border-zinc-200 bg-amber-50 text-center font-semibold text-amber-700 dark:border-zinc-700 dark:bg-amber-950/30 dark:text-amber-300",
											density.colPadX,
											density.headerPadY,
										)}
									>
										{target}{" "}
										<span className="font-normal opacity-70">
											({result?.task ?? "?"})
										</span>
									</th>
								);
							})}
							{featureColumns.length > 0 ? (
								<th
									colSpan={featureColumns.length}
									className={cn(
										"border-b border-l border-zinc-200 bg-blue-50 text-center font-semibold text-blue-700 dark:border-zinc-700 dark:bg-blue-950/40 dark:text-blue-300",
										density.colPadX,
										density.headerPadY,
									)}
								>
									Features
								</th>
							) : null}
						</tr>
						<tr>
							{targetColumns.map((target) => {
								const task = byTarget[target]?.task ?? "regression";
								return (
									<FragmentHeaders key={target} task={task} density={density} />
								);
							})}
							{featureColumns.map((column) => (
								<th
									key={column}
									className={cn(
										"border-b border-l border-zinc-200 bg-blue-50/60 text-left font-medium text-blue-700 dark:border-zinc-700 dark:bg-blue-950/20 dark:text-blue-300",
										density.colPadX,
										"py-1",
										density.valueClass,
									)}
								>
									{column}
								</th>
							))}
						</tr>
					</thead>
					<tbody>
						{heldOutIndexes.map((rowIndex, position) => {
							const row = parsed.rows[rowIndex];
							return (
								<tr
									key={rowIndex}
									className="border-b border-zinc-100 dark:border-zinc-800"
								>
									<td
										className={cn(
											"text-zinc-400",
											density.colPadX,
											density.rowPadY,
										)}
									>
										{rowIndex + 1}
									</td>
									{targetColumns.map((target) => (
										<TargetCells
											key={target}
											target={target}
											position={position}
											actualRaw={row[target]}
											kind={columnMeta[target]?.kind ?? "text"}
											result={byTarget[target]}
											density={density}
										/>
									))}
									{featureColumns.map((column) => (
										<td
											key={column}
											className={cn(
												"border-l border-zinc-100 text-zinc-700 dark:border-zinc-800 dark:text-zinc-300",
												density.colPadX,
												density.rowPadY,
												density.valueClass,
											)}
										>
											{formatValueByKind(
												row[column],
												columnMeta[column]?.kind ?? "text",
											) || "—"}
										</td>
									))}
								</tr>
							);
						})}
					</tbody>
				</table>
			</div>

			{provider ? (
				<div className="px-4 pb-3 text-[11px] text-zinc-400">
					Provider: {provider}
					{nContext != null ? ` · ${nContext} context rows` : ""}
				</div>
			) : null}
		</div>
	);
}

function FragmentHeaders({ task, density }: { task: LtmTask; density: Density }) {
	return (
		<>
			<th
				className={cn(
					"border-b border-l border-zinc-200 bg-amber-50/50 text-right font-medium text-amber-700 dark:border-zinc-700 dark:bg-amber-950/20 dark:text-amber-300",
					density.colPadX,
					"py-1",
				)}
			>
				Actual
			</th>
			<th
				className={cn(
					"border-b border-zinc-200 bg-violet-50/60 text-right font-medium text-violet-700 dark:border-zinc-700 dark:bg-violet-950/20 dark:text-violet-300",
					density.colPadX,
					"py-1",
				)}
			>
				Predicted
			</th>
			<th
				className={cn(
					"border-b border-zinc-200 text-right font-medium text-zinc-500 dark:border-zinc-700",
					density.colPadX,
					"py-1",
				)}
			>
				{task === "classification" ? "Result" : "Δ"}
			</th>
		</>
	);
}

function TargetCells({
	target,
	position,
	actualRaw,
	kind,
	result,
	density,
}: {
	target: string;
	position: number;
	actualRaw: unknown;
	kind: ColumnKind;
	result: TargetResult | undefined;
	density: Density;
}) {
	void target;
	const pad = cn(density.colPadX, density.rowPadY);
	if (!result || result.status === "error") {
		return (
			<>
				<td
					className={cn(
						"border-l border-zinc-100 text-right text-zinc-400 dark:border-zinc-800",
						pad,
					)}
				>
					{formatValueByKind(actualRaw, kind) || "—"}
				</td>
				<td className={cn("text-right text-zinc-400", pad)} colSpan={2}>
					n/a
				</td>
			</>
		);
	}

	const task = result.task;
	const predictedRaw = result.predictions[position];
	const actualText = formatValueByKind(actualRaw, kind) || "—";
	const predictedText = formatPrediction(predictedRaw, kind);

	if (task === "classification") {
		const actualStr = actualRaw == null ? null : String(actualRaw);
		const predStr = predictedRaw == null ? null : String(predictedRaw);
		const known = actualStr != null && actualStr !== "";
		const match = known ? actualStr === predStr : null;
		return (
			<>
				<td
					className={cn(
						"border-l border-zinc-100 bg-amber-50/40 text-right text-zinc-700 dark:border-zinc-800 dark:bg-amber-950/10 dark:text-zinc-200",
						pad,
					)}
				>
					{actualText}
				</td>
				<td
					className={cn(
						"bg-violet-50/40 text-right font-medium text-zinc-900 dark:bg-violet-950/10 dark:text-zinc-100",
						pad,
					)}
				>
					{predictedText}
				</td>
				<td
					className={cn(
						"text-right font-medium",
						pad,
						match == null
							? "text-zinc-400"
							: match
								? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300"
								: "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-300",
					)}
				>
					{match == null ? "—" : match ? "✓ match" : "✗ miss"}
				</td>
			</>
		);
	}

	// Regression
	const actualNum = toFiniteNumber(coerceValue(actualRaw, kind));
	const predNum = toFiniteNumber(predictedRaw);
	const delta =
		actualNum != null && predNum != null ? predNum - actualNum : null;
	const pctError =
		actualNum != null && predNum != null && actualNum !== 0
			? Math.abs(delta as number) / Math.abs(actualNum)
			: null;
	const closeness = closenessClass(pctError);

	return (
		<>
			<td
				className={cn(
					"border-l border-zinc-100 bg-amber-50/40 text-right text-zinc-700 dark:border-zinc-800 dark:bg-amber-950/10 dark:text-zinc-200",
					pad,
				)}
			>
				{actualText}
			</td>
			<td
				className={cn(
					"bg-violet-50/40 text-right font-medium text-zinc-900 dark:bg-violet-950/10 dark:text-zinc-100",
					pad,
				)}
			>
				{predictedText}
			</td>
			<td className={cn("text-right font-medium", pad, closeness)}>
				{delta == null ? (
					"—"
				) : (
					<>
						{delta >= 0 ? "+" : ""}
						{formatPrediction(delta, kind)}
						{pctError != null ? (
							<span className="ml-1 opacity-70">
								({(pctError * 100).toFixed(1)}%)
							</span>
						) : null}
					</>
				)}
			</td>
		</>
	);
}

// -----------------------------------------------------------------------------
// Small presentational helpers
// -----------------------------------------------------------------------------

function ConfigSection({
	title,
	hint,
	action,
	children,
}: {
	title: string;
	hint?: string;
	action?: React.ReactNode;
	children: React.ReactNode;
}) {
	return (
		<section className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800">
			<div className="mb-2 flex items-center justify-between gap-2">
				<h4 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
					{title}
				</h4>
				<div className="flex items-center gap-2">
					{action}
					{hint ? (
						<span className="text-[11px] text-zinc-400">{hint}</span>
					) : null}
				</div>
			</div>
			{children}
		</section>
	);
}

function SelectAllControls({
	onSelectAll,
	onClear,
	allSelected,
	noneSelected,
}: {
	onSelectAll: () => void;
	onClear: () => void;
	allSelected: boolean;
	noneSelected: boolean;
}) {
	return (
		<div className="flex items-center gap-1 text-[11px]">
			<button
				type="button"
				onClick={onSelectAll}
				disabled={allSelected}
				className="rounded px-1.5 py-0.5 font-medium text-blue-600 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-40 dark:text-blue-300 dark:hover:bg-blue-950/40"
			>
				Select all
			</button>
			<span className="text-zinc-300 dark:text-zinc-600">·</span>
			<button
				type="button"
				onClick={onClear}
				disabled={noneSelected}
				className="rounded px-1.5 py-0.5 font-medium text-zinc-500 hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:text-zinc-400 dark:hover:bg-zinc-800"
			>
				Clear
			</button>
		</div>
	);
}

function TaskBadge({ task }: { task: LtmTask }) {
	return (
		<span
			className={cn(
				"rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide",
				task === "regression"
					? "bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300"
					: "bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-950/40 dark:text-fuchsia-300",
			)}
		>
			{task === "regression" ? "reg" : "clf"}
		</span>
	);
}

function DensityToggle<T extends string>({
	label,
	value,
	options,
	onChange,
}: {
	label: string;
	value: T;
	options: Array<{ value: T; label: string }>;
	onChange: (value: T) => void;
}) {
	const currentIndex = Math.max(
		0,
		options.findIndex((option) => option.value === value),
	);
	const current = options[currentIndex] ?? options[0];
	const next = options[(currentIndex + 1) % options.length];
	const active = currentIndex !== 0;

	return (
		<button
			type="button"
			onClick={() => onChange(next.value)}
			title={`${label}: ${current.label} (click for ${next.label})`}
			className={cn(
				"inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-medium transition-colors",
				active
					? "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-950/50 dark:text-blue-300"
					: "border-zinc-200 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800",
			)}
		>
			<span className="text-zinc-400 dark:text-zinc-500">{label}:</span>
			{current.label}
		</button>
	);
}

function LegendSwatch({ className, label }: { className: string; label: string }) {
	return (
		<span className="inline-flex items-center gap-1.5">
			<span className={cn("inline-block h-3 w-3 rounded-sm", className)} />
			{label}
		</span>
	);
}

// -----------------------------------------------------------------------------
// Pure helpers
// -----------------------------------------------------------------------------

function isNumericKind(kind: ColumnKind | undefined): boolean {
	return (
		kind === "integer" ||
		kind === "number" ||
		kind === "currency" ||
		kind === "percent"
	);
}

function detectTask(kind: ColumnKind | undefined): LtmTask {
	return isNumericKind(kind) ? "regression" : "classification";
}

function scoreLtmTargetColumn(column: string, kind: ColumnKind): number {
	const lower = column.toLowerCase();
	let score =
		kind === "currency" ? 60 : kind === "number" ? 50 : kind === "percent" ? 45 : 30;
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
	featureColumns: string[],
	columnMeta: Record<string, ColumnMeta>,
	targetColumn: string,
	includeTarget: boolean,
): TableDataRow {
	const result: TableDataRow = {};
	for (const column of featureColumns) {
		result[column] = coerceForLtm(row[column], columnMeta[column]?.kind);
	}
	if (includeTarget) {
		result[targetColumn] = coerceForLtm(row[targetColumn], columnMeta[targetColumn]?.kind);
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

function toFiniteNumber(value: unknown): number | null {
	if (typeof value === "number") return Number.isFinite(value) ? value : null;
	if (typeof value === "string") {
		const num = Number(value.replace(/,/g, "").replace(/^[$€£¥₹]\s?/, ""));
		return Number.isFinite(num) ? num : null;
	}
	if (value instanceof Date) return value.getTime();
	return null;
}

function formatPrediction(value: unknown, kind: ColumnKind): string {
	if (value == null || value === "") return "—";
	if (isNumericKind(kind) && typeof value === "number") {
		return formatValueByKind(value, kind) || String(value);
	}
	if (typeof value === "number") {
		return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
	}
	return String(value);
}

function closenessClass(pctError: number | null): string {
	if (pctError == null) return "text-zinc-400";
	if (pctError <= 0.1)
		return "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/30 dark:text-emerald-300";
	if (pctError <= 0.25)
		return "bg-amber-50 text-amber-700 dark:bg-amber-950/30 dark:text-amber-300";
	return "bg-rose-50 text-rose-700 dark:bg-rose-950/30 dark:text-rose-300";
}
