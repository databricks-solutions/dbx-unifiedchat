import { useCallback, useEffect, useRef, useState } from 'react';
import { useSession } from '@/contexts/SessionContext';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Streamdown } from 'streamdown';

type AgentRxEvent =
  | { type: 'tool_call'; tool: string; args: Record<string, unknown> }
  | { type: 'tool_result'; tool: string; result: string }
  | { type: 'final'; content: string }
  | { type: 'error'; message: string };

type TraceEntry = AgentRxEvent & { id: number };

type Turn = {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  trace: TraceEntry[];
  error?: string;
  running?: boolean;
};

const SUGGESTIONS: string[] = [
  'List the Genie spaces currently indexed in the knowledge base.',
  'List the tables and columns in the NYC Taxi Trip Data Analysis space.',
  'List the benchmark questions in the NYC Taxi Trip Data Analysis space.',
  'Summarise the last 7 days of negative feedback.',
];

export default function AgentRxPage() {
  const { session } = useSession();
  const [input, setInput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const idCounter = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const nextId = () => ++idCounter.current;

  // Keep the latest turn in view as it streams.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [turns]);

  const updateTurn = useCallback((id: number, patch: (t: Turn) => Turn) => {
    setTurns((prev) => prev.map((t) => (t.id === id ? patch(t) : t)));
  }, []);

  const submit = useCallback(
    async (message: string) => {
      const text = message.trim();
      if (!text || isRunning) return;
      setInput('');
      setIsRunning(true);

      // History = all prior turns with content (skip the empty assistant
      // placeholder we are about to add, and any errored/blank turns).
      const history = turns
        .filter((t) => t.content.trim().length > 0)
        .map((t) => ({ role: t.role, content: t.content }));

      const userTurn: Turn = { id: nextId(), role: 'user', content: text, trace: [] };
      const assistantId = nextId();
      const assistantTurn: Turn = {
        id: assistantId,
        role: 'assistant',
        content: '',
        trace: [],
        running: true,
      };
      setTurns((prev) => [...prev, userTurn, assistantTurn]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await fetch('/api/agent-rx', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
          },
          body: JSON.stringify({ message: text, history }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          const detail = await response.text().catch(() => '');
          throw new Error(`AgentRx request failed (${response.status}): ${detail.slice(0, 400)}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            for (const line of frame.split('\n')) {
              if (!line.startsWith('data:')) continue;
              const payload = line.slice(5).trim();
              if (!payload || payload === '[DONE]') continue;
              let event: AgentRxEvent;
              try {
                event = JSON.parse(payload) as AgentRxEvent;
              } catch (err) {
                console.warn('[AgentRx] failed to parse SSE frame:', payload, err);
                continue;
              }
              if (event.type === 'final') {
                updateTurn(assistantId, (t) => ({ ...t, content: event.content }));
              } else if (event.type === 'error') {
                updateTurn(assistantId, (t) => ({ ...t, error: event.message }));
              } else {
                updateTurn(assistantId, (t) => ({
                  ...t,
                  trace: [...t.trace, { ...event, id: nextId() }],
                }));
              }
            }
          }
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          updateTurn(assistantId, (t) => ({ ...t, error: (err as Error).message ?? String(err) }));
        }
      } finally {
        updateTurn(assistantId, (t) => ({ ...t, running: false }));
        setIsRunning(false);
        abortRef.current = null;
      }
    },
    [isRunning, turns, updateTurn],
  );

  const handleSubmit = useCallback(() => submit(input), [input, submit]);

  const newConversation = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setTurns([]);
    setInput('');
    setIsRunning(false);
  }, []);

  if (!session?.user) {
    return null;
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col p-6">
      <header className="flex items-start justify-between gap-4 pb-4">
        <div>
          <h1 className="font-bold text-2xl">AgentRx — Knowledge base</h1>
          <p className="text-muted-foreground text-sm">
            Admin agent for managing Genie spaces (indexing, benchmark &amp; sample
            questions, column visibility) and analysing user feedback. Conversational —
            follow-ups remember earlier turns.
          </p>
        </div>
        {turns.length > 0 && (
          <Button type="button" variant="outline" size="sm" onClick={newConversation}>
            New conversation
          </Button>
        )}
      </header>

      <div ref={scrollRef} className="flex flex-1 flex-col gap-4 overflow-y-auto pb-4">
        {turns.length === 0 ? (
          <div className="flex flex-col gap-3 pt-6">
            <p className="text-muted-foreground text-sm">Try one of these, or ask your own:</p>
            <div className="flex flex-wrap gap-2">
              {SUGGESTIONS.map((s) => (
                <Button
                  key={s}
                  variant="outline"
                  size="sm"
                  type="button"
                  disabled={isRunning}
                  onClick={() => submit(s)}
                >
                  {s}
                </Button>
              ))}
            </div>
          </div>
        ) : (
          turns.map((t) => <TurnView key={t.id} turn={t} />)
        )}
      </div>

      <div className="flex flex-col gap-2 border-t pt-3">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              handleSubmit();
            }
          }}
          placeholder="Ask AgentRx to list / add / remove benchmark or sample questions, hide columns, manage the index, or analyse feedback…"
          rows={2}
          disabled={isRunning}
        />
        <div className="flex justify-end">
          <Button type="button" onClick={handleSubmit} disabled={!input.trim() || isRunning}>
            {isRunning ? 'Running…' : 'Send'}
          </Button>
        </div>
      </div>
    </div>
  );
}

function TurnView({ turn }: { turn: Turn }) {
  if (turn.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-primary px-4 py-2 text-primary-foreground">
          {turn.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {turn.trace.length > 0 && (
        <details className="rounded-md border bg-muted/30 p-2 text-sm" open={turn.running}>
          <summary className="cursor-pointer font-semibold text-muted-foreground">
            ReAct trace ({turn.trace.length})
          </summary>
          <div className="mt-2 flex flex-col gap-1.5">
            {turn.trace.map((entry) => (
              <TraceLine key={entry.id} entry={entry} />
            ))}
          </div>
        </details>
      )}

      {turn.running && !turn.content && !turn.error && (
        <div className="text-muted-foreground text-sm">Thinking…</div>
      )}

      {turn.content && (
        <div className="prose prose-sm max-w-none rounded-md border bg-background p-4">
          <Streamdown>{turn.content}</Streamdown>
        </div>
      )}

      {turn.error && <div className="text-destructive text-sm">⚠ {turn.error}</div>}
    </div>
  );
}

function TraceLine({ entry }: { entry: TraceEntry }) {
  switch (entry.type) {
    case 'tool_call':
      return (
        <div className="text-sm">
          <span className="font-mono text-blue-600">→ {entry.tool}</span>{' '}
          <span className="text-muted-foreground">{JSON.stringify(entry.args)}</span>
        </div>
      );
    case 'tool_result':
      return (
        <details className="text-sm">
          <summary className="cursor-pointer font-mono text-emerald-700">← {entry.tool}</summary>
          <pre className="mt-1 whitespace-pre-wrap break-all text-muted-foreground text-xs">
            {entry.result}
          </pre>
        </details>
      );
    case 'error':
      return <div className="text-destructive text-sm">⚠ {entry.message}</div>;
    default:
      return null;
  }
}
