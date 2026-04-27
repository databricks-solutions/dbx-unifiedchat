import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';
import {
  convertToModelMessages,
  createUIMessageStream,
  streamText,
  generateText,
  type LanguageModelUsage,
  pipeUIMessageStreamToResponse,
} from 'ai';
import type { LanguageModelV3Usage } from '@ai-sdk/provider';
import { z } from 'zod';

// Convert ai's LanguageModelUsage to @ai-sdk/provider's LanguageModelV3Usage
function toV3Usage(usage: LanguageModelUsage): LanguageModelV3Usage {
  return {
    inputTokens: {
      total: usage.inputTokens,
      noCache: undefined,
      cacheRead: undefined,
      cacheWrite: undefined,
    },
    outputTokens: {
      total: usage.outputTokens,
      text: undefined,
      reasoning: undefined,
    },
  };
}
import {
  authMiddleware,
  requireAuth,
  requireChatAccess,
  getIdFromRequest,
} from '../middleware/auth';
import {
  deleteChatById,
  getMessagesByChatId,
  saveChat,
  saveMessages,
  updateChatLastContextById,
  updateChatVisiblityById,
  isDatabaseAvailable,
  updateChatTitleById,
  updateChatAgentSettingsById,
} from '@chat-template/db';
import {
  type ChatMessage,
  checkChatAccess,
  convertToUIMessages,
  generateUUID,
  myProvider,
  postRequestBodySchema,
  type PostRequestBody,
  StreamCache,
  type VisibilityType,
  CONTEXT_HEADER_CONVERSATION_ID,
  CONTEXT_HEADER_USER_ID,
} from '@chat-template/core';
import { ChatSDKError } from '@chat-template/core/errors';
import { storeMessageMeta } from '../lib/message-meta-store';
import { drainStreamToWriter, fallbackToStreamText } from '../lib/stream-fallback';

export const chatRouter: RouterType = Router();

const chatAgentSettingsSchema = z.object({
  executionMode: z.enum(['parallel', 'sequential']),
  synthesisRoute: z.enum(['auto', 'table_route', 'genie_route']),
  clarificationSensitivity: z.enum(['off', 'low', 'medium', 'high', 'on']),
  countOnly: z.boolean(),
});

const streamCache = new StreamCache();
const ACTIVE_TURN_DEDUPE_WINDOW_MS = 15 * 1000;

type ActiveTurnRequest = {
  logicalRequestId: string;
  incomingMessageId: string | null;
  partFingerprint: string | null;
  previousAnchorId: string | null;
  streamId: string;
  createdAt: number;
  streamReadyPromise: Promise<void>;
  resolveStreamReady: () => void;
};

const activeTurnRequests = new Map<string, ActiveTurnRequest>();

function getMessagePartFingerprint(message?: ChatMessage): string | null {
  if (!message) {
    return null;
  }

  try {
    return JSON.stringify(message.parts);
  } catch {
    return null;
  }
}

function getPreviousAnchorId(previousMessages: ChatMessage[]): string | null {
  return previousMessages.at(-1)?.id ?? null;
}

function getLogicalRequestId({
  chatId,
  message,
  previousMessages,
}: {
  chatId: string;
  message?: ChatMessage;
  previousMessages: ChatMessage[];
}): string {
  if (message?.id) {
    return message.id;
  }

  return `${chatId}:${previousMessages.at(-1)?.id ?? 'continuation'}`;
}

function clearActiveTurnRequest(chatId: string): void {
  activeTurnRequests.delete(chatId);
}

const isPlaywrightMockMode =
  process.env.PLAYWRIGHT === 'True' &&
  process.env.DATABRICKS_HOST === 'mock-value';

function getMockResponseText(message?: ChatMessage): string {
  const prompt = message?.parts.find((part) => part.type === 'text')?.text ?? '';
  if (!prompt) {
    return 'Mock continuation response.';
  }
  if (/diagnosis|common/i.test(prompt)) {
    return 'The most common diagnosis code in the mock dataset is I10.';
  }
  if (/enrollment/i.test(prompt)) {
    return 'Mock enrollment trends are available by month.';
  }
  if (/tables?/i.test(prompt)) {
    return 'Available mock tables include claims, members, and providers.';
  }
  return `Mock response for: ${prompt}`;
}

function createActiveTurnRequest({
  logicalRequestId,
  message,
  previousMessages,
  streamId,
}: {
  logicalRequestId: string;
  message: ChatMessage;
  previousMessages: ChatMessage[];
  streamId: string;
}): ActiveTurnRequest {
  let resolveStreamReady!: () => void;
  const streamReadyPromise = new Promise<void>((resolve) => {
    resolveStreamReady = resolve;
  });

  return {
    logicalRequestId,
    incomingMessageId: message.id,
    partFingerprint: getMessagePartFingerprint(message),
    previousAnchorId: getPreviousAnchorId(previousMessages),
    streamId,
    createdAt: Date.now(),
    streamReadyPromise,
    resolveStreamReady,
  };
}

function findDuplicateActiveTurn({
  chatId,
  message,
  previousMessages,
}: {
  chatId: string;
  message?: ChatMessage;
  previousMessages: ChatMessage[];
}): ActiveTurnRequest | null {
  if (!message) {
    return null;
  }

  const activeTurn = activeTurnRequests.get(chatId);
  if (!activeTurn) {
    return null;
  }

  if (Date.now() - activeTurn.createdAt > ACTIVE_TURN_DEDUPE_WINDOW_MS) {
    clearActiveTurnRequest(chatId);
    return null;
  }

  const sameMessageId = activeTurn.incomingMessageId === message.id;
  const sameContentAndContext =
    activeTurn.partFingerprint === getMessagePartFingerprint(message) &&
    activeTurn.previousAnchorId === getPreviousAnchorId(previousMessages);

  return sameMessageId || sameContentAndContext ? activeTurn : null;
}

async function pipeCachedStreamToResponse({
  res,
  streamId,
  streamReadyPromise,
}: {
  res: Response;
  streamId: string;
  streamReadyPromise?: Promise<void>;
}): Promise<boolean> {
  if (streamReadyPromise) {
    await Promise.race([
      streamReadyPromise,
      new Promise<void>((resolve) => setTimeout(resolve, 1000)),
    ]);
  }

  const stream = streamCache.getStream(streamId);
  if (!stream) {
    return false;
  }

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  stream.pipe(res);
  stream.on('error', (error) => {
    console.error('[Chat] Cached stream replay error:', error);
    if (!res.headersSent) {
      res.status(500).end();
    }
  });

  return true;
}
// Apply auth middleware to all chat routes
chatRouter.use(authMiddleware);

/**
 * POST /api/chat - Send a message and get streaming response
 *
 * Note: Works in ephemeral mode when database is disabled.
 * Streaming continues normally, but no chat/message persistence occurs.
 */
chatRouter.post('/', requireAuth, async (req: Request, res: Response) => {
  const dbAvailable = isDatabaseAvailable();
  if (!dbAvailable) {
    console.log('[Chat] Running in ephemeral mode - no persistence');
  }

  let requestBody: PostRequestBody;

  try {
    requestBody = postRequestBodySchema.parse(req.body);
  } catch (_) {
    console.error('Error parsing request body:', _);
    const error = new ChatSDKError('bad_request:api');
    const response = error.toResponse();
    return res.status(response.status).json(response.json);
  }

  let activeTurnRequest: ActiveTurnRequest | null = null;

  try {
    const {
      id,
      message,
      selectedChatModel,
      selectedVisibilityType,
      agentSettings,
    }: {
      id: string;
      message?: ChatMessage;
      selectedChatModel: string;
      selectedVisibilityType: VisibilityType;
      agentSettings?: {
        executionMode: 'parallel' | 'sequential';
        synthesisRoute: 'auto' | 'table_route' | 'genie_route';
        clarificationSensitivity: 'off' | 'low' | 'medium' | 'high' | 'on';
        countOnly: boolean;
      };
    } = requestBody;

    const session = req.session;
    if (!session) {
      const error = new ChatSDKError('unauthorized:chat');
      const response = error.toResponse();
      return res.status(response.status).json(response.json);
    }

    const { chat, allowed, reason } = await checkChatAccess(
      id,
      session?.user.id,
    );

    if (reason !== 'not_found' && !allowed) {
      const error = new ChatSDKError('forbidden:chat');
      const response = error.toResponse();
      return res.status(response.status).json(response.json);
    }

    if (!chat) {
      // Only create new chat if we have a message (not a continuation)
      if (isDatabaseAvailable() && message) {
        await saveChat({
          id,
          userId: session.user.id,
          title: 'New chat',
          visibility: selectedVisibilityType,
          executionMode: agentSettings?.executionMode ?? 'parallel',
          synthesisRoute: agentSettings?.synthesisRoute ?? 'auto',
          clarificationSensitivity:
            agentSettings?.clarificationSensitivity ?? 'medium',
          countOnly: agentSettings?.countOnly ?? true,
        });

        generateTitleFromUserMessage({ message })
          .then((title) =>
            updateChatTitleById({
              chatId: id,
              title,
            }),
          )
          .catch((error) => {
            console.error('Error generating title:', error);
            const textFromUserMessage = message?.parts.find(
              (part) => part.type === 'text',
            )?.text;
            if (textFromUserMessage) {
              updateChatTitleById({
                chatId: id,
                title: truncatePreserveWords(textFromUserMessage, 128),
              });
            }
          });
      }
    } else {
      if (chat.userId !== session.user.id) {
        const error = new ChatSDKError('forbidden:chat');
        const response = error.toResponse();
        return res.status(response.status).json(response.json);
      }

      if (dbAvailable && agentSettings) {
        await updateChatAgentSettingsById({
          chatId: id,
          executionMode: agentSettings.executionMode,
          synthesisRoute: agentSettings.synthesisRoute,
          clarificationSensitivity: agentSettings.clarificationSensitivity,
          countOnly: agentSettings.countOnly,
        });
      }
    }

    const messagesFromDb = await getMessagesByChatId({ id });

    // Use previousMessages from request body when:
    // 1. Ephemeral mode (DB not available) - always use client-side messages
    // 2. Continuation request (no message) - tool results only exist client-side
    const useClientMessages =
      !dbAvailable || (!message && requestBody.previousMessages);
    const previousMessages = useClientMessages
      ? (requestBody.previousMessages ?? [])
      : convertToUIMessages(messagesFromDb);
    const logicalRequestId = getLogicalRequestId({
      chatId: id,
      message,
      previousMessages: previousMessages as ChatMessage[],
    });
    const streamId = generateUUID();

    const duplicateActiveTurn = findDuplicateActiveTurn({
      chatId: id,
      message,
      previousMessages: previousMessages as ChatMessage[],
    });
    if (duplicateActiveTurn) {
      console.warn(
        '[Chat] Duplicate turn detected, reusing active stream',
        {
          chatId: id,
          logicalRequestId: duplicateActiveTurn.logicalRequestId,
          incomingMessageId: message?.id,
        },
      );
      const attached = await pipeCachedStreamToResponse({
        res,
        streamId: duplicateActiveTurn.streamId,
        streamReadyPromise: duplicateActiveTurn.streamReadyPromise,
      });
      if (attached) {
        return;
      }

      return res.status(409).json({
        error: 'Duplicate chat turn already in progress',
      });
    }

    clearActiveTurnRequest(id);

    // If message is provided, add it to the list and save it
    // If not (continuation/regeneration), just use previous messages
    let uiMessages: ChatMessage[];
    if (message) {
      activeTurnRequest = createActiveTurnRequest({
        logicalRequestId,
        message,
        previousMessages: previousMessages as ChatMessage[],
        streamId,
      });
      activeTurnRequests.set(id, activeTurnRequest);
      uiMessages = [...previousMessages, message];
      await saveMessages({
        messages: [
          {
            chatId: id,
            id: message.id,
            role: 'user',
            parts: message.parts,
            attachments: [],
            createdAt: new Date(),
            traceId: null,
          },
        ],
      });
    } else {
      // Continuation: use existing messages without adding new user message
      uiMessages = previousMessages as ChatMessage[];

      // For continuations with database enabled, save any updated assistant messages
      // This ensures tool-result parts (like MCP approval responses) are persisted
      if (dbAvailable && requestBody.previousMessages) {
        const assistantMessages = requestBody.previousMessages.filter(
          (m: ChatMessage) => m.role === 'assistant',
        );
        if (assistantMessages.length > 0) {
          await saveMessages({
            messages: assistantMessages.map((m: ChatMessage) => ({
              chatId: id,
              id: m.id,
              role: m.role,
              parts: m.parts,
              attachments: [],
              createdAt: m.metadata?.createdAt
                ? new Date(m.metadata.createdAt)
                : new Date(),
              traceId: null,
            })),
          });

          // Check if this is an MCP denial - if so, we're done (no need to call LLM)
          // Denial is indicated by a dynamic-tool part with state 'output-denied'
          // or with approval.approved === false
          const hasMcpDenial = requestBody.previousMessages?.some(
            (m: ChatMessage) =>
              m.parts?.some(
                (p) =>
                  p.type === 'dynamic-tool' &&
                  (p.state === 'output-denied' ||
                    ('approval' in p && p.approval?.approved === false)),
              ),
          );

          if (hasMcpDenial) {
            // We don't need to call the LLM because the user has denied the tool call
            res.end();
            return;
          }
        }
      }
    }

    // Clear any previous active stream for this chat
    streamCache.clearActiveStream(id);
    if (!message) {
      clearActiveTurnRequest(id);
    }

    let finalUsage: LanguageModelUsage | undefined;
    let traceId: string | null = null;
    let clarificationData: { reason: string; options: string[] } | null = null;

    if (isPlaywrightMockMode) {
      const mockTraceId = `tr-${id}`;
      const mockText = getMockResponseText(message);
      const stream = createUIMessageStream({
        originalMessages: uiMessages,
        generateId: generateUUID,
        execute: async ({ writer }) => {
          const messageId = generateUUID();
          const textId = generateUUID();
          writer.write({ type: 'start', messageId });
          writer.write({ type: 'start-step' });
          writer.write({ type: 'text-start', id: textId });
          writer.write({ type: 'text-delta', id: textId, delta: mockText });
          writer.write({ type: 'text-end', id: textId });
          writer.write({ type: 'finish-step' });
          writer.write({ type: 'data-traceId', data: mockTraceId });
        },
        onFinish: async ({ responseMessage }) => {
          storeMessageMeta(responseMessage.id, id, mockTraceId);
          try {
            await saveMessages({
              messages: [
                {
                  id: responseMessage.id,
                  role: responseMessage.role,
                  parts: responseMessage.parts,
                  createdAt: new Date(),
                  attachments: [],
                  chatId: id,
                  traceId: mockTraceId,
                },
              ],
            });
          } catch (err) {
            console.error('[mock onFinish] Failed to save assistant message:', err);
          }
          streamCache.clearActiveStream(id);
          if (
            activeTurnRequest &&
            activeTurnRequests.get(id)?.logicalRequestId ===
              activeTurnRequest.logicalRequestId
          ) {
            clearActiveTurnRequest(id);
          }
        },
      });

      pipeUIMessageStreamToResponse({
        stream,
        response: res,
        consumeSseStream({ stream }) {
          streamCache.storeStream({
            streamId,
            chatId: id,
            stream,
          });
          if (
            activeTurnRequest &&
            activeTurnRequests.get(id)?.logicalRequestId ===
              activeTurnRequest.logicalRequestId
          ) {
            activeTurnRequest.resolveStreamReady();
          }
        },
      });
      return;
    }

    const model = await myProvider.languageModel(selectedChatModel);
    const modelMessages = await convertToModelMessages(uiMessages);
    const chatAgentSettings = requestBody.agentSettings;
    const traceKind = message ? 'chat-turn' : 'continuation';
    const requestHeaders = {
      [CONTEXT_HEADER_CONVERSATION_ID]: id,
      [CONTEXT_HEADER_USER_ID]: session.user.email ?? session.user.id,
      'x-chat-request-id': logicalRequestId,
      'x-chat-user-message-id': message?.id ?? '',
      'x-agent-execution-mode': chatAgentSettings?.executionMode ?? 'parallel',
      'x-agent-synthesis-route': chatAgentSettings?.synthesisRoute ?? 'auto',
      'x-agent-clarification-sensitivity':
        chatAgentSettings?.clarificationSensitivity ?? 'medium',
      'x-agent-count-only': String(chatAgentSettings?.countOnly ?? true),
      'x-chat-request-kind': traceKind,
      'x-chat-trace-source': 'chat-route',
      'x-chat-retry-attempt': '0',
      ...(req.headers['x-forwarded-access-token']
        ? { 'x-forwarded-access-token': req.headers['x-forwarded-access-token'] as string }
        : {}),
    };

    const result = streamText({
      model,
      messages: modelMessages,
      providerOptions: {
        databricks: { includeTrace: true },
      },
      includeRawChunks: true,
      headers: requestHeaders,
      onChunk: ({ chunk }) => {
        if (chunk.type === 'raw') {
          const raw = chunk.rawValue as any;
          // Extract trace in Databricks serving endpoint output format, if present
          if (raw?.type === 'response.output_item.done') {
            const traceIdFromChunk =
              raw?.databricks_output?.trace?.info?.trace_id;
            if (typeof traceIdFromChunk === 'string') {
              traceId = traceIdFromChunk;
            }
          }
          // Extract clarification interrupt data, if present
          if (raw?.databricks_output?.clarification) {
            clarificationData = raw.databricks_output.clarification;
          }
          // Extract trace from MLflow AgentServer output format, if present
          if (!traceId && typeof raw?.trace_id === 'string') {
            traceId = raw.trace_id;
          }
        }
      },
      onFinish: ({ usage }) => {
        finalUsage = usage;
      },
    });

    /**
     * We manually read from toUIMessageStream instead of using writer.merge
     * so the execute promise (and thus the outer stream) stays alive if we
     * need to retry with a second streamText call after a streaming error.
     */
    const stream = createUIMessageStream({
      // Pass originalMessages so that continuation responses reuse the existing
      // assistant message ID. Without this, handleUIMessageStreamFinish generates
      // a fresh ID, causing the client to push a second assistant message instead
      // of replacing the existing one.
      originalMessages: uiMessages,
      // The DB Message.id column is typed as uuid, so we must generate UUIDs
      // rather than the AI SDK's default short-id format (e.g. "Xt8nZiQRj1fS4yiU").
      generateId: generateUUID,
      execute: async ({ writer }) => {
        // Manually drain the AI stream so we can append the traceId data part
        // after all model chunks are processed (traceId is captured via onChunk).
        // result.toUIMessageStream() converts TextStreamPart → UIMessageChunk:
        // - text-delta: maps TextStreamPart.text → UIMessageChunk.delta
        // - start-step/finish-step: strips extra fields
        // - finish: strips rawFinishReason/totalUsage
        // - raw: dropped (trace_id captured via onChunk above)
        const aiStream = result.toUIMessageStream<ChatMessage>({
          sendReasoning: true,
          sendSources: true,
          sendFinish: false,
          onError: (error) => {
            const msg =
              error instanceof Error ? error.message : String(error);
            writer.onError?.(error);
            return msg;
          },
        });

        const { failed } = await drainStreamToWriter(aiStream, writer);

        if (failed) {
          console.log('Streaming failed, retrying with fallback streamText...');
          const fallbackResult = await fallbackToStreamText(
            {
              model,
              messages: modelMessages,
              headers: {
                ...requestHeaders,
                'x-chat-request-kind': 'chat-fallback',
                'x-chat-original-request-kind': traceKind,
                'x-chat-retry-attempt': '1',
              },
            },
            writer,
          );

          finalUsage = fallbackResult?.usage;
          traceId = fallbackResult?.traceId ?? null;
          clarificationData = fallbackResult?.clarificationData ?? clarificationData;
        }

        // Write clarification data so the client can show a structured modal.
        if (clarificationData) {
          writer.write({ type: 'data-clarification', data: clarificationData });
          // Clarification turns intentionally pause waiting for a follow-up.
          // Keep the backend checkpoint resumable, but stop advertising this
          // SSE stream as an active resumable stream so reconnects do not
          // replay the old clarification payload and popup.
          streamCache.clearActiveStream(id);
        }
        // Write traceId so the client knows whether feedback is supported.
        writer.write({ type: 'data-traceId', data: traceId });
      },
      onFinish: async ({ responseMessage }) => {
        // Store in-memory for ephemeral mode (also useful when DB is available)
        storeMessageMeta(responseMessage.id, id, traceId);

        try {
          await saveMessages({
            messages: [
              {
                id: responseMessage.id,
                role: responseMessage.role,
                parts: responseMessage.parts,
                createdAt: new Date(),
                attachments: [],
                chatId: id,
                traceId, // Store trace ID for feedback
              },
            ],
          });
        } catch (err) {
          console.error('[onFinish] Failed to save assistant message:', err);
        }

        if (finalUsage) {
          try {
            await updateChatLastContextById({
              chatId: id,
              context: toV3Usage(finalUsage),
            });
          } catch (err) {
            console.warn('Unable to persist last usage for chat', id, err);
          }
        }

        streamCache.clearActiveStream(id);
        if (
          activeTurnRequest &&
          activeTurnRequests.get(id)?.logicalRequestId ===
            activeTurnRequest.logicalRequestId
        ) {
          clearActiveTurnRequest(id);
        }
      },
    });

    pipeUIMessageStreamToResponse({
      stream,
      response: res,
      consumeSseStream({ stream }) {
        streamCache.storeStream({
          streamId,
          chatId: id,
          stream,
        });
        if (
          activeTurnRequest &&
          activeTurnRequests.get(id)?.logicalRequestId ===
            activeTurnRequest.logicalRequestId
        ) {
          activeTurnRequest.resolveStreamReady();
        }
      },
    });
  } catch (error) {
    if (
      activeTurnRequest &&
      activeTurnRequests.get(requestBody.id)?.logicalRequestId ===
        activeTurnRequest.logicalRequestId
    ) {
      activeTurnRequest.resolveStreamReady();
      clearActiveTurnRequest(requestBody.id);
    }

    console.error('[Chat] Caught error in chat API:', {
      errorType: error?.constructor?.name,
      message: error instanceof Error ? error.message : String(error),
      stack: error instanceof Error ? error.stack : undefined,
      error,
    });

    if (error instanceof ChatSDKError) {
      const response = error.toResponse();
      return res.status(response.status).json(response.json);
    }

    const chatError = new ChatSDKError('offline:chat');
    const response = chatError.toResponse();
    return res.status(response.status).json(response.json);
  }
});

/**
 * DELETE /api/chat?id=:id - Delete a chat
 */
chatRouter.delete(
  '/:id',
  [requireAuth, requireChatAccess],
  async (req: Request, res: Response) => {
    const id = getIdFromRequest(req);
    if (!id) return;

    const deletedChat = await deleteChatById({ id });
    return res.status(200).json(deletedChat);
  },
);

/**
 * GET /api/chat/:id
 */

chatRouter.get(
  '/:id',
  [requireAuth, requireChatAccess],
  async (req: Request, res: Response) => {
    const id = getIdFromRequest(req);
    if (!id) return;

    const { chat } = await checkChatAccess(id, req.session?.user.id);

    return res.status(200).json(chat);
  },
);

/**
 * GET /api/chat/:id/stream - Resume a stream
 */
chatRouter.get(
  '/:id/stream',
  [requireAuth],
  async (req: Request, res: Response) => {
    const chatId = getIdFromRequest(req);
    if (!chatId) return;
    const cursor = req.headers['x-resume-stream-cursor'] as string;

    console.log(`[Stream Resume] Cursor: ${cursor}`);

    console.log(`[Stream Resume] GET request for chat ${chatId}`);

    // Check if there's an active stream for this chat first
    const streamId = streamCache.getActiveStreamId(chatId);

    if (!streamId) {
      console.log(`[Stream Resume] No active stream for chat ${chatId}`);
      const streamError = new ChatSDKError('empty:stream');
      const response = streamError.toResponse();
      return res.status(response.status).json(response.json);
    }

    const { allowed, reason } = await checkChatAccess(
      chatId,
      req.session?.user.id,
    );

    // If chat doesn't exist in DB, it's a temporary chat from the homepage - allow it
    if (reason === 'not_found') {
      console.log(
        `[Stream Resume] Resuming stream for temporary chat ${chatId} (not yet in DB)`,
      );
    } else if (!allowed) {
      console.log(
        `[Stream Resume] User ${req.session?.user.id} does not have access to chat ${chatId} (reason: ${reason})`,
      );
      const streamError = new ChatSDKError('forbidden:chat', reason);
      const response = streamError.toResponse();
      return res.status(response.status).json(response.json);
    }

    // Get all cached chunks for this stream
    const stream = streamCache.getStream(streamId, {
      cursor: cursor ? Number.parseInt(cursor) : undefined,
    });

    if (!stream) {
      console.log(`[Stream Resume] No stream found for ${streamId}`);
      const streamError = new ChatSDKError('empty:stream');
      const response = streamError.toResponse();
      return res.status(response.status).json(response.json);
    }

    console.log(`[Stream Resume] Resuming stream ${streamId}`);

    // Set headers for SSE
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('Connection', 'keep-alive');

    // Pipe the cached stream directly to the response
    stream.pipe(res);

    // Handle stream errors
    stream.on('error', (error) => {
      console.error('[Stream Resume] Stream error:', error);
      if (!res.headersSent) {
        res.status(500).end();
      }
    });
  },
);

/**
 * POST /api/chat/title - Generate title from message
 */
chatRouter.post('/title', requireAuth, async (req: Request, res: Response) => {
  try {
    const { message } = req.body;
    const title = await generateTitleFromUserMessage({ message });
    res.json({ title });
  } catch (error) {
    console.error('Error generating title:', error);
    res.status(500).json({ error: 'Failed to generate title' });
  }
});

/**
 * PATCH /api/chat/:id/settings - Update per-chat agent settings
 */
chatRouter.patch(
  '/:id/settings',
  [requireAuth, requireChatAccess],
  async (req: Request, res: Response) => {
    try {
      const id = getIdFromRequest(req);
      if (!id) return;

      const parsed = chatAgentSettingsSchema.safeParse(req.body);
      if (!parsed.success) {
        return res.status(400).json({ error: 'Invalid agent settings' });
      }

      const {
        executionMode,
        synthesisRoute,
        clarificationSensitivity,
        countOnly,
      } = parsed.data;
      await updateChatAgentSettingsById({
        chatId: id,
        executionMode,
        synthesisRoute,
        clarificationSensitivity,
        countOnly,
      });

      res.json({ success: true });
    } catch (error) {
      console.error('Error updating chat agent settings:', error);
      res.status(500).json({ error: 'Failed to update chat agent settings' });
    }
  },
);

/**
 * PATCH /api/chat/:id/visibility - Update chat visibility
 */
chatRouter.patch(
  '/:id/visibility',
  [requireAuth, requireChatAccess],
  async (req: Request, res: Response) => {
    try {
      const id = getIdFromRequest(req);
      if (!id) return;
      const { visibility } = req.body;

      if (!visibility || !['public', 'private'].includes(visibility)) {
        return res.status(400).json({ error: 'Invalid visibility type' });
      }

      await updateChatVisiblityById({ chatId: id, visibility });
      res.json({ success: true });
    } catch (error) {
      console.error('Error updating visibility:', error);
      res.status(500).json({ error: 'Failed to update visibility' });
    }
  },
);

// Helper function to generate title from user message
async function generateTitleFromUserMessage({
  message,
  maxMessageLength = 256,
}: {
  message: ChatMessage;
  maxMessageLength?: number;
}) {
  const model = await myProvider.languageModel('title-model');

  // Truncate each text part to the maxMessageLength
  const truncatedMessage = {
    ...message,
    parts: message.parts.map((part) =>
      part.type === 'text'
        ? { ...part, text: part.text.slice(0, maxMessageLength) }
        : part,
    ),
  };
  const titleInput = truncatedMessage.parts
    .filter((part) => part.type === 'text')
    .map((part) => part.text.trim())
    .filter(Boolean)
    .join('\n\n');

  const { text: title } = await generateText({
    model,
    system: `\n
    - you will generate a short title based on the first message a user begins a conversation with
    - ensure it is not more than 80 characters long
    - the title should be a summary of the user's message
    - do not use quotes or colons. do not include other expository content ("I'll help...")`,
    // Keep title-generation inputs recognizable in provider-side traces.
    prompt: titleInput
      ? `TITLE_GENERATION_REQUEST\n${titleInput}`
      : `TITLE_GENERATION_REQUEST\n${JSON.stringify(truncatedMessage)}`,
  });

  return title;
}

function truncatePreserveWords(input: string, maxLength: number): string {
  if (maxLength <= 0) return '';
  if (input.length <= maxLength) return input;

  // Take the raw slice first
  const slice = input.slice(0, maxLength);

  // Find the last whitespace within the slice
  const lastSpaceIndex = slice.lastIndexOf(' ');

  // If no whitespace found, we must break mid-word
  if (lastSpaceIndex === -1) {
    return slice;
  }

  // If the whitespace is too close to the start (e.g., leading space),
  // fallback to mid-word break to avoid returning an empty string
  if (lastSpaceIndex === 0) {
    return slice;
  }

  return slice.slice(0, lastSpaceIndex);
}

