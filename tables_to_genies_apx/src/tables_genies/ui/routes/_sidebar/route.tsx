import { createFileRoute, Link, Outlet } from '@tanstack/react-router';
import { Database, Sparkles, Share2, Boxes, Rocket, ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';

export const Route = createFileRoute('/_sidebar')({
  component: SidebarLayout,
});

const navItems = [
  { to: '/graph-explorer', label: '3. Explore Graph', icon: <Share2 size={16} /> },
  { to: '/catalog-browser', label: '1. Browse Catalogs', icon: <Database size={16} /> },
  { to: '/enrichment', label: '2. Enrich Tables', icon: <Sparkles size={16} /> },
  { to: '/genie-builder', label: '4. Build Rooms', icon: <Boxes size={16} /> },
  { to: '/genie-create', label: '5. Create Rooms', icon: <Rocket size={16} /> },
];

function SidebarLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className="flex h-screen bg-white dark:bg-slate-950 text-slate-900 dark:text-slate-50">
      {/* Sidebar */}
      <aside className={`${
        sidebarCollapsed ? 'w-16' : 'w-64'
      } bg-slate-50 dark:bg-slate-900 border-r border-slate-200 dark:border-slate-700 transition-all duration-300 relative`}>
        {/* Toggle Button */}
        <button
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          className="absolute -right-3 top-6 bg-blue-600 text-white rounded-full p-1 shadow-lg hover:bg-blue-700 transition-colors z-20"
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {sidebarCollapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>

        <div className="p-6">
          {!sidebarCollapsed && (
            <>
              <h1 className="text-2xl font-bold mb-2">Tables to Genies</h1>
              <p className="text-sm text-slate-600 dark:text-slate-400 mb-6">Create Genie rooms from UC tables</p>
            </>
          )}
          
          <nav className="space-y-2">
            {navItems.map((item) => (
              <Link
                key={item.to}
                to={item.to}
                className={`flex items-center gap-3 px-4 py-2 rounded-md hover:bg-slate-200 dark:hover:bg-slate-800 transition-colors text-slate-700 dark:text-slate-300 ${
                  sidebarCollapsed ? 'justify-center' : ''
                }`}
                activeProps={{
                  className: 'bg-blue-600 text-white hover:bg-blue-700 dark:hover:bg-blue-700',
                }}
                title={sidebarCollapsed ? item.label : undefined}
              >
                {item.icon}
                {!sidebarCollapsed && <span className="text-sm font-medium">{item.label}</span>}
              </Link>
            ))}
          </nav>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-white dark:bg-slate-950">
        <div className="container mx-auto p-8">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
