import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';
import { generateText } from 'ai';
import { z } from 'zod';
import { myProvider } from '@chat-template/core';

import { authMiddleware, requireAuth } from '../middleware/auth';

export const tabularRouter: RouterType = Router();

tabularRouter.use(authMiddleware);

const AGENT_TABULAR_BASE_URL = (() => {
  const proxy = process.env.API_PROXY;
  if (!proxy) return null;
  return proxy.replace(/\/invocations\/?$/, '');
})();

tabularRouter.get('/health', requireAuth, async (_req: Request, res: Response) => {
  if (!AGENT_TABULAR_BASE_URL) {
    return res.status(503).json({
      enabled: false,
      error: 'API_PROXY is not configured; cannot reach Python tabular endpoint.',
    });
  }

  try {
    const response = await fetch(`${AGENT_TABULAR_BASE_URL}/api/tabular/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(30_000),
    });
    const payload = await response.text();
    res.status(response.status).type(response.headers.get('content-type') ?? 'application/json');
    return res.send(payload);
  } catch (error) {
    return res.status(502).json({
      enabled: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

tabularRouter.post('/predict', requireAuth, async (req: Request, res: Response) => {
  if (!AGENT_TABULAR_BASE_URL) {
    return res.status(503).json({
      success: false,
      error: 'API_PROXY is not configured; cannot reach Python tabular endpoint.',
    });
  }

  try {
    const response = await fetch(`${AGENT_TABULAR_BASE_URL}/api/tabular/predict`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
      // First local TabICLv2 calls may include checkpoint/model warmup.
      signal: AbortSignal.timeout(120_000),
    });
    const payload = await response.text();
    res.status(response.status).type(response.headers.get('content-type') ?? 'application/json');
    return res.send(payload);
  } catch (error) {
    return res.status(502).json({
      success: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
});

// ---------------------------------------------------------------------------
// Future prediction template (per visualization workspace)
//
// Inspects a workspace's current table structure and a small row sample, then
// asks an auxiliary LLM to propose an editable set of fields the user can fill
// to generate unlabeled future rows for TabICLv2 inference.
// ---------------------------------------------------------------------------

const COLUMN_KINDS = [
  'integer',
  'currency',
  'percent',
  'number',
  'date',
  'text',
] as const;

const futureTemplateRequestSchema = z.object({
  workspaceId: z.string().min(1),
  columns: z.array(z.string()).min(1).max(200),
  columnMeta: z.record(z.string(), z.enum(COLUMN_KINDS)).optional(),
  sampleRows: z
    .array(z.record(z.string(), z.unknown()))
    .max(10)
    .optional(),
  targetColumns: z.array(z.string()).optional(),
  featureColumns: z.array(z.string()).optional(),
});

const futureTemplateFieldSchema = z.object({
  name: z.string().min(1),
  label: z.string().min(1),
  kind: z.enum(COLUMN_KINDS),
  inputType: z.enum(['list', 'single']),
  defaultValue: z.string().default(''),
  placeholder: z.string().optional(),
  required: z.boolean().default(false),
  options: z.array(z.string()).optional(),
});

const futureTemplateSchema = z.object({
  title: z.string().min(1),
  description: z.string().default(''),
  fields: z.array(futureTemplateFieldSchema).max(40),
  notes: z.string().optional(),
});

type FutureTemplate = z.infer<typeof futureTemplateSchema>;
type FutureTemplateRequest = z.infer<typeof futureTemplateRequestSchema>;

const MAX_CELL_LENGTH = 80;

function truncateCell(value: unknown): unknown {
  if (typeof value === 'string' && value.length > MAX_CELL_LENGTH) {
    return `${value.slice(0, MAX_CELL_LENGTH)}…`;
  }
  return value;
}

function collectObservedValues(
  column: string,
  sampleRows: Array<Record<string, unknown>>,
  limit = 8,
): string[] {
  const values = new Set<string>();
  for (const row of sampleRows) {
    const value = row[column];
    if (value == null || value === '') continue;
    values.add(String(value).trim());
    if (values.size >= limit) break;
  }
  return [...values];
}

function isTimeLikeColumn(column: string, kind: string | undefined): boolean {
  if (kind === 'date') return true;
  return /(^|_)(period|month|year|quarter|week|day|date|time)(_|$)/i.test(
    column,
  );
}

function getTemplateColumns(request: FutureTemplateRequest): string[] {
  const targets = new Set(request.targetColumns ?? []);
  const columnSet = new Set(request.columns);
  const selectedFeatures = (request.featureColumns ?? []).filter(
    (column) => columnSet.has(column) && !targets.has(column),
  );
  if (selectedFeatures.length > 0) return selectedFeatures;
  return request.columns.filter((column) => !targets.has(column));
}

function buildFallbackTemplate(request: FutureTemplateRequest): FutureTemplate {
  const meta = request.columnMeta ?? {};
  const sampleRows = request.sampleRows ?? [];

  const fields: FutureTemplate['fields'] = [];
  for (const column of getTemplateColumns(request)) {
    const kind = meta[column] ?? 'text';
    const observed = collectObservedValues(column, sampleRows);
    const timeLike = isTimeLikeColumn(column, kind);
    const isCategorical =
      kind === 'text' || (observed.length > 0 && observed.length <= 6);

    fields.push({
      name: column,
      label: column,
      kind: COLUMN_KINDS.includes(kind as (typeof COLUMN_KINDS)[number])
        ? (kind as (typeof COLUMN_KINDS)[number])
        : 'text',
      inputType: timeLike || isCategorical ? 'list' : 'single',
      defaultValue: timeLike
        ? defaultFutureDateValues(column, sampleRows)
        : observed.slice(0, 3).join(', '),
      placeholder: timeLike
        ? '2024-01-01, 2024-02-01, 2024-03-01'
        : observed[0] ?? '',
      required: timeLike,
      options: isCategorical && observed.length > 0 ? observed : undefined,
    });
  }

  return {
    title: 'Future prediction rows',
    description:
      'Fill these fields to generate unlabeled future rows. Comma-separated values expand into multiple rows.',
    fields,
    notes:
      'Heuristic template generated from column names and sample values (LLM unavailable).',
  };
}

function normalizeTemplateField(field: FutureTemplate['fields'][number]): FutureTemplate['fields'][number] {
  const categorical = field.kind === 'text' || field.options?.length;
  return categorical || field.kind === 'date'
    ? { ...field, inputType: 'list' }
    : field;
}

type YearMonth = { year: number; month: number };

function defaultFutureDateValues(
  column: string,
  rows: Array<Record<string, unknown>>,
): string {
  let latest: YearMonth | null = null;
  for (const row of rows) {
    const candidate = readYearMonthFromValue(row[column]);
    if (!candidate) continue;
    if (!latest || monthOrdinal(candidate) > monthOrdinal(latest)) {
      latest = candidate;
    }
  }
  const start = latest ? addMonths(latest, 1) : { year: 2024, month: 1 };
  return [start, addMonths(start, 1), addMonths(start, 2)]
    .map((value) => `${value.year}-${pad2(value.month)}-01`)
    .join(', ');
}

function readYearMonthFromValue(value: unknown): YearMonth | null {
  if (value == null) return null;
  const text = String(value).trim();
  const isoMatch = text.match(/\b(19\d{2}|20\d{2})[-/_](\d{1,2})\b/);
  if (isoMatch) {
    const year = Number(isoMatch[1]);
    const month = Number(isoMatch[2]);
    if (month >= 1 && month <= 12) return { year, month };
  }
  const timestamp = Date.parse(text);
  if (!Number.isNaN(timestamp)) {
    const date = new Date(timestamp);
    return { year: date.getFullYear(), month: date.getMonth() + 1 };
  }
  return null;
}

function addMonths(value: YearMonth, count: number): YearMonth {
  const ordinal = monthOrdinal(value) + count;
  return {
    year: Math.floor(ordinal / 12),
    month: (ordinal % 12) + 1,
  };
}

function monthOrdinal(value: YearMonth): number {
  return value.year * 12 + (value.month - 1);
}

function pad2(value: number): string {
  return String(value).padStart(2, '0');
}

function extractJsonObject(text: string): unknown {
  const trimmed = text.trim();
  const fenceMatch = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
  const candidate = fenceMatch ? fenceMatch[1].trim() : trimmed;
  const start = candidate.indexOf('{');
  const end = candidate.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) {
    throw new Error('No JSON object found in model output.');
  }
  return JSON.parse(candidate.slice(start, end + 1));
}

function buildTemplatePrompt(request: FutureTemplateRequest): string {
  const meta = request.columnMeta ?? {};
  const sampleRows = (request.sampleRows ?? []).map((row) => {
    const out: Record<string, unknown> = {};
    for (const column of request.columns) {
      out[column] = truncateCell(row[column]);
    }
    return out;
  });

  const columnDescriptions = request.columns.map((column) => ({
    name: column,
    kind: meta[column] ?? 'text',
    isTarget: (request.targetColumns ?? []).includes(column),
    observedValues: collectObservedValues(column, request.sampleRows ?? []),
  }));

  return [
    'You design a form that lets a user create FUTURE rows (timestamps/categories not present yet) for a tabular prediction model.',
    'Return ONLY a JSON object, no prose, no markdown fences.',
    '',
    'JSON shape:',
    '{',
    '  "title": string,',
    '  "description": string,',
    '  "fields": [',
    '    {',
    '      "name": string (must be one of the table columns),',
    '      "label": string,',
    '      "kind": "integer"|"currency"|"percent"|"number"|"date"|"text",',
    '      "inputType": "list" (comma-separated values expand into multiple rows) | "single",',
    '      "defaultValue": string,',
    '      "placeholder": string,',
    '      "required": boolean,',
    '      "options": string[] (optional; for categorical dimensions)',
    '    }',
    '  ],',
    '  "notes": string (optional, short assumptions)',
    '}',
    '',
    'Rules:',
    '- Only include fields the user must provide to construct a future row (dimensions/date keys).',
    '- The fields MUST be selected feature columns only. Do not add unselected feature columns.',
    '- NEVER include target columns as fields; the model predicts those.',
    '- Time columns should use inputType "list" and accept comma-separated future periods.',
    '- ALL categorical/text dimensions must use inputType "list" and accept comma-separated values.',
    '- Categorical dimensions should set "options" and a sensible default from observed values.',
    '- Final prediction rows are the cartesian product of all list-valued fields, capped by the client.',
    '- Omit derived/computed columns when they can be inferred from a date (e.g. quarter from month).',
    '',
    `Target columns (do NOT add as fields): ${JSON.stringify(
      request.targetColumns ?? [],
    )}`,
    `Selected feature columns (fields MUST be limited to this list): ${JSON.stringify(
      getTemplateColumns(request),
    )}`,
    `Columns: ${JSON.stringify(columnDescriptions)}`,
    `Sample rows (up to 10): ${JSON.stringify(sampleRows)}`,
  ].join('\n');
}

tabularRouter.post(
  '/future-template',
  requireAuth,
  async (req: Request, res: Response) => {
    const parsedRequest = futureTemplateRequestSchema.safeParse(req.body);
    if (!parsedRequest.success) {
      return res.status(400).json({
        success: false,
        error: 'Invalid future-template request.',
        issues: parsedRequest.error.issues,
      });
    }

    const request = parsedRequest.data;
    const featureSet = new Set(getTemplateColumns(request));

    try {
      const model = await myProvider.languageModel('artifact-model');
      const { text } = await generateText({
        model,
        system:
          'You output strict JSON describing a form for generating future tabular rows. No prose.',
        prompt: buildTemplatePrompt(request),
      });

      const validated = futureTemplateSchema.parse(extractJsonObject(text));
      const fields = validated.fields
        .filter((field) => featureSet.has(field.name))
        .map(normalizeTemplateField);
      const fallbackByName = new Map(
        buildFallbackTemplate(request).fields.map((field) => [field.name, field]),
      );
      for (const column of featureSet) {
        if (!fields.some((field) => field.name === column)) {
          const fallback = fallbackByName.get(column);
          if (fallback) fields.push(fallback);
        }
      }

      if (fields.length === 0) {
        return res.json({ success: true, template: buildFallbackTemplate(request) });
      }

      return res.json({
        success: true,
        template: { ...validated, fields },
      });
    } catch (error) {
      console.warn(
        '[future-template] LLM template generation failed, using fallback:',
        error instanceof Error ? error.message : String(error),
      );
      return res.json({
        success: true,
        template: buildFallbackTemplate(request),
      });
    }
  },
);
