---
name: Graph Explorer Enhancements
overview: "Enhance the graph-explorer.tsx React Flow visualization with 4 features: edge visibility with toggle switches, hover panel fix, room collapsible panel with node annotations, and interactive community legend."
todos:
  - id: edge-toggle
    content: Add structural/semantic edge toggle switches with strength-based styling and labels in graph-explorer.tsx
    status: completed
  - id: hover-fix
    content: Fix hover panel disappearing by implementing ref-based hover intent pattern and z-index/nopan fixes
    status: completed
  - id: room-panel
    content: Add collapsible room panel (top-right) with colored symbols, and annotate TableNode with room dots
    status: completed
  - id: community-legend
    content: "Make community legend interactive: click to highlight/dim members on the graph"
    status: completed
isProject: false
---

# Graph Explorer Enhancements

All changes are in the single file: `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/graph-explorer.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/graph-explorer.tsx)`

The backend already provides edge data with `types` (e.g. `"same_schema,semantic"`, `"column_overlap_2"`, `"semantic"`) and `weight` fields via the `GraphRAGTableGraphBuilder.to_cytoscape_format()` method. The frontend already converts these to React Flow edges (lines 206-227) but they may not render visibly. The data flow is: backend Cytoscape elements -> `useMemo` conversion -> `useEdgesState`.

---

## 1. Edge Visibility with Strength and Toggle Switches

**Problem**: Edges exist in the data but may not render visually (e.g. if all nodes are in disconnected components, or edges have no visible styling). User wants explicit strength overlays and toggle controls.

**Changes**:

- Add two boolean state variables: `showStructuralEdges` and `showSemanticEdges` (both default `true`)
- In the `useMemo` block (lines 197-260), classify each edge as structural (types contain `same_schema`, `column_overlap`, `fk_hint`, `same_catalog`) or semantic (types contain `semantic`)
- Apply distinct visual styling:
  - **Structural**: solid gray line, `strokeWidth` proportional to `weight` (e.g. `Math.min(weight, 8)`), label showing weight
  - **Semantic**: dashed purple line, `strokeWidth` proportional to `weight`, animated, with label showing the `semantic_reason`
- Add a toggle panel (React Flow `Panel` at `position="top-right"`) with two `<label><input type="checkbox" />` switches:
  - "Structural Edges" toggle
  - "Semantic Edges" toggle
- Filter edges passed to `<ReactFlow edges={...}>` based on toggle state. Use a `useMemo` that depends on `[initialEdges, showStructuralEdges, showSemanticEdges]` to compute `filteredEdges`
- Enhance the `SemanticEdge` custom edge component (lines 159-174) to render the dashed line with a label showing strength/reason
- Add a new `StructuralEdge` custom edge component with solid styling and weight label
- Update `edgeTypes` to include both `structural` and `semantic`

---

## 2. Fix Hover Detail Panel Disappearing

**Problem**: The hover panel (lines 375-446) disappears when the user moves their mouse to interact with it, because `onNodeMouseLeave` fires and the 200ms `setTimeout` is too short. Additionally, the React Flow pane's drag handler interferes.

**Changes**:

- Replace the simple `setTimeout` approach with a ref-based "intent" pattern:
  - Add `const hoverIntentRef = useRef<boolean>(false)`
  - On `onNodeMouseEnter`: set `hoveredNode`, set `hoverIntentRef.current = true`
  - On `onNodeMouseLeave`: set `hoverIntentRef.current = false`, then `setTimeout(() => { if (!hoverIntentRef.current) setHoveredNode(null) }, 300)`
  - On the hover panel's `onMouseEnter`: set `hoverIntentRef.current = true`
  - On the hover panel's `onMouseLeave`: set `hoverIntentRef.current = false`, then `setTimeout(() => { if (!hoverIntentRef.current) setHoveredNode(null) }, 300)`
- Add `pointer-events: auto` and `z-index: 50` to the hover panel `<div>` to ensure it's above the React Flow pane
- Add `className="nopan"` or `style={{ pointerEvents: 'all' }}` to prevent React Flow from capturing drag events on the panel

---

## 3. Room Collapsible Panel + Node Room Annotations

**Problem**: After adding nodes to a room, there's no visual indication on the graph. User wants:

- A collapsible panel in the top-right corner showing rooms with colored symbols
- Each node annotated with room symbols in its top-right corner
- Nodes can belong to multiple rooms

**Changes**:

### 3a. Room Color/Symbol System

- Define a `ROOM_COLORS` array of distinct colors (e.g. 8-10 colors)
- Define room symbols (small colored circles or Unicode markers like `\u25CF`) indexed by room creation order
- Create a `useMemo` that maps `genieRooms` to `{ roomId, name, color, symbol, tables }` objects

### 3b. Collapsible Room Panel

- Add a new component `RoomPanel` rendered inside the ReactFlow container
- Position: `absolute top-4 right-4` (move existing Selection Panel to adjust if both visible)
- Collapsed state: shows a small pill per room with `[symbol] [name]` and member count
- Expanded state (on click): shows list of member nodes with their table names
- Use `useState<string | null>(expandedRoomId)` to track which room is expanded

### 3c. Node Room Annotations

- Modify the `TableNode` component (lines 126-156) to accept room assignments via `data.rooms` (an array of `{ color, symbol }` objects)
- Render small colored dots/circles in the top-right corner of each node for each room it belongs to
- Compute `nodeRoomMap: Record<string, Array<{color, symbol}>>` in a `useMemo` that scans `genieRooms` and maps table FQNs to room symbols
- Inject room data into each node's `data` field before passing to React Flow

---

## 4. Interactive Community Legend

**Problem**: The community legend panel (lines 358-371) is static. Clicking a community should highlight its members.

**Changes**:

- Add state: `const [highlightedCommunity, setHighlightedCommunity] = useState<string | null>(null)`
- Make each legend entry clickable with `onClick={() => setHighlightedCommunity(schema === highlightedCommunity ? null : schema)}`
- Add hover/active styling (`cursor-pointer`, highlight ring on active)
- When a community is highlighted:
  - Dim all nodes NOT in that community by adding an `opacity: 0.2` style to their wrapper
  - Dim all edges NOT connecting two nodes in that community
  - Add a glow/ring to nodes that ARE in the community
- Implement by modifying the `nodes` array in a `useMemo` that adds a `style` property based on `highlightedCommunity`, and filtering/dimming edges similarly
- Show a "Clear highlight" button or allow clicking the same community again to deselect

