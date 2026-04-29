import type { LanguageModelV3 } from '@ai-sdk/provider';

import { getHostUrl } from '@chat-template/utils';
// Import auth module directly
import {
  getDatabricksToken,
  getAuthMethod,
  getDatabricksUserIdentity,
  getCachedCliHost,
} from '@chat-template/auth';
import { createDatabricksProvider } from '@databricks/ai-sdk-provider';
import { extractReasoningMiddleware, wrapLanguageModel } from 'ai';
import { shouldInjectContextForEndpoint } from './request-context';

// Header keys for passing context through streamText headers
export const CONTEXT_HEADER_CONVERSATION_ID = 'x-databricks-conversation-id';
export const CONTEXT_HEADER_USER_ID = 'x-databricks-user-id';

// Use centralized authentication - only on server side
async function getProviderToken(): Promise<string> {
  // First, check if we have a PAT token
  if (process.env.DATABRICKS_TOKEN) {
    console.log('Using PAT token from DATABRICKS_TOKEN env var');
    return process.env.DATABRICKS_TOKEN;
  }

  // Otherwise, use centralized authentication module
  return getDatabricksToken();
}

// Cache the workspace hostname once resolved
let cachedWorkspaceHostname: string | null = null;

// Get workspace hostname with one-time resolution and caching
export async function getWorkspaceHostname(): Promise<string> {
  if (cachedWorkspaceHostname) {
    return cachedWorkspaceHostname;
  }

  try {
    // Use the same approach as getDatabricksCurrentUser to get hostname
    const authMethod = getAuthMethod();

    if (authMethod === 'cli') {
      const cachedCliHost = getCachedCliHost();
      if (cachedCliHost) {
        cachedWorkspaceHostname = cachedCliHost;
        return cachedWorkspaceHostname;
      }

      // For CLI auth, we need to call getDatabricksUserIdentity which handles hostname resolution
      // This will trigger the CLI auth flow and properly cache the host
      await getDatabricksUserIdentity();

      // After CLI auth succeeds, get the hostname from the CLI cache
      const cliHost = getCachedCliHost();
      if (cliHost) {
        cachedWorkspaceHostname = cliHost;
        return cachedWorkspaceHostname;
      } else {
        cachedWorkspaceHostname = getHostUrl();
        return cachedWorkspaceHostname;
      }
    } else {
      // For OAuth, use the standard method
      cachedWorkspaceHostname = getHostUrl();
      return cachedWorkspaceHostname;
    }
  } catch (error) {
    throw new Error(
      `Unable to determine Databricks workspace hostname: ${error instanceof Error ? error.message : 'Unknown error'}`,
    );
  }
}

// Environment variable to enable SSE logging
const LOG_SSE_EVENTS = process.env.LOG_SSE_EVENTS === 'true';
const IS_DEVELOPMENT = process.env.NODE_ENV !== 'production';

const API_PROXY = process.env.API_PROXY;

// Cache for endpoint details to check task type
const endpointDetailsCache = new Map<
  string,
  { task: string | undefined; timestamp: number }
>();
const ENDPOINT_DETAILS_CACHE_DURATION = 60 * 60 * 1000; // 60 minutes

/**
 * Checks if context should be injected based on cached endpoint details.
 * Returns true if API_PROXY is set or if the endpoint task type is agent/v2/chat or agent/v1/responses.
 */
function shouldInjectContext(): boolean {
  const servingEndpoint = process.env.DATABRICKS_SERVING_ENDPOINT;
  if (!servingEndpoint) {
    return Boolean(API_PROXY);
  }

  const cached = endpointDetailsCache.get(servingEndpoint);
  const endpointTask = cached?.task;

  return shouldInjectContextForEndpoint(endpointTask);
}

type ProxyRequestBody = Record<string, unknown> & {
  input?: unknown;
  messages?: Array<{
    role?: string;
    content?: unknown;
  }>;
};

function extractContentPreview(content: unknown): string | null {
  if (typeof content === 'string') {
    return content;
  }
  if (!Array.isArray(content)) {
    return null;
  }

  const textParts = content
    .map((part) => {
      if (typeof part === 'string') {
        return part;
      }
      if (!part || typeof part !== 'object') {
        return null;
      }
      const record = part as Record<string, unknown>;
      if (typeof record.text === 'string') {
        return record.text;
      }
      if (typeof record.content === 'string') {
        return record.content;
      }
      return null;
    })
    .filter((part): part is string => Boolean(part));

  if (textParts.length === 0) {
    return null;
  }
  return textParts.join(' ');
}

function summarizeRequestBody(body: unknown): {
  messageCount: number | null;
  lastUserMessagePreview: string | null;
} {
  if (!body || typeof body !== 'object') {
    return {
      messageCount: null,
      lastUserMessagePreview: null,
    };
  }

  const requestBody = body as ProxyRequestBody;
  const messages = Array.isArray(requestBody.messages)
    ? requestBody.messages
    : Array.isArray(requestBody.input)
      ? (requestBody.input as Array<{ role?: string; content?: unknown }>)
      : [];

  const lastUserMessage = [...messages]
    .reverse()
    .find((message) => message?.role === 'user');
  const preview = extractContentPreview(lastUserMessage?.content);

  return {
    messageCount: messages.length,
    lastUserMessagePreview: preview
      ? preview.slice(0, 200) + (preview.length > 200 ? '...' : '')
      : null,
  };
}

function normalizeResponsesInputContent(content: unknown) {
  if (typeof content === 'string') {
    return [{ type: 'input_text', text: content }];
  }

  if (!Array.isArray(content)) {
    return [];
  }

  return content.flatMap((part) => {
    if (typeof part === 'string') {
      return [{ type: 'input_text', text: part }];
    }

    if (!part || typeof part !== 'object') {
      return [];
    }

    const typedPart = part as Record<string, unknown>;
    const text = typedPart.text;

    if (
      (typedPart.type === 'text' ||
        typedPart.type === 'input_text' ||
        typedPart.type === 'output_text') &&
      typeof text === 'string'
    ) {
      return [{ type: 'input_text', text }];
    }

    return [typedPart];
  });
}

function maybeConvertMessagesToResponsesInput(body: ProxyRequestBody) {
  if (body.input || !Array.isArray(body.messages)) {
    return body;
  }

  const { messages, ...rest } = body;
  return {
    ...rest,
    input: messages.map((message) => ({
      role: message.role,
      content: normalizeResponsesInputContent(message.content),
    })),
  };
}

// Custom fetch function to transform Databricks responses to OpenAI format
export const databricksFetch: typeof fetch = async (input, init) => {
  const url = input.toString();
  let requestInit = init;

  // Extract context and agent settings from headers (passed via streamText headers option)
  const headers = new Headers(requestInit?.headers);
  const conversationId = headers.get(CONTEXT_HEADER_CONVERSATION_ID);
  const userId = headers.get(CONTEXT_HEADER_USER_ID);
  const executionMode = headers.get('x-agent-execution-mode');
  const synthesisRoute = headers.get('x-agent-synthesis-route');
  const clarificationSensitivity = headers.get(
    'x-agent-clarification-sensitivity',
  );
  const countOnly = headers.get('x-agent-count-only') === 'true';
  const useApiProxy = headers.get('x-use-api-proxy') === 'true';
  // Remove custom headers so they don't get sent to the API
  headers.delete(CONTEXT_HEADER_CONVERSATION_ID);
  headers.delete(CONTEXT_HEADER_USER_ID);
  headers.delete('x-agent-execution-mode');
  headers.delete('x-agent-synthesis-route');
  headers.delete('x-agent-clarification-sensitivity');
  headers.delete('x-agent-count-only');
  headers.delete('x-use-api-proxy');
  requestInit = { ...requestInit, headers };

  if (requestInit?.body && typeof requestInit.body === 'string') {
    try {
      let body = JSON.parse(requestInit.body) as ProxyRequestBody;

      if (useApiProxy) {
        body = maybeConvertMessagesToResponsesInput(body);
      }

      // Inject context and agent settings into request body if appropriate
      if (conversationId && userId && shouldInjectContext()) {
        const existingContext =
          body.context && typeof body.context === 'object' ? body.context : {};
        const existingCustomInputs =
          body.custom_inputs && typeof body.custom_inputs === 'object'
            ? body.custom_inputs
            : {};

        body = {
          ...body,
          context: {
            ...existingContext,
            conversation_id: conversationId,
            user_id: userId,
          },
          custom_inputs: {
            ...existingCustomInputs,
            ...(executionMode ? { execution_mode: executionMode } : {}),
            ...(synthesisRoute && synthesisRoute !== 'auto'
              ? { force_synthesis_route: synthesisRoute }
              : {}),
            ...(clarificationSensitivity
              ? { clarification_sensitivity: clarificationSensitivity }
              : {}),
            ...(countOnly ? { count_only: true } : {}),
          },
        };
      }

      requestInit = { ...requestInit, body: JSON.stringify(body) };
    } catch {
      // If JSON parsing fails, pass through unchanged
    }
  }

  // Only log outbound request summaries during local development.
  if (IS_DEVELOPMENT && requestInit?.body) {
    const bodyText =
      typeof requestInit.body === 'string'
        ? requestInit.body
        : JSON.stringify(requestInit.body);
    try {
      const requestBody =
        typeof requestInit.body === 'string'
          ? JSON.parse(requestInit.body)
          : requestInit.body;
      const requestSummary = summarizeRequestBody(requestBody);
      console.log(
        'Databricks request:',
        JSON.stringify({
          url,
          method: requestInit.method || 'POST',
          bodySize: new TextEncoder().encode(bodyText).length,
          messageCount: requestSummary.messageCount,
          lastUserMessagePreview: requestSummary.lastUserMessagePreview,
        }),
      );
    } catch (_e) {
      console.log(
        'Databricks request:',
        JSON.stringify({
          url,
          method: requestInit.method || 'POST',
          bodySize: new TextEncoder().encode(bodyText).length,
          messageCount: null,
          lastUserMessagePreview: null,
        }),
      );
    }
  }

  const response = await fetch(url, requestInit);

  // If SSE logging is enabled and this is a streaming response, wrap the body to log events
  if (LOG_SSE_EVENTS && response.body) {
    const contentType = response.headers.get('content-type') || '';
    const isSSE =
      contentType.includes('text/event-stream') ||
      contentType.includes('application/x-ndjson');

    if (isSSE) {
      const originalBody = response.body;
      const reader = originalBody.getReader();
      const decoder = new TextDecoder();
      let eventCounter = 0;

      const loggingStream = new ReadableStream({
        async pull(controller) {
          const { done, value } = await reader.read();

          if (done) {
            console.log('[SSE] Stream ended');
            controller.close();
            return;
          }

          // Decode and log the chunk
          const text = decoder.decode(value, { stream: true });
          const lines = text.split('\n').filter((line) => line.trim());

          for (const line of lines) {
            eventCounter++;
            if (line.startsWith('data:')) {
              const data = line.slice(5).trim();
              try {
                const parsed = JSON.parse(data);
                console.log(`[SSE #${eventCounter}]`, JSON.stringify(parsed));
              } catch {
                console.log(`[SSE #${eventCounter}] (raw)`, data);
              }
            } else if (line.trim()) {
              console.log(`[SSE #${eventCounter}] (line)`, line);
            }
          }

          // Pass the original data through
          controller.enqueue(value);
        },
        cancel() {
          reader.cancel();
        },
      });

      // Create a new response with the logging stream
      return new Response(loggingStream, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    }
  }

  return response;
};

type CachedProvider = ReturnType<typeof createDatabricksProvider>;
type ProviderMode = 'direct' | 'proxy';
const providerCache = new Map<
  ProviderMode,
  { provider: CachedProvider; timestamp: number }
>();
const PROVIDER_CACHE_DURATION = 60 * 60 * 1000; // Cache provider for 60 minutes

function getAuxiliaryModelEndpoint(id: string): string {
  const endpoint = process.env.LLM_ENDPOINT_SUMMARIZE ?? process.env.LLM_ENDPOINT;
  if (!endpoint) {
    throw new Error(
      `Please set LLM_ENDPOINT_SUMMARIZE or LLM_ENDPOINT for ${id}`,
    );
  }
  return endpoint;
}

// Helper function to get or create the Databricks provider with OAuth
async function getOrCreateDatabricksProvider(
  mode: ProviderMode,
): Promise<CachedProvider> {
  const cached = providerCache.get(mode);
  if (cached && Date.now() - cached.timestamp < PROVIDER_CACHE_DURATION) {
    console.log(`Using cached OAuth provider (${mode})`);
    return cached.provider;
  }

  console.log(`Creating new OAuth provider (${mode})`);
  // Ensure we have a valid token before creating provider
  await getProviderToken();
  const hostname = await getWorkspaceHostname();

  // Create provider with fetch that always uses fresh token
  const provider = createDatabricksProvider({
    // When using endpoints such as Agent Bricks or custom agents, we need to use remote tool calling to handle the tool calls
    useRemoteToolCalling: true,
    baseURL: `${hostname}/serving-endpoints`,
    formatUrl: ({ baseUrl, path }) =>
      mode === 'proxy' && API_PROXY ? API_PROXY : `${baseUrl}${path}`,
    fetch: async (...[input, init]: Parameters<typeof fetch>) => {
      // Always get fresh token for each request (will use cache if valid)
      const currentToken = await getProviderToken();
      const headers = new Headers(init?.headers);
      headers.set('Authorization', `Bearer ${currentToken}`);
      headers.set('x-use-api-proxy', String(mode === 'proxy' && Boolean(API_PROXY)));
      if (mode === 'proxy' && API_PROXY) {
        headers.set('x-mlflow-return-trace-id', 'true');
      }

      return databricksFetch(input, {
        ...init,
        headers,
      });
    },
  });

  providerCache.set(mode, { provider, timestamp: Date.now() });
  return provider;
}

// Get the task type of the serving endpoint
const getEndpointDetails = async (servingEndpoint: string) => {
  const cached = endpointDetailsCache.get(servingEndpoint);
  if (
    cached &&
    Date.now() - cached.timestamp < ENDPOINT_DETAILS_CACHE_DURATION
  ) {
    return cached;
  }

  // Always get fresh token for each request (will use cache if valid)
  const currentToken = await getProviderToken();
  const hostname = await getWorkspaceHostname();
  const headers = new Headers();
  headers.set('Authorization', `Bearer ${currentToken}`);

  const response = await databricksFetch(
    `${hostname}/api/2.0/serving-endpoints/${servingEndpoint}`,
    {
      method: 'GET',
      headers,
    },
  );
  const data = (await response.json()) as { task: string | undefined };
  const returnValue = {
    task: data.task as string | undefined,
    timestamp: Date.now(),
  };
  endpointDetailsCache.set(servingEndpoint, returnValue);
  return returnValue;
};

// Create a smart provider wrapper that handles OAuth initialization
interface SmartProvider {
  languageModel(id: string): Promise<LanguageModelV3>;
}

export class OAuthAwareProvider implements SmartProvider {
  private modelCache = new Map<
    string,
    { model: LanguageModelV3; timestamp: number }
  >();
  private readonly CACHE_DURATION = 60 * 60 * 1000; // 60 minutes

  async languageModel(id: string): Promise<LanguageModelV3> {
    // Check cache first
    const cached = this.modelCache.get(id);
    if (cached && Date.now() - cached.timestamp < this.CACHE_DURATION) {
      console.log(`Using cached model for ${id}`);
      return cached.model;
    }

    const model = await (async () => {
      if (id === 'title-model' || id === 'artifact-model') {
        const provider = await getOrCreateDatabricksProvider('direct');
        return provider.chatCompletions(getAuxiliaryModelEndpoint(id));
      }
      if (API_PROXY) {
        const provider = await getOrCreateDatabricksProvider('proxy');
        // For API proxy we always use the responses agent
        return provider.responses(id);
      }
      const provider = await getOrCreateDatabricksProvider('direct');
      // Server-side environment validation
      if (!process.env.DATABRICKS_SERVING_ENDPOINT) {
        throw new Error(
          'Please set the DATABRICKS_SERVING_ENDPOINT environment variable to the name of an agent serving endpoint',
        );
      }

      const servingEndpoint = process.env.DATABRICKS_SERVING_ENDPOINT;
      const endpointDetails = await getEndpointDetails(servingEndpoint);

      console.log(`Creating fresh model for ${id}`);
      switch (endpointDetails.task) {
        case 'agent/v2/chat':
          return provider.chatAgent(servingEndpoint);
        case 'agent/v1/responses':
        case 'agent/v2/responses':
          return provider.responses(servingEndpoint);
        case 'llm/v1/chat':
          return provider.chatCompletions(servingEndpoint);
        default:
          return provider.responses(servingEndpoint);
      }
    })();

    const wrappedModel = wrapLanguageModel({
      model,
      middleware: [extractReasoningMiddleware({ tagName: 'think' })],
    });

    // Cache the model
    this.modelCache.set(id, { model: wrappedModel, timestamp: Date.now() });
    return wrappedModel;
  }
}

// Create a singleton instance
const providerInstance = new OAuthAwareProvider();

// Export function that returns the provider (no server function needed here)
export function getDatabricksServerProvider() {
  return providerInstance;
}
