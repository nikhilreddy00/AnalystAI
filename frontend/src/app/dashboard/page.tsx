// D5: AutoDashboard now owns the entire dashboard view — pinned charts are
// merged into its pool, layout (order + hidden) is editable in-place.
// The legacy `DashboardCanvas` view is gone.

import Link from "next/link";
import { ArrowRight, LayoutDashboard } from "lucide-react";
import AutoDashboard from "@/components/AutoDashboard";
import { DashboardDataProvider } from "@/context/DashboardDataContext";

export const metadata = { title: "Dashboard — AnalystAI" };

export default function DashboardPage() {
  return (
    <div className="min-h-screen bg-zinc-50 text-zinc-900">
      <header className="border-b border-zinc-200 px-6 py-4 bg-white/95 backdrop-blur-sm sticky top-0 z-10 shadow-sm">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white shadow-md">
              <LayoutDashboard className="w-4 h-4" />
            </div>
            <div>
              <h1 className="text-base font-bold text-zinc-900">AnalystAI Dashboard</h1>
              <p className="text-xs text-zinc-500">Auto-built from your latest analysis · click charts to cross-filter · edit layout to reorder</p>
            </div>
          </div>
          <Link
            href="/chat"
            className="inline-flex items-center gap-2 text-sm bg-zinc-900 hover:bg-zinc-800 text-white px-5 py-2.5 rounded-xl font-medium transition-colors shadow-md"
          >
            Open chat
            <ArrowRight className="w-4 h-4" />
          </Link>
        </div>
      </header>

      <main className="max-w-7xl mx-auto">
        <DashboardDataProvider>
          <AutoDashboard />
        </DashboardDataProvider>
      </main>
    </div>
  );
}
