import { test, expect } from '../fixtures';
import { ChatPage } from '../pages/chat';
import { mockResponsesApiMultiDeltaTextStream } from '../helpers';

function buildUiMessageStream(chunks: string[]) {
  const messageId = crypto.randomUUID();
  const textId = crypto.randomUUID();
  const events = [
    { type: 'start', messageId },
    { type: 'start-step' },
    { type: 'text-start', id: textId },
    ...chunks.map((delta) => ({ type: 'text-delta', id: textId, delta })),
    { type: 'text-end', id: textId },
    { type: 'finish-step' },
    { type: 'data-traceId', data: 'tr-chart-test' },
  ];
  return `${events.map((event) => `data: ${JSON.stringify(event)}`).join('\n\n')}\n\ndata: [DONE]\n\n`;
}

function buildChartStream(spec: Record<string, unknown>) {
  return buildUiMessageStream([
    `Here is a chart.\n\n\`\`\`echarts-chart\n${JSON.stringify(spec)}\n\`\`\`\n`,
  ]);
}

function buildWorkspaceStream(workspace: Record<string, unknown>) {
  return buildUiMessageStream([
    `Here is a workspace.\n\n\`\`\`viz-workspace\n${JSON.stringify(workspace)}\n\`\`\`\n`,
  ]);
}

async function clearAppLocalStorage(page: import('@playwright/test').Page) {
  await page.goto('/');
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => {
    localStorage.clear();
  });
}

async function mockThreadApi(
  page: import('@playwright/test').Page,
  getAssistantText: (args: {
    userText: string;
    requestIndex: number;
    chatId: string;
  }) => string = ({ userText }) => `Mock response for: ${userText}`,
) {
  const chatMessages = new Map<
    string,
    Array<{
      id: string;
      chatId: string;
      role: 'user' | 'assistant';
      parts: Array<{ type: 'text'; text: string }>;
      attachments: [];
      createdAt: string;
      traceId: string | null;
    }>
  >();
  let requestCount = 0;

  await page.route('**/api/chat*', async (route) => {
    if (route.request().method() !== 'POST') {
      await route.fallback();
      return;
    }

    requestCount += 1;
    const body = route.request().postDataJSON() as {
      id: string;
      message?: {
        id?: string;
        parts?: Array<{ type?: string; text?: string }>;
      };
    };
    const chatId = body.id;
    const userText =
      body.message?.parts?.find((part) => part.type === 'text')?.text ??
      `request ${requestCount}`;
    const assistantText = getAssistantText({
      userText,
      requestIndex: requestCount,
      chatId,
    });
    const messages = chatMessages.get(chatId) ?? [];

    messages.push({
      id: body.message?.id ?? crypto.randomUUID(),
      chatId,
      role: 'user',
      parts: [{ type: 'text', text: userText }],
      attachments: [],
      createdAt: new Date().toISOString(),
      traceId: null,
    });
    messages.push({
      id: crypto.randomUUID(),
      chatId,
      role: 'assistant',
      parts: [{ type: 'text', text: assistantText }],
      attachments: [],
      createdAt: new Date().toISOString(),
      traceId: `tr-${requestCount}`,
    });
    chatMessages.set(chatId, messages);

    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: buildUiMessageStream([assistantText]),
    });
  });

  await page.route('**/api/chat/*', async (route) => {
    const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: chatId,
        createdAt: new Date().toISOString(),
        title: 'Mock thread',
        userId: 'ada-id',
        visibility: 'private',
        executionMode: 'parallel',
        synthesisRoute: 'auto',
        clarificationSensitivity: 'medium',
        countOnly: false,
        lastContext: null,
      }),
    });
  });

  await page.route('**/api/messages/*', async (route) => {
    const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(chatMessages.get(chatId) ?? []),
    });
  });

  await page.route('**/api/feedback/chat/*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    });
  });
}

test.describe('Chat', () => {
  test('should send a message and receive a streaming response', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await mockThreadApi(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('What is the most common diagnosis code?');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toBeVisible();
    const text = await content.textContent();
    expect(text).toBeTruthy();
    expect(text!.length).toBeGreaterThan(0);
  });

  test('should redirect to /chat/:id after sending a message', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await mockThreadApi(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('Show me enrollment trends');
    await chatPage.isGenerationComplete();

    await chatPage.hasChatIdInUrl();
  });

  test('should display user message in the chat', async ({ adaContext }) => {
    const chatPage = new ChatPage(adaContext.page);
    await mockThreadApi(adaContext.page);
    await chatPage.createNewChat();

    const userText = 'How many patients are in the dataset?';
    await chatPage.sendUserMessage(userText);

    const userMsg = await chatPage.getRecentUserMessage();
    await expect(userMsg).toContainText(userText);
  });
});

test.describe('Interactive Charts', () => {
  test('should render streamed chart payload with interactive controls', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const assistantContent = `Here is a chart.\n\n\`\`\`echarts-chart\n${JSON.stringify({
      config: {
        chartType: 'dualAxis',
        title: 'Monthly spend and claims',
        xAxisField: 'service_month',
        series: [
          {
            field: 'claim_count',
            name: 'Claim Count',
            format: 'number',
            chartType: 'bar',
            axis: 'primary',
          },
          {
            field: 'paid_amount',
            name: 'Paid Amount',
            format: 'currency',
            chartType: 'line',
            axis: 'secondary',
          },
        ],
        supportedChartTypes: ['dualAxis', 'bar', 'line'],
        toolbox: true,
      },
      chartData: [
        { service_month: '2024-01', claim_count: 10, paid_amount: 1200 },
        { service_month: '2024-02', claim_count: 15, paid_amount: 1800 },
      ],
      downloadData: [
        { service_month: '2024-01', claim_count: 10, paid_amount: 1200 },
        { service_month: '2024-02', claim_count: 15, paid_amount: 1800 },
      ],
      totalRows: 2,
      aggregated: false,
      aggregationNote: null,
    })}\n\`\`\`\n`;

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: buildUiMessageStream([assistantContent]),
      });
    });

    await page.route('**/api/chat/*', async (route) => {
      const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: chatId,
          createdAt: new Date().toISOString(),
          title: 'Monthly spend and claims',
          userId: 'ada-id',
          visibility: 'private',
          executionMode: 'parallel',
          synthesisRoute: 'auto',
          clarificationSensitivity: 'medium',
          countOnly: false,
          lastContext: null,
        }),
      });
    });

    await page.route('**/api/messages/*', async (route) => {
      const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'user',
            parts: [{ type: 'text', text: 'Show monthly spend and claims' }],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: null,
          },
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'assistant',
            parts: [{ type: 'text', text: assistantContent }],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: 'tr-chart-test',
          },
        ]),
      });
    });

    await page.route('**/api/feedback/chat/*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await chatPage.createNewChat();
    await chatPage.sendUserMessage('Show monthly spend and claims');
    await chatPage.isGenerationComplete();

    await expect(page.getByRole('button', { name: 'Dual Axis' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Bar' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Line' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Download CSV' })).toBeVisible();
    await expect(page.getByRole('img').filter({ hasText: /\$/ }).first()).toBeVisible();
  });

  test('should fall back to regular code rendering for malformed chart payloads', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: buildUiMessageStream([
          'Malformed chart below.\n\n```echarts-chart\n{"config":{"chartType":"bar","series":[]},"chartData":[]}\n```\n',
        ]),
      });
    });

    await page.route('**/api/chat/*', async (route) => {
      const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: chatId,
          createdAt: new Date().toISOString(),
          title: 'Malformed chart',
          userId: 'ada-id',
          visibility: 'private',
          executionMode: 'parallel',
          synthesisRoute: 'auto',
          clarificationSensitivity: 'medium',
          countOnly: false,
          lastContext: null,
        }),
      });
    });

    await page.route('**/api/messages/*', async (route) => {
      const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'user',
            parts: [{ type: 'text', text: 'Render malformed chart' }],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: null,
          },
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'assistant',
            parts: [
              {
                type: 'text',
                text: 'Malformed chart below.\n\n```echarts-chart\n{"config":{"chartType":"bar","series":[]},"chartData":[]}\n```\n',
              },
            ],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: 'tr-chart-test',
          },
        ]),
      });
    });

    await page.route('**/api/feedback/chat/*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await chatPage.createNewChat();
    await chatPage.sendUserMessage('Render malformed chart');
    await chatPage.isGenerationComplete();

    await expect(page.getByRole('button', { name: 'Download CSV' })).toHaveCount(0);
    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toContainText('"chartType":"bar"');
  });

  test('should render fallback visualization workspaces with a paginated table', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const assistantText = `Here is a workspace.\n\n\`\`\`viz-workspace\n${JSON.stringify({
      workspaceId: 'query-fallback',
      title: 'Fallback workspace',
      description: 'Workspace fallback for table-only results.',
      table: {
        columns: ['patient_id'],
        rows: Array.from({ length: 12 }, (_, index) => ({ patient_id: `patient-${String(index + 1).padStart(2, '0')}` })),
        totalRows: 12,
        previewRowCount: 12,
        isPreview: false,
        filename: 'results.csv',
        title: 'Fallback workspace',
        sql: 'SELECT patient_id FROM claims LIMIT 12',
        sqlFilename: 'query.sql',
      },
      fields: [
        { name: 'patient_id', label: 'Patient Id', kind: 'text', role: 'id', format: 'number', uniqueCount: 12, uniqueRatio: 1 },
      ],
      charts: [
        {
          config: {
            chartType: 'bar',
            title: 'Fallback workspace',
            xAxisField: '__workspace_fallback__',
            series: [{ field: 'row_count', name: 'Rows', format: 'number', axis: 'primary' }],
            supportedChartTypes: ['bar'],
            toolbox: true,
            style: { palette: 'default', showLegend: false, showLabels: true, showGridLines: true, showTitle: true, showDescription: false, smoothLines: false },
          },
          chartData: [{ __workspace_fallback__: 'Rows', row_count: 12 }],
          downloadData: [{ __workspace_fallback__: 'Rows', row_count: 12 }],
          totalRows: 12,
          aggregated: true,
          aggregationNote: 'Chart generation returned no payload; showing the table preview with a fallback summary chart.',
          meta: {
            source: 'fallback',
            fallbackApplied: true,
            chartId: 'query-fallback-chart-1',
            sourceTableId: 'query-fallback',
            sourceRowCount: 12,
          },
        },
      ],
      sourceMeta: {
        queryIndex: 1,
        label: 'Fallback workspace',
        previewLimited: false,
        totalRows: 12,
      },
    })}\n\`\`\`\n`;

    await mockThreadApi(page, () => assistantText);
    await clearAppLocalStorage(page);
    await chatPage.createNewChat();
    await chatPage.sendUserMessage('Show fallback workspace');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content.getByRole('button', { name: 'Hide table' })).toBeVisible();
    await expect(content.getByRole('button', { name: 'Show SQL' })).toBeVisible();
    // AG Grid pagination summary — "1 to 10 of 12".
    const pagingSummary = content.locator('.ag-paging-row-summary-panel');
    await expect(pagingSummary).toContainText('1 to 10 of 12');
    await expect(content.getByText('patient-01')).toBeVisible();
    await expect(content.getByText('patient-11')).toHaveCount(0);

    // AG Grid v35 renders 4 pagination buttons sharing the same class in
    // order [First, Previous, Next, Last]; nth(2) is the Next button.
    await content.locator('.ag-paging-button').nth(2).click();

    await expect(pagingSummary).toContainText('11 to 12 of 12');
    await expect(content.getByText('patient-11')).toBeVisible();
    await expect(content.getByText('patient-12')).toBeVisible();
  });

  test('should render a visualization workspace with ask/customize actions', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => {
      pageErrors.push(String(error?.stack || error));
    });
    const assistantContent = `Here is a workspace.\n\n\`\`\`viz-workspace\n${JSON.stringify({
      workspaceId: 'query-1',
      title: 'Monthly spend',
      description: 'Per-table visualization workspace',
      table: {
        columns: ['service_month', 'paid_amount', 'benefit_type'],
        rows: [
          { service_month: '2024-01', paid_amount: 1200, benefit_type: 'Medical' },
          { service_month: '2024-02', paid_amount: 1800, benefit_type: 'Rx' },
        ],
        totalRows: 2,
        previewRowCount: 2,
        isPreview: false,
        filename: 'results.csv',
        title: 'Monthly spend',
      },
      fields: [
        { name: 'service_month', label: 'Service Month', kind: 'date', role: 'time', format: 'number', uniqueCount: 2, uniqueRatio: 1 },
        { name: 'paid_amount', label: 'Paid Amount', kind: 'numeric', role: 'currency', format: 'currency', uniqueCount: 2, uniqueRatio: 1 },
        { name: 'benefit_type', label: 'Benefit Type', kind: 'text', role: 'dimension', format: 'number', uniqueCount: 2, uniqueRatio: 1 },
      ],
      charts: [
        {
          config: {
            chartType: 'line',
            title: 'Monthly spend',
            description: 'Trend over time',
            xAxisField: 'service_month',
            series: [
              {
                field: 'paid_amount',
                name: 'Paid Amount',
                format: 'currency',
                chartType: 'line',
                axis: 'primary',
              },
            ],
            supportedChartTypes: ['line', 'bar', 'area'],
            toolbox: true,
            style: { palette: 'default', showLegend: true, showLabels: false, showGridLines: true, showTitle: true, showDescription: true, smoothLines: true },
          },
          chartData: [
            { service_month: '2024-01', paid_amount: 1200 },
            { service_month: '2024-02', paid_amount: 1800 },
          ],
          downloadData: [
            { service_month: '2024-01', paid_amount: 1200 },
            { service_month: '2024-02', paid_amount: 1800 },
          ],
          totalRows: 2,
          aggregated: false,
          aggregationNote: null,
          meta: {
            chartId: 'query-1-chart-1',
            sourceTableId: 'query-1',
            source: 'auto',
            rationale: 'line selected for paid amount by service month',
            confidence: 0.92,
          },
        },
      ],
    })}\n\`\`\`\n\n<div class="accordion-group">\n\n<details name="sql-accordion"><summary>SQL Explanation</summary>\n\n<div class="accordion-content">\n\nExcellent! I successfully retrieved SQL fragments from all three Genie spaces in parallel.\n\n</div>\n</details>\n\n</div>\n`;

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: buildUiMessageStream([assistantContent]),
      });
    });

    await page.route('**/api/chat/*', async (route) => {
      const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: chatId,
          createdAt: new Date().toISOString(),
          title: 'Monthly spend',
          userId: 'ada-id',
          visibility: 'private',
          executionMode: 'parallel',
          synthesisRoute: 'auto',
          clarificationSensitivity: 'medium',
          countOnly: false,
          lastContext: null,
        }),
      });
    });

    await page.route('**/api/messages/*', async (route) => {
      const chatId = route.request().url().split('/').pop() ?? crypto.randomUUID();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'user',
            parts: [{ type: 'text', text: 'Show monthly spend workspace' }],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: null,
          },
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'assistant',
            parts: [{ type: 'text', text: assistantContent }],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: 'tr-chart-test',
          },
        ]),
      });
    });

    await page.route('**/api/feedback/chat/*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.route('**/api/chart-workspaces/rechart', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          mode: 'replace',
          overrides: {
            title: 'Paid amount by benefit type',
            chartType: 'bar',
            xAxisField: 'benefit_type',
            yAxisField: 'paid_amount',
            groupByField: '',
            timeBucket: 'none',
            description: 'Bar chart by benefit type',
          },
        }),
      });
    });

    await clearAppLocalStorage(page);
    await chatPage.createNewChat();
    await chatPage.sendUserMessage('Show monthly spend workspace');
    await chatPage.isGenerationComplete();

    await page.getByRole('button', { name: /Monthly spend/ }).click();
    await expect(page.getByRole('button', { name: 'Ask', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Customize', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Add another chart' })).toBeVisible();

    await page.getByRole('button', { name: 'Ask', exact: true }).click();
    await page.getByPlaceholder(/Make this a monthly line chart/i).fill('Make this a bar chart by benefit_type');
    await page.getByRole('button', { name: 'Generate chart' }).click();

    await expect(page.getByText('Paid amount by benefit type').first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Show details' })).toBeVisible();
    await expect(page.getByText('[Chart unavailable]')).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });
});

test.describe('Multi-Agent Streaming', () => {
  test('should display assistant response with content from multi-agent workflow', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await mockThreadApi(adaContext.page, ({ userText }) => `Summary for: ${userText}`);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('Summarize the claims data');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toBeVisible();
  });

  test('should handle multiple sequential messages', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await mockThreadApi(adaContext.page, ({ requestIndex }) => `Mock assistant response ${requestIndex}`);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('What tables are available?');
    await chatPage.isGenerationComplete();

    const count1 = await chatPage.getAssistantMessageCount();
    expect(count1).toBeGreaterThanOrEqual(1);

    await chatPage.sendUserMessage('Tell me more about the first one');
    await chatPage.isGenerationComplete();

    const count2 = await chatPage.getAssistantMessageCount();
    expect(count2).toBeGreaterThan(count1);
  });
});

test.describe('Ephemeral Mode', () => {
  test('should work without database (no chat history persistence)', async ({
    adaContext,
  }) => {
    const chatPage = new ChatPage(adaContext.page);
    await mockThreadApi(adaContext.page);
    await chatPage.createNewChat();

    await chatPage.sendUserMessage('Simple test query');
    await chatPage.isGenerationComplete();

    const { content } = await chatPage.getRecentAssistantMessage();
    await expect(content).toBeVisible();
  });
});

test.describe('Agent Settings', () => {
  test('should send selected route with locked execution and count-only settings', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const requests: Array<{
      executionMode: 'parallel' | 'sequential';
      synthesisRoute: 'auto' | 'table_route' | 'genie_route';
      clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
      countOnly: boolean;
    }> = [];

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      const body = route.request().postDataJSON() as {
        agentSettings?: {
          executionMode: 'parallel' | 'sequential';
          synthesisRoute: 'auto' | 'table_route' | 'genie_route';
          clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
          countOnly: boolean;
        };
      };

      expect(body.agentSettings).toBeDefined();
      requests.push(body.agentSettings!);

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `${mockResponsesApiMultiDeltaTextStream(['Settings verified.']).join('\n\n')}\n\n`,
      });
    });

    const combinations = [
      {
        executionMode: 'parallel',
        synthesisRoute: 'auto',
        clarificationSensitivity: 'medium',
        countOnly: true,
      },
      {
        executionMode: 'parallel',
        synthesisRoute: 'table_route',
        clarificationSensitivity: 'medium',
        countOnly: true,
      },
      {
        executionMode: 'parallel',
        synthesisRoute: 'genie_route',
        clarificationSensitivity: 'medium',
        countOnly: true,
      },
    ] as const;

    for (const [index, combination] of combinations.entries()) {
      await test.step(
        `${combination.executionMode} + ${combination.synthesisRoute}`,
        async () => {
          const requestCountBefore = requests.length;
          await clearAppLocalStorage(page);
          await chatPage.configureAgentSettings(
            combination.executionMode,
            combination.synthesisRoute,
            combination.clarificationSensitivity,
          );
          await chatPage.sendUserMessage(`settings verification ${index + 1}`);
          await chatPage.isGenerationComplete();

          expect(requests).toHaveLength(requestCountBefore + 1);
          expect(requests.at(-1)).toEqual(combination);
        },
      );
    }
  });

  test('should use the newly selected route for later turns in the same thread', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const requests: Array<{
      executionMode: 'parallel' | 'sequential';
      synthesisRoute: 'auto' | 'table_route' | 'genie_route';
      clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
      countOnly: boolean;
    }> = [];

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      const body = route.request().postDataJSON() as {
        agentSettings?: {
          executionMode: 'parallel' | 'sequential';
          synthesisRoute: 'auto' | 'table_route' | 'genie_route';
          clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
          countOnly: boolean;
        };
      };

      expect(body.agentSettings).toBeDefined();
      requests.push(body.agentSettings!);

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `${mockResponsesApiMultiDeltaTextStream(['Settings verified.']).join('\n\n')}\n\n`,
      });
    });

    await clearAppLocalStorage(page);

    let requestCountBefore = requests.length;
    await chatPage.configureAgentSettings('parallel', 'table_route', 'medium');
    await chatPage.sendUserMessage('first turn with table');
    await chatPage.isGenerationComplete();
    expect(requests).toHaveLength(requestCountBefore + 1);
    expect(requests.at(-1)).toEqual({
      executionMode: 'parallel',
      synthesisRoute: 'table_route',
      clarificationSensitivity: 'medium',
      countOnly: true,
    });

    requestCountBefore = requests.length;
    await chatPage.configureAgentSettings('parallel', 'genie_route', 'high');
    await chatPage.sendUserMessage('second turn with genie');
    await chatPage.isGenerationComplete();
    expect(requests).toHaveLength(requestCountBefore + 1);
    expect(requests.at(-1)).toEqual({
      executionMode: 'parallel',
      synthesisRoute: 'genie_route',
      clarificationSensitivity: 'high',
      countOnly: true,
    });

    await clearAppLocalStorage(page);

    requestCountBefore = requests.length;
    await chatPage.configureAgentSettings('parallel', 'genie_route', 'off');
    await chatPage.sendUserMessage('first turn with genie');
    await chatPage.isGenerationComplete();
    expect(requests).toHaveLength(requestCountBefore + 1);
    expect(requests.at(-1)).toEqual({
      executionMode: 'parallel',
      synthesisRoute: 'genie_route',
      clarificationSensitivity: 'off',
      countOnly: true,
    });

    requestCountBefore = requests.length;
    await chatPage.configureAgentSettings('parallel', 'table_route', 'on');
    await chatPage.sendUserMessage('second turn with table');
    await chatPage.isGenerationComplete();
    expect(requests).toHaveLength(requestCountBefore + 1);
    expect(requests.at(-1)).toEqual({
      executionMode: 'parallel',
      synthesisRoute: 'table_route',
      clarificationSensitivity: 'on',
      countOnly: true,
    });
  });

  test('should preserve welcome-screen settings across a clarification follow-up', async ({
    adaContext,
  }) => {
    test.setTimeout(60_000);

    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const requests: Array<{
      id: string;
      messageText?: string;
      agentSettings: {
        executionMode: 'parallel' | 'sequential';
        synthesisRoute: 'auto' | 'table_route' | 'genie_route';
        clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
        countOnly: boolean;
      };
    }> = [];

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      const body = route.request().postDataJSON() as {
        id: string;
        message?: {
          parts?: Array<{ type?: string; text?: string }>;
        };
        agentSettings?: {
          executionMode: 'parallel' | 'sequential';
          synthesisRoute: 'auto' | 'table_route' | 'genie_route';
          clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
          countOnly: boolean;
        };
      };

      expect(body.agentSettings).toBeDefined();

      const messageText = body.message?.parts?.find(
        (part) => part.type === 'text',
      )?.text;

      requests.push({
        id: body.id,
        messageText,
        agentSettings: body.agentSettings!,
      });

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          requests.length === 1
            ? `${[
                { type: 'start', messageId: crypto.randomUUID() },
                { type: 'start-step' },
                { type: 'text-start', id: 'clarification-text' },
                {
                  type: 'text-delta',
                  id: 'clarification-text',
                  delta: 'Clarification required before continuing.',
                },
                { type: 'text-end', id: 'clarification-text' },
                { type: 'finish-step' },
                {
                  type: 'data-clarification',
                  data: {
                    reason: 'Which member trend do you want by month?',
                    options: ['Monthly member count', 'Monthly member spend'],
                  },
                },
                { type: 'data-traceId', data: 'tr-clarification-test' },
              ]
                .map((event) => `data: ${JSON.stringify(event)}`)
                .join('\n\n')}\n\ndata: [DONE]\n\n`
            : buildUiMessageStream(['Settings preserved after clarification.']),
      });
    });

    await chatPage.createNewChat();
    await page.getByTestId('agent-settings-trigger').click();
    await page.getByTestId('synthesis-route-table_route').click();
    await page.getByTestId('clarification-sensitivity-slider').fill('4');
    await page.getByTestId('agent-settings-confirm').click();

    await chatPage.sendUserMessage('show member trend');
    await chatPage.isGenerationComplete();
    await expect.poll(() => requests.length).toBe(1);

    expect(requests).toHaveLength(1);
    expect(requests[0]).toEqual({
      id: requests[0]!.id,
      messageText: 'show member trend',
      agentSettings: {
        executionMode: 'parallel',
        synthesisRoute: 'table_route',
        clarificationSensitivity: 'on',
        countOnly: true,
      },
    });

    const firstRequestId = requests[0]!.id;

    await page.evaluate(async ({ chatId }) => {
      await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          id: chatId,
          message: {
            id: crypto.randomUUID(),
            role: 'user',
            parts: [{ type: 'text', text: 'monthly for 2024' }],
          },
          selectedChatModel: 'chat-model',
          selectedVisibilityType: 'private',
          agentSettings: {
            executionMode: 'parallel',
            synthesisRoute: 'table_route',
            clarificationSensitivity: 'on',
            countOnly: true,
          },
        }),
      });
    }, { chatId: firstRequestId });
    await expect.poll(() => requests.length).toBe(2);

    expect(requests).toHaveLength(2);
    expect(requests[1]).toEqual({
      id: firstRequestId,
      messageText: 'monthly for 2024',
      agentSettings: {
        executionMode: 'parallel',
        synthesisRoute: 'table_route',
        clarificationSensitivity: 'on',
        countOnly: true,
      },
    });
  });

  test('should not reconnect a stale clarification stream after answering the modal', async ({
    adaContext,
  }) => {
    test.setTimeout(60_000);

    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const postRequests: Array<{ id: string; messageText?: string }> = [];
    let resumeStreamRequests = 0;

    await page.route('**/api/chat/*/stream', async (route) => {
      resumeStreamRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `${[
          { type: 'start', messageId: crypto.randomUUID() },
          { type: 'start-step' },
          { type: 'text-start', id: 'replayed-clarification' },
          {
            type: 'text-delta',
            id: 'replayed-clarification',
            delta: 'Clarification required before continuing.',
          },
          { type: 'text-end', id: 'replayed-clarification' },
          { type: 'finish-step' },
          {
            type: 'data-clarification',
            data: {
              reason: 'Which member trend do you want by month?',
              options: ['Monthly member count', 'Monthly member spend'],
            },
          },
          { type: 'data-traceId', data: 'tr-stale-clarification-stream' },
        ]
          .map((event) => `data: ${JSON.stringify(event)}`)
          .join('\n\n')}\n\ndata: [DONE]\n\n`,
      });
    });

    await page.route('**/api/chat*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.fallback();
        return;
      }

      const body = route.request().postDataJSON() as {
        id: string;
        message?: {
          parts?: Array<{ type?: string; text?: string }>;
        };
      };

      const messageText = body.message?.parts?.find(
        (part) => part.type === 'text',
      )?.text;
      postRequests.push({ id: body.id, messageText });

      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body:
          postRequests.length === 1
            ? `${[
                { type: 'start', messageId: crypto.randomUUID() },
                { type: 'start-step' },
                { type: 'text-start', id: 'clarification-text' },
                {
                  type: 'text-delta',
                  id: 'clarification-text',
                  delta: 'Clarification required before continuing.',
                },
                { type: 'text-end', id: 'clarification-text' },
                { type: 'finish-step' },
                {
                  type: 'data-clarification',
                  data: {
                    reason: 'Which member trend do you want by month?',
                    options: ['Monthly member count', 'Monthly member spend'],
                  },
                },
                { type: 'data-traceId', data: 'tr-clarification-test' },
              ]
                .map((event) => `data: ${JSON.stringify(event)}`)
                .join('\n\n')}\n\ndata: [DONE]\n\n`
            : buildUiMessageStream(['Final summary after clarification.']),
      });
    });

    await chatPage.createNewChat();
    await chatPage.sendUserMessage('show member trend');
    await chatPage.isGenerationComplete();
    await expect.poll(() => postRequests.length).toBe(1);

    const chatId = postRequests[0]?.id;
    expect(chatId).toBeTruthy();

    await page.evaluate(async ({ currentChatId }) => {
      await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          id: currentChatId,
          message: {
            id: crypto.randomUUID(),
            role: 'user',
            parts: [{ type: 'text', text: 'Monthly member count' }],
          },
          selectedChatModel: 'chat-model',
          selectedVisibilityType: 'private',
        }),
      });
    }, { currentChatId: chatId });

    await expect.poll(() => postRequests.length).toBe(2);
    expect(postRequests[1]?.messageText).toBe('Monthly member count');
    expect(resumeStreamRequests).toBe(0);
  });

  test('should isolate settings across multiple open tabs', async ({
    adaContext,
  }) => {
    const { context, page } = adaContext;
    const secondPage = await context.newPage();
    const firstChatPage = new ChatPage(page);
    const secondChatPage = new ChatPage(secondPage);

    await firstChatPage.createNewChat();
    await secondChatPage.createNewChat();

    await firstChatPage.configureAgentSettings('parallel', 'genie_route', 'high');
    await secondChatPage.configureAgentSettings('parallel', 'table_route', 'low');

    await page.bringToFront();
    await firstChatPage.openAgentSettings();
    await expect(page.getByTestId('execution-mode-value')).toHaveText('Parallel');
    await expect(page.getByTestId('synthesis-route-genie_route')).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByTestId('clarification-sensitivity-value')).toHaveText(
      'High',
    );
    await firstChatPage.cancelAgentSettings();

    await secondPage.bringToFront();
    await secondChatPage.openAgentSettings();
    await expect(secondPage.getByTestId('execution-mode-value')).toHaveText(
      'Parallel',
    );
    await expect(
      secondPage.getByTestId('synthesis-route-table_route'),
    ).toHaveAttribute('aria-pressed', 'true');
    await expect(
      secondPage.getByTestId('clarification-sensitivity-value'),
    ).toHaveText('Low');
    await secondChatPage.cancelAgentSettings();

    await secondPage.close();
  });

  test('should discard draft changes on cancel', async ({ adaContext }) => {
    const chatPage = new ChatPage(adaContext.page);

    await chatPage.createNewChat();
    await chatPage.openAgentSettings();
    await chatPage.setSynthesisRoute('genie_route');
    await chatPage.setClarificationSensitivity('on');
    await chatPage.cancelAgentSettings();

    await chatPage.openAgentSettings();
    await expect(adaContext.page.getByTestId('execution-mode-value')).toHaveText(
      'Parallel',
    );
    await expect(
      adaContext.page.getByTestId('synthesis-route-auto'),
    ).toHaveAttribute('aria-pressed', 'true');
    await expect(
      adaContext.page.getByTestId('clarification-sensitivity-value'),
    ).toHaveText('Medium');
  });

  test('should show updated settings after leaving and reopening a thread once', async ({
    adaContext,
  }) => {
    const { page } = adaContext;
    const chatPage = new ChatPage(page);
    const chatId = crypto.randomUUID();
    const userId = 'test-user-id';
    let chatSettings = {
      executionMode: 'parallel' as const,
      synthesisRoute: 'auto' as const,
      clarificationSensitivity: 'medium' as const,
    };

    await page.route(`**/api/chat/${chatId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          id: chatId,
          createdAt: new Date().toISOString(),
          title: 'Existing thread',
          userId,
          visibility: 'private',
          executionMode: chatSettings.executionMode,
          synthesisRoute: chatSettings.synthesisRoute,
          clarificationSensitivity: chatSettings.clarificationSensitivity,
          lastContext: null,
        }),
      });
    });

    await page.route(`**/api/messages/${chatId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            id: crypto.randomUUID(),
            chatId,
            role: 'assistant',
            parts: [{ type: 'text', text: 'Existing response' }],
            attachments: [],
            createdAt: new Date().toISOString(),
            traceId: null,
          },
        ]),
      });
    });

    await page.route(`**/api/feedback/chat/${chatId}`, async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({}),
      });
    });

    await page.route(`**/api/chat/${chatId}/settings`, async (route) => {
      const nextSettings = route.request().postDataJSON() as typeof chatSettings;
      chatSettings = nextSettings;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ success: true }),
      });
    });

    await page.goto(`/chat/${chatId}`);
    await page.waitForLoadState('networkidle');

    await chatPage.configureAgentSettings('parallel', 'genie_route', 'high');

    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.goto(`/chat/${chatId}`);
    await page.waitForLoadState('networkidle');

    await chatPage.openAgentSettings();
    await expect(page.getByTestId('execution-mode-value')).toHaveText(
      'Parallel',
    );
    await expect(
      page.getByTestId('synthesis-route-genie_route'),
    ).toHaveAttribute('aria-pressed', 'true');
    await expect(
      page.getByTestId('clarification-sensitivity-value'),
    ).toHaveText('High');
  });
});
