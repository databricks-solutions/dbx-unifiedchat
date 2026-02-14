import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState } from 'react';
import { useBuildGraph, useGetGraphDataSuspense } from '@/lib/api';
import { selector } from '@/lib/selector';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft } from 'lucide-react';
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
          <CardContent>
            <Button onClick={handleBuildGraph} disabled={buildGraphMutation.isPending}>
              {buildGraphMutation.isPending ? 'Building...' : 'Build Graph'}
            </Button>
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
        'color': '#fff',
        'text-halign': 'center',
        'text-valign': 'center',
        'font-size': '12px',
      },
    },
    {
      selector: 'edge',
      style: {
        'width': 'data(weight)',
        'line-color': '#94a3b8',
        'target-arrow-color': '#94a3b8',
        'target-arrow-shape': 'triangle',
        'curve-style': 'bezier',
      },
    },
  ];

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Table Relationship Graph ({graphData.node_count} tables, {graphData.edge_count} relationships)
        </CardTitle>
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
