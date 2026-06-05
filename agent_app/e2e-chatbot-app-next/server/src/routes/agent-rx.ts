import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';
import { authMiddleware, requireAuth } from '../middleware/auth';
import { ChatSDKError } from '@chat-template/core/errors';

export const agentRxRouter: RouterType = Router();

agentRxRouter.use(authMiddleware);

/**
 * POST /api/agent-rx - AgentRx (knowledge-base management) admin endpoint.
 *
 * AgentRx is a sibling agent to the super-agent. It manages the
 * Genie-space knowledge base and analyses MLflow feedback. We proxy to
 * the Python AgentServer's /api/agent-rx/stream endpoint and stream
 * server-sent events back to the client.
 *
 * Body:
 *   - message: string  (required) - admin's latest natural-language request
 *   - history: {role, content}[]  (optional) - prior turns for multi-turn memory
 *
 * Streaming: emits SSE frames of shape `data: {json}\n\n` with `type` of
 *   "tool_call" | "tool_result" | "final" | "error", terminated by
 *   `data: [DONE]\n\n`.
 */
agentRxRouter.post('/', requireAuth, async (req: Request, res: Response) => {
  const { message, history } = req.body ?? {};

  if (!message || typeof message !== 'string' || message.trim().length === 0) {
    const error = new ChatSDKError('bad_request:api');
    const response = error.toResponse();
    return res.status(response.status).json(response.json);
  }

  // Prior turns ({ role, content }) for multi-turn memory; sanitised + bounded.
  const safeHistory = Array.isArray(history)
    ? history
        .filter(
          (t: unknown): t is { role: string; content: string } =>
            !!t &&
            typeof (t as { role?: unknown }).role === 'string' &&
            typeof (t as { content?: unknown }).content === 'string' &&
            (t as { content: string }).content.trim().length > 0,
        )
        .map((t) => ({ role: t.role, content: t.content }))
        .slice(-50)
    : [];

  const agentBackendUrl = process.env.API_PROXY;
  if (!agentBackendUrl) {
    return res.status(503).json({
      error: 'service_unavailable',
      message:
        'AgentRx requires the Python AgentServer. Set API_PROXY to its base URL.',
    });
  }

  // API_PROXY points at the AgentServer base, possibly with a trailing
  // /invocations. Strip the trailing /invocations if present so we can hit
  // /api/agent-rx/stream on the same host.
  const base = agentBackendUrl.replace(/\/invocations\/?$/, '');
  const upstream = `${base.replace(/\/$/, '')}/api/agent-rx/stream`;

  try {
    const response = await fetch(upstream, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({ message, history: safeHistory }),
    });

    if (!response.ok) {
      const text = await response.text().catch(() => '');
      return res.status(response.status).json({
        error: 'upstream_error',
        status: response.status,
        body: text.slice(0, 4000),
      });
    }

    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache, no-transform');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders?.();

    if (response.body) {
      const reader = response.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        res.write(value);
      }
    }
    res.end();
  } catch (error) {
    console.error('[/api/agent-rx] proxy error:', error);
    res.status(502).json({
      error: 'proxy_error',
      message: error instanceof Error ? error.message : String(error),
    });
  }
});

export default agentRxRouter;
