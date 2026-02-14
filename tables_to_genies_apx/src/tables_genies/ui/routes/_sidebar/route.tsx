import { createFileRoute, Link, Outlet } from '@tanstack/react-router';
import { Database, Sparkles, Share2, Boxes, Rocket } from 'lucide-react';

export const Route = createFileRoute('/_sidebar')({
  component: SidebarLayout,
});

const navItems = [
  { to: '/catalog-browser', label: '1. Browse Catalogs', icon: <Database size={16} /> },
  { to: '/enrichment', label: '2. Enrich Tables', icon: <Sparkles size={16} /> },
  { to: '/graph-explorer', label: '3. Explore Graph', icon: <Share2 size={16} /> },
  { to: '/genie-builder', label: '4. Build Rooms', icon: <Boxes size={16} /> },
  { to: '/genie-create', label: '5. Create Rooms', icon: <Rocket size={16} /> },
];

function SidebarLayout() {
  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside className="w-64 bg-card border-r border-border">
        <div className="p-6">
          <h1 className="text-2xl font-bold mb-2">Tables to Genies</h1>
          <p className="text-sm text-muted-foreground mb-6">Create Genie rooms from UC tables</p>
          
          <nav className="space-y-2">
            {navItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className="flex items-center gap-3 px-4 py-2 rounded-md hover:bg-accent transition-colors"
                activeProps={{
                  className: 'bg-primary text-primary-foreground hover:bg-primary/90',
                }}
              >
                {item.icon}
                <span className="text-sm font-medium">{item.label}</span>
              </Link>
            ))}
          </nav>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="container mx-auto p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
