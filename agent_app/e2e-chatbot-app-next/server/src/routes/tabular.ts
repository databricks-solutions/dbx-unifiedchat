import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';
import { z } from 'zod';

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
// Builds a deterministic editable template from the selected feature columns.
// Each field accepts comma-separated values; future prediction rows are the
// cartesian product of those values on the client.
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
    fields.push({
      name: column,
      label: column,
      kind: COLUMN_KINDS.includes(kind as (typeof COLUMN_KINDS)[number])
        ? (kind as (typeof COLUMN_KINDS)[number])
        : 'text',
      inputType: 'list',
      defaultValue: timeLike
        ? defaultFutureDateValues(column, sampleRows)
        : isNumericKind(kind)
          ? defaultNumericValues(column, kind, sampleRows)
          : observed.slice(0, 3).join(', '),
      placeholder: timeLike
        ? '2024-01-01, 2024-02-01, 2024-03-01'
        : observed[0] ?? '',
      required: timeLike,
      options: observed.length > 0 ? observed : undefined,
    });
  }

  return {
    title: 'Future prediction rows',
    description:
      'Fill these fields to generate unlabeled future rows. Comma-separated values expand into multiple rows.',
    fields,
    notes:
      'Template generated from the selected feature columns. Comma-separated values expand into cartesian-product prediction rows.',
  };
}

function isNumericKind(kind: string | undefined): boolean {
  return (
    kind === 'integer' ||
    kind === 'number' ||
    kind === 'currency' ||
    kind === 'percent'
  );
}

function defaultNumericValues(
  column: string,
  _kind: string,
  rows: Array<Record<string, unknown>>,
): string {
  const values = rows
    .map((row) => toFiniteNumber(row[column]))
    .filter((value): value is number => value != null)
    .sort((a, b) => a - b);
  if (values.length === 0) return '';

  const median = medianNumber(values);
  const defaults = [median];
  const nearestDifferent = values.find((value) => value !== median);
  if (nearestDifferent != null) defaults.push(nearestDifferent);
  return defaults.map((value) => formatNumberForTemplate(value)).join(', ');
}

function toFiniteNumber(value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, '').replace(/^[$€£¥₹]\s?/, ''));
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function medianNumber(sortedValues: number[]): number {
  const mid = Math.floor(sortedValues.length / 2);
  if (sortedValues.length % 2 === 1) return sortedValues[mid] as number;
  return ((sortedValues[mid - 1] as number) + (sortedValues[mid] as number)) / 2;
}

function formatNumberForTemplate(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(6)));
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

    return res.json({
      success: true,
      template: buildFallbackTemplate(parsedRequest.data),
    });
  },
);
