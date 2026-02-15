import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState } from 'react';
import { useBuildGraph, useGetGraphDataSuspense, useGetGraphBuildLogs } from '@/lib/api';
import { selector } from '@/lib/selector';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft, Terminal } from 'lucide-react';
import CytoscapeComponent from 'react-cytoscapejs';

export const Route = createFileRoute('/_sidebar/graph-explorer')({
  component: () => (
    <div>
      <h1 className="text-3xl font-bold mb-6">Explore Graph</h1>
      <GraphExplorerContent />
    </div>
  ),
});

function GraphExplorerContent() {
  const [graphBuilt, setGraphBuilt] = useState(false);
  const buildGraphMutation = useBuildGraph();
  const navigate = useNavigate();

  // Poll logs while building
  const { data: logs } = useGetGraphBuildLogs({
    query: {
      refetchInterval: (query) => {
        return buildGraphMutation.isPending ? 1000 : false;
      },
      enabled: buildGraphMutation.isPending || graphBuilt
    }
  });

  const handleBuildGraph = async () => {
    await buildGraphMutation.mutateAsync();
    setGraphBuilt(true);
  };

  return (
    <div className="space-y-6">
      {!graphBuilt && (
        <Card>
          <CardHeader>
            <CardTitle>Build Table Relationship Graph</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <Button onClick={handleBuildGraph} disabled={buildGraphMutation.isPending}>
              {buildGraphMutation.isPending ? 'Building...' : 'Build Graph'}
            </Button>

            {buildGraphMutation.isPending && (
              <div className="mt-4 space-y-2">
                <div className="flex items-center gap-2 text-sm font-medium text-slate-700 dark:text-slate-300">
                  <Terminal size={16} />
                  <span>Build Logs:</span>
                </div>
                <div className="bg-slate-950 rounded-lg p-4 font-mono text-xs text-slate-300 h-64 overflow-y-auto space-y-1 border border-slate-800 shadow-inner">
                  {logs?.map((log, i) => (
                    <div key={i} className="flex gap-3">
                      <span className="text-slate-500 shrink-0">[{log.timestamp}]</span>
                      <span className={
                        log.level === 'error' ? 'text-red-400' :
                        log.level === 'success' ? 'text-green-400' :
                        'text-slate-300'
                      }>
                        {log.message}
                      </span>
                    </div>
                  ))}
                  {(!logs || logs.length === 0) && (
                    <div className="animate-pulse text-slate-500 italic">Initializing build process...</div>
                  )}
                  <div className="h-1" /> {/* Spacer for scroll to bottom */}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {graphBuilt && (
        <Suspense fallback={<Skeleton className="h-96 w-full" />}>
          <GraphVisualization />
        </Suspense>
      )}

      <div className="flex gap-4">
        <Button variant="outline" onClick={() => navigate({ to: '/enrichment' })}>
          <ArrowLeft size={16} /> Back
        </Button>
        {graphBuilt && (
          <Button onClick={() => navigate({ to: '/genie-builder' })}>
            Next: Build Rooms →
          </Button>
        )}
      </div>
    </div>
  );
}

function GraphVisualization() {
  const { data: graphData } = useGetGraphDataSuspense(selector());

  const cytoscapeStylesheet = [
    {
      selector: 'node',
      style: {
        'background-color': '#3b82f6',
        'label': 'data(label)',
        'color': '#1e293b',
        'text-halign': 'center',
        'text-valign': 'bottom',
        'text-margin-y': '5px',
        'font-size': '10px',
        'width': '30px',
        'height': '30px',
        'font-weight': 'bold'
      },
    },
    {
      selector: 'edge',
      style: {
        'width': 'mapData(weight, 1, 10, 1, 5)',
        'line-color': '#cbd5e1',
        'curve-style': 'bezier',
        'opacity': 0.6
      },
    },
    {
      // Semantic edges (LLM-discovered relationships)
      selector: 'edge[?types*="semantic"]',
      style: {
        'line-color': '#a855f7',
        'line-style': 'dashed',
        'width': 3,
        'opacity': 0.8,
        'target-arrow-shape': 'triangle',
        'target-arrow-color': '#a855f7'
      },
    },
    {
      selector: 'node[schema = "demo_mixed"]',
      style: { 'background-color': '#ef4444' }
    },
    {
      selector: 'node[schema = "claims"]',
      style: { 'background-color': '#10b981' }
    },
    {
      selector: 'node[schema = "drug_discovery"]',
      style: { 'background-color': '#f59e0b' }
    },
  ];

  // Count semantic edges
  const semanticEdgeCount = graphData.elements.filter(
    (el: any) => el.data?.source && el.data?.types?.includes('semantic')
  ).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Table Relationship Graph ({graphData.node_count} tables, {graphData.edge_count} relationships)
        </CardTitle>
        {semanticEdgeCount > 0 && (
          <div className="text-sm text-slate-600 dark:text-slate-400 mt-2">
            <span className="inline-flex items-center gap-2">
              <span className="w-8 h-0.5 bg-slate-400"></span>
              <span>Structural</span>
              <span className="w-8 h-0.5 bg-purple-500 border-dashed border-t-2 border-purple-500"></span>
              <span>Semantic ({semanticEdgeCount} LLM-discovered)</span>
            </span>
          </div>
        )}
      </CardHeader>
      <CardContent>
        <div className="border rounded-lg" style={{ height: '600px' }}>
          <CytoscapeComponent
            elements={graphData.elements}
            style={{ width: '100%', height: '100%' }}
            stylesheet={cytoscapeStylesheet}
            layout={{ name: 'cose', animate: true }}
          />
        </div>
      </CardContent>
    </Card>
  );
}
