import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState } from 'react';
import { useGetSelectionSuspense, useRunEnrichment, useGetEnrichmentStatusSuspense, useListEnrichmentResultsSuspense } from '@/lib/api';
import { selector } from '@/lib/selector';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft } from 'lucide-react';

export const Route = createFileRoute('/_sidebar/enrichment')({
  component: () => (
    <div>
      <h1 className="text-3xl font-bold mb-6">Enrich Tables</h1>
      <Suspense fallback={<EnrichmentSkeleton />}>
        <EnrichmentView />
      </Suspense>
    </div>
  ),
});

function EnrichmentView() {
  const { data: selection } = useGetSelectionSuspense(selector());
  const [jobId, setJobId] = useState<string | null>(null);
  const runEnrichmentMutation = useRunEnrichment();
  const navigate = useNavigate();

  const handleRunEnrichment = async () => {
    const result = await runEnrichmentMutation.mutateAsync({
      table_fqns: selection.table_fqns,
    });
    setJobId(result.job_id);
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Selected Tables ({selection.count})</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2 mb-4">
            {selection.table_fqns.slice(0, 10).map((fqn) => (
              <div key={fqn} className="text-sm p-2 bg-accent rounded">{fqn}</div>
            ))}
            {selection.count > 10 && (
              <p className="text-sm text-muted-foreground">...and {selection.count - 10} more</p>
            )}
          </div>

          {!jobId && (
            <Button onClick={handleRunEnrichment} disabled={runEnrichmentMutation.isPending}>
              {runEnrichmentMutation.isPending ? 'Starting...' : 'Run Enrichment'}
            </Button>
          )}
        </CardContent>
      </Card>

      {jobId && (
        <Suspense fallback={<Skeleton className="h-32 w-full" />}>
          <EnrichmentProgress jobId={jobId} />
        </Suspense>
      )}

      {jobId && (
        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <EnrichmentResults />
        </Suspense>
      )}

      <div className="flex gap-4">
        <Button variant="outline" onClick={() => navigate({ to: '/catalog-browser' })}>
          <ArrowLeft size={16} /> Back
        </Button>
        {jobId && (
          <Button onClick={() => navigate({ to: '/graph-explorer' })}>
            Next: Explore Graph →
          </Button>
        )}
      </div>
    </div>
  );
}

function EnrichmentProgress({ jobId }: { jobId: string }) {
  const { data: status } = useGetEnrichmentStatusSuspense(jobId, selector());

  return (
    <Card>
      <CardHeader>
        <CardTitle>Enrichment Progress</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          <div className="flex justify-between text-sm">
            <span>Status: {status.status}</span>
            <span>{status.progress} / {status.total}</span>
          </div>
          <div className="w-full bg-secondary rounded-full h-2">
            <div
              className="bg-primary h-2 rounded-full transition-all"
              style={{ width: `${(status.progress / status.total) * 100}%` }}
            />
          </div>
          {status.error && (
            <p className="text-sm text-destructive">{status.error}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function EnrichmentResults() {
  const { data: results } = useListEnrichmentResultsSuspense(selector());

  return (
    <Card>
      <CardHeader>
        <CardTitle>Enrichment Results</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border">
          <table className="w-full">
            <thead>
              <tr className="border-b">
                <th className="text-left p-3">Table</th>
                <th className="text-right p-3">Columns</th>
                <th className="text-center p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <tr key={result.fqn} className="border-b last:border-0">
                  <td className="p-3 text-sm">{result.fqn}</td>
                  <td className="p-3 text-sm text-right">{result.column_count}</td>
                  <td className="p-3 text-center">
                    {result.enriched ? '✓' : '✗'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

function EnrichmentSkeleton() {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <Skeleton className="h-8 w-48" />
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {[...Array(5)].map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
