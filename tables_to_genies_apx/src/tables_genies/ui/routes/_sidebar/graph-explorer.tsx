import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState, useCallback, useMemo, useRef, useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useBuildGraph, useGetGraphData, useGetGraphDataSuspense, useGetGraphBuildLogs, useCreateGenieRoom, useListGenieRoomsSuspense, useDeleteGenieRoom, useGenerateFromCommunities } from '@/lib/api';
import { selector } from '@/lib/selector';
import { loadState, saveState } from '@/lib/workflow-state';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ArrowLeft, Terminal, X, ChevronDown, ChevronRight, Plus, Layers, Info, Minus, Sparkles, Trash2 } from 'lucide-react';
import { 
  ReactFlow, 
  Background, 
  BackgroundVariant,
  Controls, 
  MiniMap,
  Node,
  Edge,
  NodeTypes,
  MarkerType,
  useNodesState,
  useEdgesState,
  Panel,
  EdgeTypes,
  BaseEdge,
  EdgeLabelRenderer,
  getStraightPath,
  SelectionMode,
  Handle,
  Position,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from '@dagrejs/dagre';

export const Route = createFileRoute('/_sidebar/graph-explorer')({
  component: () => (
    <div>
      <h1 className="text-3xl font-bold mb-6">Explore Graph</h1>
      <GraphExplorerContent />
    </div>
  ),
});

function GraphExplorerContent() {
  // Load persisted state from localStorage
  const loadGraphBuiltState = () => {
    try {
      const saved = localStorage.getItem('graph-explorer-built');
      return saved === 'true';
    } catch {
      return false;
    }
  };

  const loadPersistedState = () => {
    try {
      const saved = localStorage.getItem('graph-explorer-state');
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  };

  const persistedState = loadPersistedState();
  const [graphBuilt, setGraphBuilt] = useState(loadGraphBuiltState());
  const buildGraphMutation = useBuildGraph();
  const navigate = useNavigate();

  // Visualization state
  const [showStructuralEdges, setShowStructuralEdges] = useState(persistedState.showStructuralEdges ?? true);
  const [showSemanticEdges, setShowSemanticEdges] = useState(persistedState.showSemanticEdges ?? true);
  const [highlightedCommunity, setHighlightedCommunity] = useState<string | null>(persistedState.highlightedCommunity || null);
  const [expandedRoomName, setExpandedRoomName] = useState<string | null>(persistedState.expandedRoomName || null);

  // Persist state changes to localStorage
  useEffect(() => {
    saveState('graph-explorer', {
      graphBuilt: !!graphBuilt,
      showStructuralEdges,
      showSemanticEdges,
      highlightedCommunity,
      expandedRoomName,
    });
  }, [graphBuilt, showStructuralEdges, showSemanticEdges, highlightedCommunity, expandedRoomName]);

  // Load state on mount
  useEffect(() => {
    const savedState = loadState('graph-explorer');
    if (savedState) {
      setShowStructuralEdges(savedState.showStructuralEdges);
      setShowSemanticEdges(savedState.showSemanticEdges);
      setHighlightedCommunity(savedState.highlightedCommunity);
      setExpandedRoomName(savedState.expandedRoomName);
    }
  }, []);

  // Check if graph data actually exists on mount if we think it's built
  const { error: graphDataError } = useGetGraphData({
    query: {
      enabled: !!graphBuilt,
      retry: false,
    }
  });

  // If we get a 404, it means the backend was restarted and lost the in-memory graph
  if (graphDataError && (graphDataError as any).status === 404 && graphBuilt) {
    setGraphBuilt(false);
    localStorage.removeItem('graph-explorer-built');
  }

  // Poll logs while building
  const { data: logs } = useGetGraphBuildLogs({
    query: {
      refetchInterval: () => {
        return buildGraphMutation.isPending ? 1000 : false;
      },
      enabled: buildGraphMutation.isPending || !!graphBuilt
    }
  });

  const handleBuildGraph = async () => {
    await buildGraphMutation.mutateAsync();
    setGraphBuilt(true);
    localStorage.setItem('graph-explorer-built', 'true');
  };

  return (
    <div className="space-y-6">
      {!graphBuilt && (
        <Card>
          <CardHeader>
            <CardTitle>Build Table Relationship Graph</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
                <Button 
                  variant="outline" 
                  onClick={handleBuildGraph} 
                  disabled={buildGraphMutation.isPending}
                >
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
          <GraphVisualization graphBuilt={!!graphBuilt} />
        </Suspense>
      )}

      <div className="flex gap-4">
        <Button variant="outline" onClick={() => navigate({ to: '/enrichment', search: (prev: any) => prev })}>
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

// Schema color mapping
const SCHEMA_COLORS: Record<string, string> = {
  'demo_mixed': '#ef4444',
  'claims': '#10b981',
  'drug_discovery': '#f59e0b',
  'default': '#3b82f6',
};

const ROOM_COLORS = [
  '#2563eb', '#7c3aed', '#db2777', '#dc2626', '#ea580c', 
  '#d97706', '#65a30d', '#059669', '#0891b2', '#4f46e5'
];

// Custom node component
function TableNode({ data, selected }: { data: any, selected?: boolean }) {
  const schemaColor = SCHEMA_COLORS[data.schema] || SCHEMA_COLORS.default;
  const rooms = data.rooms || [];

  return (
    <div
      className={`px-4 py-3 rounded-lg border-2 shadow-lg bg-white dark:bg-slate-800 transition-all hover:shadow-xl hover:scale-105 ${data.isDimmed ? 'opacity-20' : 'opacity-100'} ${data.isHighlighted ? 'ring-4 ring-yellow-400 ring-offset-2' : ''} ${selected ? 'ring-4 ring-blue-500 ring-offset-2 animate-pulse' : ''}`}
      style={{
        borderColor: schemaColor,
        minWidth: '180px',
      }}
    >
      <Handle type="target" position={Position.Top} className="w-2 h-2 !bg-slate-400" />
      
      {/* Room Annotations */}
      <div className="absolute -top-2 -right-2 flex gap-1">
        {rooms.map((room: any, idx: number) => (
          <div 
            key={idx}
            className="w-4 h-4 rounded-full border border-white dark:border-slate-900 flex items-center justify-center text-[10px] text-white font-bold shadow-sm"
            style={{ backgroundColor: room.color }}
            title={room.name}
          >
            {room.symbol}
          </div>
        ))}
      </div>

      <div className="font-semibold text-sm mb-1 text-slate-900 dark:text-slate-100">
        {data.label}
      </div>
      <div className="flex gap-2 items-center">
        <span 
          className="text-xs px-2 py-0.5 rounded-full font-medium"
          style={{ 
            backgroundColor: schemaColor + '20',
            color: schemaColor,
          }}
        >
          {data.schema}
        </span>
        <span className="text-xs text-slate-500 dark:text-slate-400">
          {data.column_count} cols
        </span>
      </div>

      <Handle type="source" position={Position.Bottom} className="w-2 h-2 !bg-slate-400" />
    </div>
  );
}

// Custom edge component for structural edges
function StructuralEdge({ sourceX, sourceY, targetX, targetY, style, markerEnd, data }: any) {
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            fontSize: 10,
            pointerEvents: 'none',
          }}
          className="bg-white/80 dark:bg-slate-800/80 px-1 rounded text-slate-500 font-mono"
        >
          {data.weight}
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

// Custom edge component for semantic edges
function SemanticEdge({ id, sourceX, sourceY, targetX, targetY, style, markerEnd, data }: any) {
  const [edgePath, labelX, labelY] = getStraightPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
  });
  
  return (
    <>
      <path
        id={id}
        style={style}
        className="react-flow__edge-path"
        d={edgePath}
        markerEnd={markerEnd}
        strokeDasharray="5,5"
      />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            fontSize: 10,
            pointerEvents: 'all',
          }}
          className="bg-purple-100/90 dark:bg-purple-900/90 px-2 py-0.5 rounded-full text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-800 flex items-center gap-1 max-w-[150px] hover:max-w-[400px] hover:z-50 transition-all duration-200 group"
          title={data.semantic_reason}
        >
          <Info size={10} />
          <span className="truncate group-hover:whitespace-normal group-hover:overflow-visible">
            {data.weight}: {data.semantic_reason}
          </span>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

const nodeTypes: NodeTypes = {
  tableNode: TableNode,
};

const edgeTypes: EdgeTypes = {
  structural: StructuralEdge,
  semantic: SemanticEdge,
};

function GraphVisualization({ graphBuilt }: { graphBuilt: boolean }) {
  const queryClient = useQueryClient();
  const { data: graphData } = useGetGraphDataSuspense({ query: selector() } as any);
  const { data: genieRooms = [] } = useListGenieRoomsSuspense({ query: selector() } as any);
  const createRoomMutation = useCreateGenieRoom();
  const deleteRoomMutation = useDeleteGenieRoom();
  const generateFromCommunitiesMutation = useGenerateFromCommunities();
  
  // Load persisted visualization state
  const loadPersistedState = () => {
    try {
      const saved = localStorage.getItem('graph-explorer-state');
      return saved ? JSON.parse(saved) : {};
    } catch {
      return {};
    }
  };

  const persistedState = loadPersistedState();
  
  const [hoveredNode, setHoveredNode] = useState<any>(null);
  const hoverIntentRef = useRef<boolean>(false);
  
  const [selectedNodes, setSelectedNodes] = useState<Node[]>([]);
  const [expandedColumns, setExpandedColumns] = useState(false);
  const [showSelectionPanel, setShowSelectionPanel] = useState(false);
  const [newRoomName, setNewRoomName] = useState('');
  const [selectedRoomId, setSelectedRoomId] = useState<string>('');

  const [showStructuralEdges, setShowStructuralEdges] = useState(persistedState.showStructuralEdges ?? true);
  const [showSemanticEdges, setShowSemanticEdges] = useState(persistedState.showSemanticEdges ?? true);
  const [highlightedCommunity, setHighlightedCommunity] = useState<string | null>(persistedState.highlightedCommunity || null);
  const [expandedRoomName, setExpandedRoomName] = useState<string | null>(persistedState.expandedRoomName || null);
  const isLoadedRef = useRef(false);

  // Load state on mount
  useEffect(() => {
    const savedState = loadState('graph-explorer');
    if (savedState) {
      setShowStructuralEdges(savedState.showStructuralEdges);
      setShowSemanticEdges(savedState.showSemanticEdges);
      setHighlightedCommunity(savedState.highlightedCommunity);
      setExpandedRoomName(savedState.expandedRoomName);
    }
    isLoadedRef.current = true;
  }, []);

  // Persist state changes to localStorage
  useEffect(() => {
    if (!isLoadedRef.current) return;

    saveState('graph-explorer', {
      graphBuilt,
      showStructuralEdges,
      showSemanticEdges,
      highlightedCommunity,
      expandedRoomName,
    });
  }, [graphBuilt, showStructuralEdges, showSemanticEdges, highlightedCommunity, expandedRoomName]);

  // Store full room data locally since list API only returns summary
  const [fullRoomData, setFullRoomData] = useState<any>({});

  // Hover state for rooms and tables
  const [hoveredRoomId, setHoveredRoomId] = useState<string | null>(null);
  const [hoveredTableFqn, setHoveredTableFqn] = useState<string | null>(null);
  
  // Store React Flow instance
  const reactFlowInstanceRef = useRef<any>(null);

  // Populate full room data from API on mount/update
  useMemo(() => {
    const newFullRoomData: Record<string, { id: string; name: string; tables: string[] }> = {};
    genieRooms.forEach((room: any) => {
      if (room.tables && Array.isArray(room.tables)) {
        newFullRoomData[room.id] = {
          id: room.id,
          name: room.name,
          tables: room.tables
        };
      }
    });
    setFullRoomData((prev: any) => ({ ...prev, ...newFullRoomData }));
  }, [genieRooms]);

  // Room mapping with colors and symbols - Deduplicated by name (since "updates" create new rooms)
  const roomMap = useMemo(() => {
    const uniqueRooms: Record<string, any> = {};
    (genieRooms as any[]).forEach((room: any) => {
      // If we see the same name, merge the table_fqns (since "updates" create new rooms)
      if (uniqueRooms[room.name]) {
        uniqueRooms[room.name].tables = Array.from(new Set([
          ...(uniqueRooms[room.name].tables || []),
          ...(room.tables || [])
        ]));
        uniqueRooms[room.name].table_count = uniqueRooms[room.name].tables.length;
      } else {
        uniqueRooms[room.name] = { ...room };
      }
    });

    // Sort rooms by name for stable ordering
    return Object.values(uniqueRooms)
      .sort((a: any, b: any) => a.name.localeCompare(b.name))
      .map((room, idx) => ({
        ...room,
        table_fqns: room.tables || [], // Map backend 'tables' to frontend 'table_fqns'
        color: ROOM_COLORS[idx % ROOM_COLORS.length],
        symbol: String.fromCharCode(65 + (idx % 26)), // A, B, C...
      }));
  }, [genieRooms]);

  // Map table FQNs to rooms
  const tableRoomMap = useMemo(() => {
    const map: Record<string, any[]> = {};
    roomMap.forEach(room => {
      room.table_fqns?.forEach((fqn: string) => {
        if (!map[fqn]) map[fqn] = [];
        map[fqn].push({
          name: room.name,
          color: room.color,
          symbol: room.symbol
        });
      });
    });
    return map;
  }, [roomMap]);

  // Convert Cytoscape format to React Flow format with dagre layout
  const { nodes: initialNodes, edges: initialEdges } = useMemo(() => {
    const dagreGraph = new dagre.graphlib.Graph();
    dagreGraph.setDefaultEdgeLabel(() => ({}));
    dagreGraph.setGraph({ rankdir: 'TB', nodesep: 100, ranksep: 150 });

    const nodes: Node[] = [];
    const edges: Edge[] = [];

    // Separate nodes and edges from elements
    graphData.elements.forEach((elem: any) => {
      if (elem.data.source) {
        // It's an edge
        const types = elem.data.types || '';
        const isSemantic = types.includes('semantic');
        const weight = elem.data.weight || 1;
        
        edges.push({
          id: `${elem.data.source}-${elem.data.target}`,
          source: elem.data.source,
          target: elem.data.target,
          type: isSemantic ? 'semantic' : 'structural',
          animated: isSemantic,
          style: { 
            stroke: isSemantic ? '#a855f7' : '#cbd5e1',
            strokeWidth: Math.min(weight, 8),
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: isSemantic ? '#a855f7' : '#cbd5e1',
          },
          data: {
            semantic_reason: elem.data.semantic_reason,
            weight: weight,
            isSemantic: isSemantic,
          }
        });
      } else {
        // It's a node
        nodes.push({
          id: elem.data.id,
          type: 'tableNode',
          position: { x: 0, y: 0 }, // Will be set by dagre
          data: {
            ...elem.data,
            rooms: tableRoomMap[elem.data.id] || [],
          },
        });
        
        // Add to dagre graph for layout
        dagreGraph.setNode(elem.data.id, { width: 200, height: 80 });
      }
    });

    // Add edges to dagre
    edges.forEach((edge) => {
      dagreGraph.setEdge(edge.source, edge.target);
    });

    // Calculate layout
    dagre.layout(dagreGraph);

    // Apply positions from dagre
    nodes.forEach((node) => {
      const dagreNode = dagreGraph.node(node.id);
      node.position = {
        x: dagreNode.x - 100,
        y: dagreNode.y - 40,
      };
    });

    return { nodes, edges };
  }, [graphData, tableRoomMap]);

  // Apply filtering and highlighting to nodes and edges
  const { filteredNodes, filteredEdges } = useMemo(() => {
    // Get table IDs for the hovered room
    const hoveredRoomTableIds = hoveredRoomId && (fullRoomData as any)[hoveredRoomId] 
      ? new Set((fullRoomData as any)[hoveredRoomId].tables as string[]) 
      : null;

    const nodes = initialNodes.map(node => {
      const isDimmed = highlightedCommunity && node.data.schema !== highlightedCommunity;
      const isHighlighted = highlightedCommunity && node.data.schema === highlightedCommunity;
      
      // Room hover highlight
      const isInHoveredRoom = hoveredRoomTableIds && hoveredRoomTableIds.has(node.id);
      
      // Individual table hover highlight
      const isHoveredTable = hoveredTableFqn === node.id;
      
      return {
        ...node,
        data: {
          ...node.data,
          isDimmed: isDimmed || (hoveredRoomTableIds && !isInHoveredRoom) || (hoveredTableFqn && !isHoveredTable),
          isHighlighted: !!(isHighlighted || isInHoveredRoom || isHoveredTable),
        }
      };
    });

    const edges = initialEdges.filter(edge => {
      if (edge.data?.isSemantic && !showSemanticEdges) return false;
      if (!edge.data?.isSemantic && !showStructuralEdges) return false;
      return true;
    }).map(edge => {
      if (!highlightedCommunity) return edge;
      
      const sourceNode = nodes.find(n => n.id === edge.source) as any;
      const targetNode = nodes.find(n => n.id === edge.target) as any;
      const inCommunity = sourceNode?.data.schema === highlightedCommunity && 
                          targetNode?.data.schema === highlightedCommunity;
      
      return {
        ...edge,
        style: {
          ...edge.style,
          opacity: inCommunity ? 1 : 0.1,
        }
      };
    });

    return { filteredNodes: nodes, filteredEdges: edges };
  }, [initialNodes, initialEdges, showStructuralEdges, showSemanticEdges, highlightedCommunity, hoveredRoomId, hoveredTableFqn, fullRoomData]);

  const [nodes, setNodes, onNodesChange] = useNodesState(filteredNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(filteredEdges);

  // Sync state when filtered results change
  useMemo(() => {
    setNodes(filteredNodes);
    setEdges(filteredEdges);
  }, [filteredNodes, filteredEdges, setNodes, setEdges]);

  const enterTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const onNodeMouseEnter = useCallback((_: any, node: Node) => {
    hoverIntentRef.current = true;
    
    // Clear any existing enter timeout
    if (enterTimeoutRef.current) clearTimeout(enterTimeoutRef.current);
    
    // Add a minor delay before showing the node details
    enterTimeoutRef.current = setTimeout(() => {
      if (hoverIntentRef.current) {
        setHoveredNode(node);
      }
    }, 1000); // 1000ms delay to prevent flickering while moving across nodes
  }, []);

  const onNodeMouseLeave = useCallback(() => {
    hoverIntentRef.current = false;
    
    // Clear enter timeout if we leave before it fires
    if (enterTimeoutRef.current) clearTimeout(enterTimeoutRef.current);

    setTimeout(() => {
      if (!hoverIntentRef.current) {
        setHoveredNode(null);
      }
    }, 800);
  }, []);

  const onSelectionChange = useCallback(({ nodes }: { nodes: Node[] }) => {
    setSelectedNodes(nodes);
    setShowSelectionPanel(nodes.length > 0);
  }, []);

  const handleRemoveFromSelection = useCallback((nodeId: string) => {
    const updatedNodes = selectedNodes.filter(n => n.id !== nodeId);
    setSelectedNodes(updatedNodes);
    setShowSelectionPanel(updatedNodes.length > 0);
  }, [selectedNodes]);

  const handleAddToRoom = useCallback(async () => {
    const tableFqns = selectedNodes.map(n => n.id);

    if (selectedRoomId === 'new' && newRoomName) {
      // Create new room
      await createRoomMutation.mutateAsync({
        data: {
          name: newRoomName,
          table_fqns: tableFqns,
        }
      }, {
        onSuccess: (newRoom) => {
          // Store full room data locally
          setFullRoomData((prev: any) => {
            const newData = { ...prev };
            newData[newRoom.id] = { id: newRoom.id, name: newRoom.name, tables: newRoom.tables };
            return newData;
          });
          queryClient.invalidateQueries({ queryKey: [`/api/genie/rooms`] });
          // Force immediate refetch
          queryClient.refetchQueries({ queryKey: [`/api/genie/rooms`] });
          setNewRoomName('');
          setSelectedRoomId('');
          setShowSelectionPanel(false);
          setSelectedNodes([]);
          // Keep expansion state for better UX
        }
      });
    } else if (selectedRoomId && selectedRoomId !== 'new') {
      // Add to existing room - delete old room and create new one with combined tables
      const existingRoom = genieRooms.find(r => r.id === selectedRoomId) as any;

      if (existingRoom) {
        // Get existing tables from the room (now that API returns full data)
        const existingTableFqns = existingRoom.tables || [];
        const combinedTableFqns = Array.from(new Set([
          ...existingTableFqns,
          ...tableFqns
        ]));

        // Delete the old room first
        await deleteRoomMutation.mutateAsync({
          roomId: selectedRoomId
        });

        // Create new room with combined tables
        await createRoomMutation.mutateAsync({
          data: {
            name: existingRoom.name,
            table_fqns: combinedTableFqns,
          }
        }, {
          onSuccess: (updatedRoom) => {
            // Store full room data locally
            setFullRoomData((prev: any) => {
              const newData = { ...prev };
              newData[updatedRoom.id] = { id: updatedRoom.id, name: updatedRoom.name, tables: updatedRoom.tables };
              delete newData[selectedRoomId];
              return newData;
            });
            queryClient.invalidateQueries({ queryKey: [`/api/genie/rooms`] });
            // Force immediate refetch
            queryClient.refetchQueries({ queryKey: [`/api/genie/rooms`] });
            setSelectedRoomId('');
            setShowSelectionPanel(false);
            setSelectedNodes([]);
            // Keep expansion state for better UX
          }
        });
      }
    }
  }, [selectedNodes, selectedRoomId, newRoomName, createRoomMutation, deleteRoomMutation, genieRooms, queryClient]);

  const handleRemoveTableFromRoom = useCallback(async (roomId: string, tableFqn: string) => {
    const room = (fullRoomData as any)[roomId];
    if (!room) return;

    // Remove the specific table from the room's table list
    const updatedTableFqns = room.tables.filter((fqn: string) => fqn !== tableFqn);

    // If no tables left, delete the room entirely
    if (updatedTableFqns.length === 0) {
      await deleteRoomMutation.mutateAsync({
        roomId: roomId
      });
      // Remove from local data
      setFullRoomData((prev: any) => {
        const newData = { ...prev };
        delete newData[roomId];
        return newData;
      });
    } else {
      // Delete old room and create new one with remaining tables
      await deleteRoomMutation.mutateAsync({
        roomId: roomId
      });

      const result = await createRoomMutation.mutateAsync({
        data: {
          name: room.name,
          table_fqns: updatedTableFqns,
        }
      });

      // Clear hover states to prevent "sticky" highlights after room ID changes
      setHoveredRoomId(null);
      setHoveredTableFqn(null);

      // Store the updated room data locally
      setFullRoomData((prev: any) => ({
        ...prev,
        [result.id]: { id: result.id, name: result.name, tables: result.tables },
        // Remove the old room data
        [roomId]: undefined
      }));
    }

    // Refresh the room list
    queryClient.invalidateQueries({ queryKey: [`/api/genie/rooms`] });
    queryClient.refetchQueries({ queryKey: [`/api/genie/rooms`] });
  }, [fullRoomData, deleteRoomMutation, createRoomMutation, queryClient]);

  const handleGenerateFromCommunities = useCallback(async () => {
    await generateFromCommunitiesMutation.mutateAsync(undefined, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: [`/api/genie/rooms`] });
        queryClient.refetchQueries({ queryKey: [`/api/genie/rooms`] });
      }
    });
  }, [generateFromCommunitiesMutation, queryClient]);

  const handleDeleteRoom = useCallback(async (roomId: string, roomName: string) => {
    await deleteRoomMutation.mutateAsync({ roomId }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: [`/api/genie/rooms`] });
        queryClient.refetchQueries({ queryKey: [`/api/genie/rooms`] });
        if (expandedRoomName === roomName) setExpandedRoomName(null);
      }
    });
  }, [deleteRoomMutation, queryClient, expandedRoomName]);

  const onPaneClick = useCallback(() => {
    setSelectedNodes([]);
    setShowSelectionPanel(false);
    setHighlightedCommunity(null);
    setHoveredNode(null);
    setHoveredTableFqn(null);
    setHoveredRoomId(null);
  }, []);

  const semanticEdgeCount = initialEdges.filter(e => e.data?.isSemantic).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          Table Relationship Graph ({graphData.node_count} tables, {graphData.edge_count} relationships)
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div style={{ height: '700px' }} className="relative">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeMouseEnter={onNodeMouseEnter}
            onNodeMouseLeave={onNodeMouseLeave}
            onSelectionChange={onSelectionChange}
            onPaneClick={onPaneClick}
            onInit={(instance) => { reactFlowInstanceRef.current = instance; }}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            fitView
            minZoom={0.1}
            maxZoom={2}
            selectNodesOnDrag={false}
            selectionMode={SelectionMode.Partial}
            panOnScroll
            selectionOnDrag
            panOnDrag={[1, 2]}
          >
            <Background variant={BackgroundVariant.Dots} gap={12} size={1} />
            <Controls />
            <MiniMap 
              nodeColor={(node) => {
                const schema = node.data?.schema as string;
                return SCHEMA_COLORS[schema] || SCHEMA_COLORS.default;
              }}
              maskColor="rgba(0, 0, 0, 0.1)"
              pannable
              zoomable
            />
            
            {/* Legend Panel */}
            <Panel position="top-left" className="bg-white dark:bg-slate-800 rounded-lg shadow-lg p-3 text-sm max-w-[200px]">
              <div className="font-semibold mb-2 flex items-center justify-between">
                <span>Communities</span>
                {highlightedCommunity && (
                  <button 
                    onClick={() => setHighlightedCommunity(null)}
                    className="text-[10px] text-blue-500 hover:underline"
                  >
                    Clear
                  </button>
                )}
              </div>
              <div className="space-y-1">
                {Object.entries(SCHEMA_COLORS).map(([schema, color]) => (
                  schema !== 'default' && (
                    <div 
                      key={schema} 
                      className={`flex items-center gap-2 cursor-pointer p-1 rounded transition-colors ${highlightedCommunity === schema ? 'bg-blue-50 dark:bg-blue-900/30 ring-1 ring-blue-200' : 'hover:bg-slate-100 dark:hover:bg-slate-700'}`}
                      onClick={() => setHighlightedCommunity(schema === highlightedCommunity ? null : schema)}
                    >
                      <div className="w-3 h-3 rounded shrink-0" style={{ backgroundColor: color as string }}></div>
                      <span className="text-xs truncate">{schema}</span>
                    </div>
                  )
                ))}
              </div>
            </Panel>

            {/* Edge Toggles Panel */}
            <Panel position="top-right" className="bg-white dark:bg-slate-800 rounded-lg shadow-lg p-3 text-sm mr-2 mt-2">
              <div className="font-semibold mb-2 flex items-center gap-2">
                <Layers size={14} />
                <span>Edge Visibility</span>
              </div>
              <div className="space-y-2">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input 
                    type="checkbox" 
                    checked={showStructuralEdges} 
                    onChange={(e) => setShowStructuralEdges(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <span className="text-xs">Structural Edges</span>
                  <div className="w-4 h-0.5 bg-slate-400 ml-auto"></div>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input 
                    type="checkbox" 
                    checked={showSemanticEdges} 
                    onChange={(e) => setShowSemanticEdges(e.target.checked)}
                    className="rounded border-slate-300"
                  />
                  <span className="text-xs">Semantic Edges ({semanticEdgeCount})</span>
                  <div className="w-4 h-0.5 bg-purple-500 border-t border-dashed ml-auto"></div>
                </label>
              </div>
            </Panel>

            {/* Room Panel */}
            <Panel position="top-right" className="bg-white dark:bg-slate-800 rounded-lg shadow-lg p-3 text-sm mr-2 mt-28 max-w-[250px]">
              <div className="font-semibold mb-2 flex items-center justify-between gap-2">
                <span>Genie Rooms</span>
                <Button 
                  variant="ghost" 
                  size="xs"
                  className="h-7 w-7 p-0 text-purple-600 hover:text-purple-700 hover:bg-purple-50 dark:text-purple-400 dark:hover:text-purple-300 dark:hover:bg-purple-900/20"
                  onClick={handleGenerateFromCommunities}
                  disabled={generateFromCommunitiesMutation.isPending}
                  title="Generate rooms from communities (AI)"
                >
                  <Sparkles size={16} className={generateFromCommunitiesMutation.isPending ? 'animate-pulse' : ''} />
                </Button>
              </div>
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {roomMap.map((room) => (
                  <div key={room.id} className="border rounded-md overflow-hidden">
                    <div className="w-full flex items-center gap-2 p-2 hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors group/room">
                      <button 
                        className="flex-1 flex items-center gap-2 text-left overflow-hidden"
                        onClick={() => setExpandedRoomName(expandedRoomName === room.name ? null : room.name)}
                        onMouseEnter={() => setHoveredRoomId(room.id)}
                        onMouseLeave={() => setHoveredRoomId(null)}
                      >
                        <div 
                          className="w-5 h-5 rounded-full flex items-center justify-center text-[10px] text-white font-bold shrink-0"
                          style={{ backgroundColor: room.color }}
                        >
                          {room.symbol}
                        </div>
                        <span className="text-xs font-medium truncate flex-1">{room.name}</span>
                        <span className="text-[10px] text-slate-500 shrink-0">{(fullRoomData as any)[room.id]?.tables?.length || room.table_count || 0}</span>
                        {expandedRoomName === room.name ? <ChevronDown size={14} className="shrink-0" /> : <ChevronRight size={14} className="shrink-0" />}
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteRoom(room.id, room.name);
                        }}
                        className="opacity-0 group-hover/room:opacity-100 text-slate-400 hover:text-red-500 transition-all p-1 rounded hover:bg-red-50 dark:hover:bg-red-900/20"
                        title="Delete room"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>
                    {expandedRoomName === room.name && (
                      <div className="bg-slate-50 dark:bg-slate-900/50 p-2 border-t text-[10px] space-y-1">
                        {(fullRoomData as any)[room.id] ? (
                          (fullRoomData as any)[room.id].tables.map((fqn: string) => (
                            <div 
                              key={fqn} 
                              className="flex items-center justify-between gap-1 hover:bg-slate-100 dark:hover:bg-slate-800/50 rounded px-1 py-0.5 transition-colors"
                              onMouseEnter={() => setHoveredTableFqn(fqn)}
                              onMouseLeave={() => setHoveredTableFqn(null)}
                            >
                              <span 
                                className="truncate text-slate-600 dark:text-slate-400 flex-1 cursor-pointer"
                                onClick={() => {
                                  // Find the node and center on it
                                  const node = nodes.find(n => n.id === fqn);
                                  if (node && reactFlowInstanceRef.current) {
                                    reactFlowInstanceRef.current.setCenter(node.position.x + 100, node.position.y + 40, { zoom: 1.5, duration: 800 });
                                  }
                                }}
                                title={`Click to center on ${fqn.split('.').pop()}`}
                              >
                                • {fqn.split('.').pop()}
                              </span>
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleRemoveTableFromRoom(room.id, fqn);
                                }}
                                className="text-red-500 hover:text-red-700 dark:text-red-400 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 rounded p-0.5 transition-colors"
                                title={`Remove ${fqn.split('.').pop()} from room`}
                              >
                                <Minus size={10} />
                              </button>
                            </div>
                          ))
                        ) : (
                          <div className="text-slate-500 italic text-center py-2">
                            Room details not available for editing
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {roomMap.length === 0 && (
                  <div className="text-xs text-slate-500 italic p-2 text-center">No rooms created yet</div>
                )}
              </div>
            </Panel>
          </ReactFlow>

          {/* Hover Detail Panel */}
          {hoveredNode && (
            <div 
              className="absolute bottom-0 left-0 right-0 bg-white dark:bg-slate-800 border-t-2 border-slate-200 dark:border-slate-700 shadow-2xl transition-all duration-300 ease-out z-[50] pointer-events-auto"
              style={{ 
                transform: hoveredNode ? 'translateY(0)' : 'translateY(100%)',
                maxHeight: '300px',
                overflowY: 'auto',
              }}
              onMouseEnter={() => { hoverIntentRef.current = true; }}
              onMouseLeave={onNodeMouseLeave}
            >
              <div className="p-4 nopan">
                <div className="flex justify-between items-start mb-3">
                  <div>
                    <h3 className="font-bold text-lg text-slate-900 dark:text-slate-100">
                      {hoveredNode.data.label as React.ReactNode}
                    </h3>
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                      {hoveredNode.id}
                    </p>
                  </div>
                  <Button 
                    variant="ghost" 
                    size="sm"
                    onClick={() => setHoveredNode(null)}
                  >
                    <X size={16} />
                  </Button>
                </div>
                
                {hoveredNode.data.table_description && (
                  <div className="mb-3">
                    <p className="text-sm text-slate-700 dark:text-slate-300">
                      {hoveredNode.data.table_description}
                    </p>
                  </div>
                )}

                {hoveredNode.data.columns && hoveredNode.data.columns.length > 0 && (
                  <div>
                    <button
                      onClick={() => setExpandedColumns(!expandedColumns)}
                      className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-300 hover:text-slate-900 dark:hover:text-slate-100 mb-2"
                    >
                      {expandedColumns ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                      Columns ({hoveredNode.data.columns.length})
                    </button>
                    
                    {expandedColumns && (
                      <div className="space-y-2 ml-6 max-h-40 overflow-y-auto">
                        {hoveredNode.data.columns.map((col: any, idx: number) => (
                          <div key={idx} className="text-sm border-l-2 border-slate-300 dark:border-slate-600 pl-3">
                            <div className="font-mono font-medium text-slate-900 dark:text-slate-100">
                              {col.name} 
                              <span className="text-slate-500 dark:text-slate-400 ml-2 font-normal">
                                {col.type}
                              </span>
                            </div>
                            {col.comment && (
                              <div className="text-xs text-slate-600 dark:text-slate-400 mt-1">
                                {col.comment}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Selection Action Panel */}
          {showSelectionPanel && (
            <div className="absolute top-4 right-4 bg-white dark:bg-slate-800 rounded-lg shadow-2xl border-2 border-slate-200 dark:border-slate-700 p-4 w-80 z-[40]">
              <div className="flex justify-between items-start mb-3">
                <h3 className="font-bold text-slate-900 dark:text-slate-100">
                  Selected Tables ({selectedNodes.length})
                </h3>
                <Button 
                  variant="ghost" 
                  size="sm"
                  onClick={() => {
                    setShowSelectionPanel(false);
                    setSelectedNodes([]);
                  }}
                >
                  <X size={16} />
                </Button>
              </div>

              <div className="space-y-2 mb-4 max-h-40 overflow-y-auto">
                {selectedNodes.map((node) => (
                  <div 
                    key={node.id}
                    className="flex items-center justify-between text-sm bg-slate-50 dark:bg-slate-700 rounded px-3 py-2"
                  >
                    <span className="text-slate-900 dark:text-slate-100 truncate">
                      {node.data.label as React.ReactNode}
                    </span>
                    <button
                      onClick={() => handleRemoveFromSelection(node.id)}
                      className="text-slate-500 hover:text-red-500"
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>

              <div className="space-y-3">
                <div>
                  <label className="text-xs font-medium text-slate-700 dark:text-slate-300 mb-1 block">
                    Add to Genie Room
                  </label>
                  <select
                    value={selectedRoomId}
                    onChange={(e) => setSelectedRoomId(e.target.value)}
                    className="w-full px-3 py-2 text-sm border rounded-md bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100"
                  >
                    <option value="">Select room...</option>
                    <option value="new">Create New Room</option>
                    {roomMap.map((room) => (
                      <option key={room.id} value={room.id}>
                        {room.name}
                      </option>
                    ))}
                  </select>
                </div>

                {selectedRoomId === 'new' && (
                  <input
                    type="text"
                    value={newRoomName}
                    onChange={(e) => setNewRoomName(e.target.value)}
                    placeholder="New room name..."
                    className="w-full px-3 py-2 text-sm border rounded-md bg-white dark:bg-slate-700 text-slate-900 dark:text-slate-100"
                  />
                )}

                <Button
                  onClick={handleAddToRoom}
                  disabled={!selectedRoomId || (selectedRoomId === 'new' && !newRoomName) || createRoomMutation.isPending}
                  className="w-full"
                  size="sm"
                >
                  <Plus size={16} className="mr-2" />
                  {createRoomMutation.isPending ? 'Adding...' : 'Add to Room'}
                </Button>
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
