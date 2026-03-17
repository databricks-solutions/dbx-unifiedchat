import { test, expect } from '@playwright/test';
import { parseSSEPayloads } from '../helpers';

/**
 * Tests for the API proxy flow: Express server -> /invocations -> Python backend.
 * These run against the server on port 3003 with API_PROXY set to the mock.
 */
test.describe('Chat API Proxy', () => {
  test('POST /api/chat should return a streaming response', async ({
    request,
  }) => {
    const chatId = crypto.randomUUID();
    const response = await request.post('/api/chat', {
      headers: {
        'X-Forwarded-User': 'test-user-id',
        'X-Forwarded-Email': 'test@example.com',
        'X-Forwarded-Preferred-Username': 'testuser',
      },
      data: {
        id: chatId,
        message: {
          role: 'user',
          content: 'What diagnoses are most common?',
        },
        selectedChatModel: 'chat-model',
        selectedVisibilityType: 'private',
      },
    });

    expect(response.status()).toBe(200);

    const body = await response.text();
    expect(body.length).toBeGreaterThan(0);

    const payloads = parseSSEPayloads(body);
    expect(payloads.length).toBeGreaterThan(0);

    const hasTextDelta = payloads.some(
      (p: any) =>
        p?.type === 'text-delta' ||
        p?.type === 'response.output_text.delta' ||
        (p?.type === 'start' && p?.messageId),
    );
    expect(hasTextDelta).toBeTruthy();
  });

  test('GET /ping should return health check', async ({ request }) => {
    const response = await request.get('/ping');
    expect(response.status()).toBe(200);
  });

  test('GET /api/config should return feature flags', async ({ request }) => {
    const response = await request.get('/api/config');
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body).toHaveProperty('features');
    expect(body.features).toHaveProperty('chatHistory');
    expect(body.features).toHaveProperty('feedback');
    // In ephemeral api-proxy mode, chatHistory should be false
    expect(body.features.chatHistory).toBe(false);
  });
});
