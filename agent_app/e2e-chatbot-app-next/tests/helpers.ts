import type {
  APIRequestContext,
  Browser,
  BrowserContext,
  Page,
} from '@playwright/test';

// ============================================================================
// SSE Parsing
// ============================================================================

export function parseSSEPayloads(body: string): unknown[] {
  return body
    .split('\n')
    .filter((l) => l.startsWith('data: ') && l !== 'data: [DONE]')
    .map((l) => {
      try {
        return JSON.parse(l.slice(6));
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

// ============================================================================
// Auth helpers
// ============================================================================

export type UserContext = {
  context: BrowserContext;
  page: Page;
  request: APIRequestContext;
  name: string;
};

export async function createAuthenticatedContext({
  browser,
  name,
}: {
  browser: Browser;
  name: string;
}): Promise<UserContext> {
  const headers = {
    'X-Forwarded-User': `${name}-id`,
    'X-Forwarded-Email': `${name}@example.com`,
    'X-Forwarded-Preferred-Username': name,
  };

  const context = await browser.newContext({ extraHTTPHeaders: headers });
  const page = await context.newPage();

  return { context, page, request: context.request, name };
}

// ============================================================================
// Mock streaming helpers (Responses API format)
// ============================================================================

export function mockSSE<T>(payload: T): string {
  return `data: ${JSON.stringify(payload)}`;
}

export const createMockStreamResponse = (SSEs: string[]) => {
  return new Response(stringsToStream(SSEs), {
    headers: { 'Content-Type': 'text/event-stream' },
  });
};

export const stringsToStream = (SSEs: string[]) => {
  const encoder = new TextEncoder();
  return new ReadableStream({
    async start(controller) {
      for (const s of SSEs) {
        controller.enqueue(encoder.encode(`${s}\n\n`));
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      controller.close();
    },
  });
};

function generateUUID(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Build a Responses API multi-delta text stream with progress steps.
 * Simulates the multi-agent Genie streaming pattern:
 *   1. Progress step output items (collapsible details blocks)
 *   2. Final summary as text deltas
 */
export function mockMultiAgentStream(
  progressSteps: string[],
  summaryChunks: string[],
  traceId?: string,
): string[] {
  const responseId = generateUUID();
  const events: string[] = [];
  let seq = 0;

  // response.created
  events.push(
    mockSSE({
      response: {
        id: responseId,
        created_at: Date.now() / 1000,
        error: null,
        model: 'databricks-claude-sonnet-4-5',
        object: 'response',
        output: [],
      },
      sequence_number: seq++,
      type: 'response.created',
    }),
  );

  // Emit progress steps as output_item.done events (the multi-agent pattern)
  if (progressSteps.length > 0) {
    const progressItemId = generateUUID();
    let block = '<details>\n<summary>Progress</summary>\n\n';
    for (const step of progressSteps) {
      block += `- ${step}\n`;
    }
    block += '\n</details>\n\n';

    events.push(
      mockSSE({
        item: {
          id: progressItemId,
          content: [{ annotations: [], text: block, type: 'output_text' }],
          role: 'assistant',
          status: 'completed',
          type: 'message',
        },
        output_index: 0,
        sequence_number: seq++,
        type: 'response.output_item.done',
      }),
    );
  }

  // Emit final summary as streamed text deltas
  const textItemId = generateUUID();
  const fullText = summaryChunks.join('');

  events.push(
    mockSSE({
      item: {
        id: textItemId,
        content: [],
        role: 'assistant',
        status: 'in_progress',
        type: 'message',
      },
      output_index: 1,
      sequence_number: seq++,
      type: 'response.output_item.added',
    }),
    mockSSE({
      content_index: 0,
      item_id: textItemId,
      output_index: 1,
      part: { annotations: [], text: '', type: 'output_text', logprobs: null },
      sequence_number: seq++,
      type: 'response.content_part.added',
    }),
  );

  for (const chunk of summaryChunks) {
    events.push(
      mockSSE({
        content_index: 0,
        delta: chunk,
        item_id: textItemId,
        logprobs: [],
        output_index: 1,
        sequence_number: seq++,
        type: 'response.output_text.delta',
      }),
    );
  }

  events.push(
    mockSSE({
      content_index: 0,
      item_id: textItemId,
      output_index: 1,
      part: {
        annotations: [],
        text: fullText,
        type: 'output_text',
        logprobs: null,
      },
      sequence_number: seq++,
      type: 'response.content_part.done',
    }),
    mockSSE({
      item: {
        id: textItemId,
        content: [
          {
            annotations: [],
            text: fullText,
            type: 'output_text',
            logprobs: null,
          },
        ],
        role: 'assistant',
        status: 'completed',
        type: 'message',
      },
      output_index: 1,
      sequence_number: seq++,
      type: 'response.output_item.done',
      ...(traceId
        ? { databricks_output: { trace: { info: { trace_id: traceId } } } }
        : {}),
    }),
    mockSSE({
      response: {
        id: responseId,
        created_at: Date.now() / 1000,
        error: null,
        model: 'databricks-claude-sonnet-4-5',
        object: 'response',
        output: [
          {
            id: textItemId,
            content: [
              {
                annotations: [],
                text: fullText,
                type: 'output_text',
                logprobs: null,
              },
            ],
            role: 'assistant',
            status: 'completed',
            type: 'message',
          },
        ],
      },
      sequence_number: seq++,
      type: 'response.completed',
    }),
  );

  return events;
}

/**
 * Build a simple Responses API text stream (no progress steps).
 */
export function mockResponsesApiMultiDeltaTextStream(
  chunks: string[],
  traceId?: string,
): string[] {
  return mockMultiAgentStream([], chunks, traceId);
}

/**
 * Build an MLflow AgentServer invocations stream.
 * Wraps the Responses API format with the /invocations envelope.
 */
export function mockMlflowAgentServerStream(
  chunks: string[],
  returnTrace: boolean,
  traceId?: string,
): string[] {
  const tid = traceId ?? (returnTrace ? generateUUID() : undefined);
  return mockResponsesApiMultiDeltaTextStream(chunks, tid);
}

// ============================================================================
// Test mode helpers
// ============================================================================

export function isWithDbMode(): boolean {
  return process.env.TEST_MODE !== 'ephemeral';
}

export function skipInEphemeralMode(
  test: { skip: (condition: boolean, reason: string) => void },
  reason = 'Requires database',
) {
  test.skip(!isWithDbMode(), reason);
}

export function skipInDbMode(
  test: { skip: (condition: boolean, reason: string) => void },
  reason = 'Only for ephemeral mode',
) {
  test.skip(isWithDbMode(), reason);
}
