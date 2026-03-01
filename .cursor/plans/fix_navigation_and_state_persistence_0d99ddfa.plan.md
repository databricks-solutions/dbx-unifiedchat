---
name: Fix Navigation and State Persistence
overview: Fix the sidebar navigation order (currently 3,1,2,4,5 → should be 1,2,3,4,5) and implement comprehensive state persistence using localStorage and URL params so users can refresh pages and navigate back while maintaining their progress.
todos:
  - id: fix-nav-order
    content: Reorder navItems array in route.tsx to correct sequence (1,2,3,4,5)
    status: completed
  - id: create-state-util
    content: Create workflow-state.ts utility with save/load/clear functions
    status: completed
  - id: persist-catalog-browser
    content: Add state persistence to catalog-browser.tsx for selections and UI state
    status: completed
  - id: persist-enrichment
    content: Add state persistence to enrichment.tsx with jobId in URL params
    status: completed
  - id: persist-graph-explorer
    content: Enable commented state persistence in graph-explorer.tsx
    status: completed
  - id: persist-genie-builder
    content: Add state persistence to genie-builder.tsx for form inputs
    status: completed
  - id: persist-genie-create
    content: Add state persistence to genie-create.tsx for creation progress
    status: completed
  - id: add-nav-guards
    content: Add navigation warnings for unsaved changes
    status: completed
  - id: add-reset-button
    content: Add 'Reset Workflow' button to clear all saved state
    status: completed
  - id: test-flow
    content: Test complete workflow with refreshes and back navigation
    status: completed
isProject: false
---

# Fix Navigation Order and Implement State Persistence

## Problem Analysis

The current sidebar navigation order is incorrect:

```10:14:tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/route.tsx
const navItems = [
  { to: '/graph-explorer', label: '3. Explore Graph', icon: <Share2 size={16} /> },
  { to: '/catalog-browser', label: '1. Browse Catalogs', icon: <Database size={16} /> },
  { to: '/enrichment', label: '2. Enrich Tables', icon: <Sparkles size={16} /> },
  { to: '/genie-builder', label: '4. Build Rooms', icon: <Boxes size={16} /> },
  { to: '/genie-create', label: '5. Create Rooms', icon: <Rocket size={16} /> },
];
```

Additionally, most pages lose their state on refresh or when navigating back.

## Implementation Strategy

### 1. Fix Sidebar Navigation Order

**File:** `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/route.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/route.tsx)`

- Reorder `navItems` array to follow correct sequence: 1→2→3→4→5
- Update the array to place catalog-browser first, then enrichment, graph-explorer, genie-builder, genie-create

### 2. Create Centralized State Management Utility

**New file:** `tables_to_genies_apx/src/tables_genies/ui/lib/workflow-state.ts`

Create a state management utility that:

- Defines TypeScript interfaces for each page's state
- Provides `saveState(pageKey, data)` and `loadState(pageKey)` functions
- Uses localStorage with versioned keys to prevent stale data issues
- Includes state validation and migration helpers

**State Keys:**

- `workflow.catalog-browser`: Selected catalogs, schemas, tables, UI expansion states
- `workflow.enrichment`: Job ID, job URL, metadata/chunks table names, write mode
- `workflow.graph-explorer`: Graph built status, visualization filters, room expansions
- `workflow.genie-builder`: Form inputs (room name, selected tables)
- `workflow.genie-create`: Creation status, progress tracking

### 3. Page-by-Page State Persistence

#### A. Browse Catalogs Page

**File:** `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/catalog-browser.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/catalog-browser.tsx)`

Current issues:

- `selectedTables` state resets on refresh (line 156)
- `selectedCatalog` and `selectedSchema` UI state lost (lines 154-155)
- `allCatalogTables` cache lost (line 158)

Implementation:

- On component mount, restore state from localStorage
- Save state to localStorage on every change (debounced for performance)
- Persist: `selectedTables` (Set → Array), `selectedCatalog`, `selectedSchema`, `allCatalogTables` (Map → Object)
- Add a "Continue from where you left off" banner if state is detected on mount

#### B. Enrich Tables Page

**File:** `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/enrichment.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/enrichment.tsx)`

Current issues:

- `jobId` and `jobUrl` reset on refresh (lines 26-27)
- Form inputs (`metadataTable`, `chunksTable`, `writeMode`) reset to defaults (lines 28-30)

Implementation:

- Save job details to localStorage immediately after job starts (line 43-45)
- Restore job state on mount if available
- Use URL search params for `jobId` to enable deep linking: `/enrichment?jobId=123`
- Continue polling job status if job is still running on page load
- Persist form input values for user convenience

#### C. Explore Graph Page

**File:** `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/graph-explorer.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/graph-explorer.tsx)`

Current state:

- Already has `graphBuilt` persistence (lines 45-55) ✅
- Has partial state persistence for filters (lines 306-315)

Improvements needed:

- Currently loads state but doesn't save changes (see commented code lines 342-376)
- Persist `showStructuralEdges`, `showSemanticEdges`, `highlightedCommunity`, `expandedRoomName`
- Auto-save whenever these values change
- Keep expanded room states across refreshes

#### D. Build Rooms Page

**File:** `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/genie-builder.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/genie-builder.tsx)`

Current issues:

- `selectedTableFqns` and `roomName` reset on refresh (lines 40-41)
- User loses their work-in-progress room configuration

Implementation:

- Persist form state (roomName, selectedTableFqns) on every change
- Restore on mount with a "Resume editing" indicator
- Clear state after successful room creation
- Show warning if navigating away with unsaved changes

#### E. Create Rooms Page

**File:** `[tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/genie-create.tsx](tables_to_genies_apx/src/tables_genies/ui/routes/_sidebar/genie-create.tsx)`

Current issues:

- `creationStarted` flag resets on refresh (line 23)
- Users can't see creation progress if they refresh

Implementation:

- Save creation status to localStorage when creation starts
- Poll backend API on mount to check if creation is in progress
- Resume showing progress if creation is still running
- Clear state once all rooms are successfully created

### 4. Navigation Guards and User Experience

Add navigation confirmation dialogs:

- Warn users if navigating away from pages with unsaved state
- Use `useBeforeUnload` hook for browser refresh/close warnings
- Add "Clear all saved data" button in sidebar footer for testing/debugging

### 5. State Cleanup and Reset

Create a global "Reset Workflow" button that:

- Clears all localStorage keys with `workflow.*` prefix
- Resets backend state (optional - delete selection/rooms via API)
- Navigates user back to step 1 (Browse Catalogs)
- Shows confirmation modal before clearing

## Technical Details

**localStorage Schema:**

```typescript
{
  "workflow.version": "1.0",
  "workflow.catalog-browser": {
    "selectedTables": ["catalog.schema.table1", "catalog.schema.table2"],
    "selectedCatalog": "my_catalog",
    "selectedSchema": "my_schema",
    "allCatalogTables": { "catalog.schema": [...] },
    "timestamp": 1709500000000
  },
  "workflow.enrichment": {
    "jobId": 12345,
    "jobUrl": "https://...",
    "metadataTable": "...",
    "chunksTable": "...",
    "writeMode": "overwrite",
    "timestamp": 1709500100000
  }
}
```

**URL Param Usage:**

- `/enrichment?jobId=123` - Direct link to enrichment job
- `/graph-explorer?community=demo_mixed` - Deep link to community view
- Use TanStack Router's search params API for type-safe param handling

## Testing Checklist

1. ✅ Sidebar shows correct order: 1 → 2 → 3 → 4 → 5
2. ✅ Refresh on any page restores state
3. ✅ Navigate forward and back preserves selections
4. ✅ Multiple browser tabs maintain independent state
5. ✅ State expires after 24 hours (prevents stale data)
6. ✅ Clear workflow button removes all saved state
7. ✅ URL with jobId param loads enrichment progress
8. ✅ Warning shows when navigating away with unsaved changes

