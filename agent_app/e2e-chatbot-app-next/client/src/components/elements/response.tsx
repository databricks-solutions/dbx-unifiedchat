import { type ComponentProps, lazy, memo, Suspense, useMemo } from 'react';
import { DatabricksMessageCitationStreamdownIntegration } from '../databricks-message-citation';
import { Streamdown } from 'streamdown';

const InteractiveChart = lazy(() =>
  import('./interactive-chart').then((m) => ({ default: m.InteractiveChart })),
);

function EChartsCodeBlock(props: { className?: string; children?: string }) {
  const { className, children } = props;
  if (className === 'language-echarts-chart' && children) {
    try {
      const spec = JSON.parse(children);
      return (
        <Suspense fallback={<div className="h-[400px] animate-pulse rounded bg-zinc-100 dark:bg-zinc-800" />}>
          <InteractiveChart spec={spec} />
        </Suspense>
      );
    } catch {
      // fall through to default code block
    }
  }
  return (
    <pre>
      <code className={className}>{children}</code>
    </pre>
  );
}

type ResponseProps = ComponentProps<typeof Streamdown>;

export const Response = memo(
  (props: ResponseProps) => {
    return (
      <Streamdown
        components={{
          a: DatabricksMessageCitationStreamdownIntegration,
          code: EChartsCodeBlock,
        }}
        className="flex flex-col gap-4"
        {...props}
      />
    );
  },
  (prevProps, nextProps) => prevProps.children === nextProps.children,
);

Response.displayName = 'Response';
