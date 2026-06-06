import {
  Router,
  type Request,
  type Response,
  type Router as RouterType,
} from 'express';

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
