'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export type ExecutionMode = 'parallel' | 'sequential';
export type SynthesisRoute = 'auto' | 'table_route' | 'genie_route';
export type ClarificationSensitivity =
  | 'off'
  | 'low'
  | 'medium'
  | 'high'
  | 'on';

export interface AgentSettings {
  executionMode: ExecutionMode;
  synthesisRoute: SynthesisRoute;
  clarificationSensitivity: ClarificationSensitivity;
  countOnly: boolean;
}

const LOCKED_EXECUTION_MODE: ExecutionMode = 'parallel';
const LOCKED_COUNT_ONLY = true;

function normalizeExecutionMode(
  value: AgentSettings['executionMode'] | undefined,
): AgentSettings['executionMode'] {
  return LOCKED_EXECUTION_MODE;
}

function normalizeSynthesisRoute(
  value: AgentSettings['synthesisRoute'] | undefined,
): AgentSettings['synthesisRoute'] {
  return value === 'table_route' || value === 'genie_route' ? value : 'auto';
}

function normalizeClarificationSensitivity(
  value: AgentSettings['clarificationSensitivity'] | undefined,
): AgentSettings['clarificationSensitivity'] {
  return value === 'off' || value === 'low' || value === 'high' || value === 'on'
    ? value
    : 'medium';
}

function normalizeSettings(
  settings?: Partial<AgentSettings>,
): AgentSettings {
  return {
    executionMode: normalizeExecutionMode(settings?.executionMode),
    synthesisRoute: normalizeSynthesisRoute(settings?.synthesisRoute),
    clarificationSensitivity: normalizeClarificationSensitivity(
      settings?.clarificationSensitivity,
    ),
    countOnly: LOCKED_COUNT_ONLY,
  };
}

function loadSettings(initialSettings?: Partial<AgentSettings>): AgentSettings {
  return normalizeSettings(initialSettings);
}

export function useAgentSettings(initialSettings?: Partial<AgentSettings>) {
  const [settings, setSettings] = useState<AgentSettings>(() =>
    loadSettings(initialSettings),
  );
  const settingsRef = useRef(settings);

  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  const update = useCallback((patch: Partial<AgentSettings>) => {
    setSettings((prev) => {
      const next = normalizeSettings({ ...prev, ...patch });
      settingsRef.current = next;
      return next;
    });
  }, []);

  return { settings, settingsRef, update };
}

const routeLabels: Record<SynthesisRoute, string> = {
  auto: 'Auto',
  table_route: 'Table',
  genie_route: 'Genie',
};

const clarificationSensitivityLevels = [
  'off',
  'low',
  'medium',
  'high',
  'on',
] as const satisfies readonly ClarificationSensitivity[];

const clarificationSensitivityLabels: Record<ClarificationSensitivity, string> = {
  off: 'Off',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  on: 'On',
};

export function AgentSettingsPanel({
  settings,
  onLiveUpdate,
  onConfirm,
}: {
  settings: AgentSettings;
  onLiveUpdate: (next: AgentSettings) => void;
  onConfirm: (next: AgentSettings) => void;
}) {
  const [open, setOpen] = useState(false);
  const [draftSettings, setDraftSettings] = useState(() => normalizeSettings(settings));
  const originalSettingsRef = useRef<AgentSettings>(normalizeSettings(settings));
  const containerRef = useRef<HTMLDivElement>(null);
  const [panelMaxHeight, setPanelMaxHeight] = useState<number | null>(null);

  useEffect(() => {
    if (!open) {
      setDraftSettings(normalizeSettings(settings));
      originalSettingsRef.current = normalizeSettings(settings);
    }
  }, [open, settings]);

  const handleOpenChange = useCallback(() => {
    setOpen((prev) => {
      const next = !prev;
      if (next) {
        const normalized = normalizeSettings(settings);
        originalSettingsRef.current = normalized;
        setDraftSettings(normalized);
      }
      return next;
    });
  }, [settings]);

  const handleCancel = useCallback(() => {
    const original = normalizeSettings(originalSettingsRef.current);
    onLiveUpdate(original);
    setDraftSettings(original);
    setOpen(false);
  }, [onLiveUpdate]);

  const handleConfirm = useCallback(() => {
    onConfirm(normalizeSettings(draftSettings));
    setOpen(false);
  }, [draftSettings, onConfirm]);

  useEffect(() => {
    if (!open) {
      setPanelMaxHeight(null);
      return;
    }

    const updatePanelMaxHeight = () => {
      const containerRect = containerRef.current?.getBoundingClientRect();
      if (!containerRect) return;

      const viewportPadding = 16;
      const triggerGap = 8;
      const viewportHeight = window.visualViewport?.height ?? window.innerHeight;
      const availableHeight = Math.max(
        0,
        Math.min(
          containerRect.top - viewportPadding - triggerGap,
          viewportHeight - viewportPadding * 2,
        ),
      );

      setPanelMaxHeight(availableHeight);
    };

    updatePanelMaxHeight();
    window.addEventListener('resize', updatePanelMaxHeight);
    window.visualViewport?.addEventListener('resize', updatePanelMaxHeight);

    return () => {
      window.removeEventListener('resize', updatePanelMaxHeight);
      window.visualViewport?.removeEventListener('resize', updatePanelMaxHeight);
    };
  }, [open]);

  const normalizedDraftSettings = normalizeSettings(draftSettings);
  const clarificationSensitivityIndex = Math.max(
    0,
    clarificationSensitivityLevels.indexOf(
      normalizedDraftSettings.clarificationSensitivity,
    ),
  );

  return (
    <div ref={containerRef} className="relative z-40">
      <button
        type="button"
        onClick={handleOpenChange}
        className="flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-zinc-500 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
        title="Agent settings"
        data-testid="agent-settings-trigger"
        aria-expanded={open}
        aria-controls="agent-settings-panel"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
        Settings
      </button>

      {open && (
        <div
          id="agent-settings-panel"
          data-testid="agent-settings-panel"
          className="absolute bottom-full right-0 z-[60] mb-2 w-64 overflow-y-auto rounded-lg border border-zinc-200 bg-white p-3 shadow-lg dark:border-zinc-700 dark:bg-zinc-900"
          style={panelMaxHeight ? { maxHeight: `${panelMaxHeight}px` } : undefined}
        >
          <div className="mb-3 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            Agent Settings
          </div>

          <div className="mb-3">
            <label className="mb-1 block font-semibold text-xs text-zinc-700 dark:text-zinc-200">
              Execution Mode
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled
                role="switch"
                aria-label="Execution mode"
                aria-checked={draftSettings.executionMode === 'sequential'}
                aria-disabled="true"
                data-testid="execution-mode-toggle"
                className="relative inline-flex h-5 w-9 shrink-0 cursor-not-allowed rounded-full border-2 border-transparent bg-zinc-300 opacity-60 transition-colors dark:bg-zinc-700"
              >
                <span
                  className="pointer-events-none inline-block h-4 w-4 translate-x-0 rounded-full bg-white shadow transition-transform"
                />
              </button>
              <span
                data-testid="execution-mode-value"
                className="text-xs text-zinc-400 dark:text-zinc-500"
              >
                {draftSettings.executionMode === 'sequential'
                  ? 'Sequential'
                  : 'Parallel'}
              </span>
            </div>
            <p className="mt-0.5 text-[10px] text-zinc-400">
              Sequential feeds last query result into the next query generation
            </p>
          </div>

          <div className="mb-3 border-zinc-200 border-t pt-3 dark:border-zinc-700">
            <label className="mb-1 block font-semibold text-xs text-zinc-700 dark:text-zinc-200">
              SQL Synthesis Agent Route
            </label>
            <div className="flex rounded-md border border-zinc-200 dark:border-zinc-700">
              {(
                ['auto', 'table_route', 'genie_route'] as SynthesisRoute[]
              ).map((route) => (
                <button
                  key={route}
                  type="button"
                  onClick={() =>
                    setDraftSettings((prev) => {
                      const next = normalizeSettings({
                        ...prev,
                        synthesisRoute: route,
                      });
                      onLiveUpdate(next);
                      return next;
                    })
                  }
                  data-testid={`synthesis-route-${route}`}
                  aria-pressed={draftSettings.synthesisRoute === route}
                  className={`flex-1 px-2 py-1 text-xs font-medium transition-colors first:rounded-l-md last:rounded-r-md ${
                    draftSettings.synthesisRoute === route
                      ? 'bg-blue-600 text-white'
                      : 'text-zinc-600 hover:bg-zinc-100 dark:text-zinc-300 dark:hover:bg-zinc-800'
                  }`}
                >
                  {routeLabels[route]}
                </button>
              ))}
            </div>
            <p className="mt-0.5 text-[10px] text-zinc-400">
              Auto lets the planner decide; Table uses UC functions; Genie uses
              Genie agents
            </p>
          </div>

          <div className="border-zinc-200 border-t pt-3 dark:border-zinc-700">
            <label className="mb-1 block font-semibold text-xs text-zinc-700 dark:text-zinc-200">
              Clarification Sensitivity
            </label>
            <input
              type="range"
              min={0}
              max={clarificationSensitivityLevels.length - 1}
              step={1}
              value={clarificationSensitivityIndex}
              onChange={(event) => {
                const nextIndex = Number.parseInt(event.currentTarget.value, 10);
                const boundedIndex = Number.isNaN(nextIndex)
                  ? clarificationSensitivityLevels.indexOf('medium')
                  : Math.min(
                      clarificationSensitivityLevels.length - 1,
                      Math.max(0, nextIndex),
                    );

                setDraftSettings((prev) => {
                  const next = normalizeSettings({
                    ...prev,
                    clarificationSensitivity:
                      clarificationSensitivityLevels[boundedIndex] ?? 'medium',
                  });
                  onLiveUpdate(next);
                  return next;
                });
              }}
              aria-label="Clarification sensitivity"
              data-testid="clarification-sensitivity-slider"
              className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-zinc-200 accent-blue-600 dark:bg-zinc-700"
            />
            <div className="mt-1 flex justify-between text-[10px] text-zinc-400">
              {clarificationSensitivityLevels.map((level) => (
                <span key={level}>{clarificationSensitivityLabels[level]}</span>
              ))}
            </div>
            <div
              data-testid="clarification-sensitivity-value"
              className="mt-2 text-xs text-zinc-600 dark:text-zinc-300"
            >
              {clarificationSensitivityLabels[
                normalizedDraftSettings.clarificationSensitivity
              ]}
            </div>
            <p className="mt-0.5 text-[10px] text-zinc-400">
              Off skips clarification, Low is lenient, High is strict, and On
              always asks before planning
            </p>
          </div>

          <div className="border-zinc-200 border-t pt-3 dark:border-zinc-700">
            <label className="mb-1 block font-semibold text-xs text-zinc-700 dark:text-zinc-200">
              Count Only
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled
                role="switch"
                aria-label="Count only"
                aria-checked={draftSettings.countOnly}
                aria-disabled="true"
                data-testid="count-only-toggle"
                className="relative inline-flex h-5 w-9 shrink-0 cursor-not-allowed rounded-full border-2 border-transparent bg-zinc-300 opacity-60 transition-colors dark:bg-zinc-700"
              >
                <span
                  className="pointer-events-none inline-block h-4 w-4 translate-x-4 rounded-full bg-white shadow transition-transform"
                />
              </button>
              <span
                data-testid="count-only-value"
                className="text-xs text-zinc-400 dark:text-zinc-500"
              >
                {draftSettings.countOnly ? 'On' : 'Off'}
              </span>
            </div>
            <p className="mt-0.5 text-[10px] text-zinc-400">
              Adds a privacy instruction to avoid patient-level data and only report patient counts
            </p>
          </div>

          <div className="mt-3 flex justify-end gap-2">
            <button
              type="button"
              onClick={handleCancel}
              data-testid="agent-settings-cancel"
              className="rounded-md border border-zinc-200 px-2 py-1 text-xs font-medium text-zinc-600 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirm}
              data-testid="agent-settings-confirm"
              className="rounded-md bg-blue-600 px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-blue-700"
            >
              Confirm
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
