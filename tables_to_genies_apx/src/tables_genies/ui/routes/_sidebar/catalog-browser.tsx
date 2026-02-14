import { createFileRoute, useNavigate } from '@tanstack/react-router';
import { Suspense, useState } from 'react';
import { useListCatalogsSuspense, useListSchemasSuspense, useListTablesSuspense, useSaveSelection } from '@/lib/api';
import { selector } from '@/lib/selector';
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { ChevronRight, ChevronDown } from 'lucide-react';

export const Route = createFileRoute('/_sidebar/catalog-browser')({
  component: () => (
    <div>
      <h1 className="text-3xl font-bold mb-6">Browse Catalogs</h1>
      <Suspense fallback={<CatalogsSkeleton />}>
        <CatalogsView />
      </Suspense>
    </div>
  ),
});

function CatalogsView() {
  const { data: catalogs } = useListCatalogsSuspense(selector());
  const [selectedTables, setSelectedTables] = useState<string[]>([]);
  const [expandedCatalogs, setExpandedCatalogs] = useState<Set<string>>(new Set());
  const [expandedSchemas, setExpandedSchemas] = useState<Set<string>>(new Set());
  const saveSelectionMutation = useSaveSelection();
  const navigate = useNavigate();

  const toggleCatalog = (catalog: string) => {
    setExpandedCatalogs(prev => {
      const newSet = new Set(prev);
      if (newSet.has(catalog)) {
        newSet.delete(catalog);
      } else {
        newSet.add(catalog);
      }
      return newSet;
    });
  };

  const toggleSchema = (key: string) => {
    setExpandedSchemas(prev => {
      const newSet = new Set(prev);
      if (newSet.has(key)) {
        newSet.delete(key);
      } else {
        newSet.add(key);
      }
      return newSet;
    });
  };

  const toggleTable = (fqn: string) => {
    setSelectedTables(prev =>
      prev.includes(fqn) ? prev.filter(t => t !== fqn) : [...prev, fqn]
    );
  };

  const handleNext = async () => {
    await saveSelectionMutation.mutateAsync({ table_fqns: selectedTables });
    navigate({ to: '/enrichment' });
  };

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Unity Catalog Tables</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {catalogs.map((catalog) => (
              <div key={catalog.name} className="border rounded-lg">
                <button
                  onClick={() => toggleCatalog(catalog.name)}
                  className="w-full flex items-center gap-2 p-3 hover:bg-accent transition-colors"
                >
                  {expandedCatalogs.has(catalog.name) ? (
                    <ChevronDown size={16} />
                  ) : (
                    <ChevronRight size={16} />
                  )}
                  <span className="font-medium">🗄️ {catalog.name}</span>
                </button>

                {expandedCatalogs.has(catalog.name) && (
                  <div className="pl-6 pb-2">
                    <Suspense fallback={<Skeleton className="h-20 w-full" />}>
                      <SchemasView
                        catalogName={catalog.name}
                        expandedSchemas={expandedSchemas}
                        selectedTables={selectedTables}
                        onToggleSchema={toggleSchema}
                        onToggleTable={toggleTable}
                      />
                    </Suspense>
                  </div>
                )}
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Selected {selectedTables.length} tables
            </p>
            <Button onClick={handleNext} disabled={selectedTables.length === 0}>
              Next: Enrich Tables →
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function SchemasView({
  catalogName,
  expandedSchemas,
  selectedTables,
  onToggleSchema,
  onToggleTable,
}: {
  catalogName: string;
  expandedSchemas: Set<string>;
  selectedTables: string[];
  onToggleSchema: (key: string) => void;
  onToggleTable: (fqn: string) => void;
}) {
  const { data: schemas } = useListSchemasSuspense(catalogName, selector());

  return (
    <div className="space-y-1">
      {schemas.map((schema) => {
        const schemaKey = `${catalogName}.${schema.name}`;
        return (
          <div key={schema.name} className="border rounded-md">
            <button
              onClick={() => onToggleSchema(schemaKey)}
              className="w-full flex items-center gap-2 p-2 hover:bg-accent transition-colors"
            >
              {expandedSchemas.has(schemaKey) ? (
                <ChevronDown size={14} />
              ) : (
                <ChevronRight size={14} />
              )}
              <span className="text-sm">📁 {schema.name}</span>
            </button>

            {expandedSchemas.has(schemaKey) && (
              <div className="pl-6 pb-1">
                <Suspense fallback={<Skeleton className="h-16 w-full" />}>
                  <TablesView
                    catalogName={catalogName}
                    schemaName={schema.name}
                    selectedTables={selectedTables}
                    onToggleTable={onToggleTable}
                  />
                </Suspense>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function TablesView({
  catalogName,
  schemaName,
  selectedTables,
  onToggleTable,
}: {
  catalogName: string;
  schemaName: string;
  selectedTables: string[];
  onToggleTable: (fqn: string) => void;
}) {
  const { data: tables } = useListTablesSuspense(catalogName, schemaName, selector());

  return (
    <div className="space-y-1">
      {tables.map((table) => (
        <label key={table.fqn} className="flex items-center gap-2 p-2 hover:bg-accent rounded cursor-pointer">
          <input
            type="checkbox"
            checked={selectedTables.includes(table.fqn)}
            onChange={() => onToggleTable(table.fqn)}
            className="w-4 h-4"
          />
          <span className="text-sm">{table.name}</span>
        </label>
      ))}
    </div>
  );
}

function CatalogsSkeleton() {
  return (
    <Card>
      <CardHeader>
        <Skeleton className="h-8 w-48" />
      </CardHeader>
      <CardContent className="space-y-2">
        {[...Array(4)].map((_, i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </CardContent>
    </Card>
  );
}
