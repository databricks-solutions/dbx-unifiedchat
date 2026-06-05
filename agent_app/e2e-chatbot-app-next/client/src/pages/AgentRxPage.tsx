import { useCallback, useRef, useState } from 'react';
import { useSession } from '@/contexts/SessionContext';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';

type AgentRxEvent =
  | { type: 'tool_call'; tool: string; args: Record<string, unknown> }
  | { type: 'tool_result'; tool: string; result: string }
  | { type: 'final'; content: string }
  | { type: 'error'; message: string };

type TraceEntry = AgentRxEvent & { id: number };

const SUGGESTIONS: string[] = [
  'List the Genie spaces currently indexed in the knowledge base.',
  'Summarise the last 7 days of negative feedback.',
  'Sample the most recent failing traces and suggest a fix.',
];

export default function AgentRxPage() {
  const { session } = useSession();
  const [input, setInput] = useState('');
  const [isRunning, setIsRunning] = useState(false);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [finalContent, setFinalContent] = useState<string | null>(null);
  const idCounter = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    setTrace([]);
    setFinalContent(null);
  }, []);

  const submit = useCallback(
    async (message: string) => {
      if (!message.trim() || isRunning) return;
      reset();
      setIsRunning(true);
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await fetch('/api/agent-rx', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
          },
          body: JSON.stringify({ message }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          const text = await response.text().catch(() => '');
          throw new Error(
            `AgentRx request failed (${response.status}): ${text.slice(0, 400)}`,
          );
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // SSE frames are separated by a blank line.
          const frames = buffer.split('\n\n');
          buffer = frames.pop() ?? '';
          for (const frame of frames) {
            for (const line of frame.split('\n')) {
              if (!line.startsWith('data:')) continue;
              const payload = line.slice(5).trim();
              if (!payload || payload === '[DONE]') continue;
              try {
                const event = JSON.parse(payload) as AgentRxEvent;
                const id = ++idCounter.current;
                if (event.type === 'final') {
                  setFinalContent(event.content);
                } else {
                  setTrace((prev) => [...prev, { ...event, id }]);
                }
              } catch (err) {
                console.warn('[AgentRx] failed to parse SSE frame:', payload, err);
              }
            }
          }
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          setTrace((prev) => [
            ...prev,
            {
              id: ++idCounter.current,
              type: 'error',
              message: (err as Error).message ?? String(err),
            },
          ]);
        }
      } finally {
        setIsRunning(false);
        abortRef.current = null;
      }
    },
    [isRunning, reset],
  );

  const handleSubmit = useCallback(() => {
    submit(input);
  }, [input, submit]);

  if (!session?.user) {
    return null;
  }

  return (
    <div className="mx-auto flex h-full w-full max-w-3xl flex-col gap-4 p-6">
      <header>
        <h1 className="font-bold text-2xl">AgentRx — Knowledge base</h1>
        <p className="text-muted-foreground text-sm">
          Admin agent for managing which Genie spaces the system can answer
          questions about, and for analysing user feedback.
        </p>
      </header>

      <section className="flex flex-col gap-2">
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <Button
              key={s}
              variant="outline"
              size="sm"
              type="button"
              disabled={isRunning}
              onClick={() => {
                setInput(s);
                submit(s);
              }}
            >
              {s}
            </Button>
          ))}
        </div>
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask AgentRx to list / add / remove indexed Genie spaces, or analyse recent feedback…"
          rows={3}
          disabled={isRunning}
        />
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="ghost"
            onClick={reset}
            disabled={isRunning && abortRef.current === null}
          >
            Clear
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={!input.trim() || isRunning}
          >
            {isRunning ? 'Running…' : 'Run AgentRx'}
          </Button>
        </div>
      </section>

      {trace.length > 0 && (
        <section className="flex flex-col gap-2">
          <h2 className="font-semibold text-lg">ReAct trace</h2>
          <div className="flex flex-col gap-2 rounded-md border bg-muted/30 p-3">
            {trace.map((entry) => (
              <TraceLine key={entry.id} entry={entry} />
            ))}
          </div>
        </section>
      )}

      {finalContent && (
        <section className="flex flex-col gap-2">
          <h2 className="font-semibold text-lg">Result</h2>
          <div className="prose prose-sm max-w-none whitespace-pre-wrap rounded-md border bg-background p-4">
            {finalContent}
          </div>
        </section>
      )}
    </div>
  );
}

function TraceLine({ entry }: { entry: TraceEntry }) {
  switch (entry.type) {
    case 'tool_call':
      return (
        <div className="text-sm">
          <span className="font-mono text-blue-600">→ {entry.tool}</span>{' '}
          <span className="text-muted-foreground">
            {JSON.stringify(entry.args)}
          </span>
        </div>
      );
    case 'tool_result':
      return (
        <details className="text-sm">
          <summary className="cursor-pointer font-mono text-emerald-700">
            ← {entry.tool}
          </summary>
          <pre className="mt-1 whitespace-pre-wrap break-all text-muted-foreground text-xs">
            {entry.result}
          </pre>
        </details>
      );
    case 'error':
      return (
        <div className="text-destructive text-sm">⚠ {entry.message}</div>
      );
    default:
      return null;
  }
}
