// D5: AutoDashboard now owns the entire dashboard view — pinned charts are
// merged into its pool, layout (order + hidden) is editable in-place.
// Phase 3.5: shares the AppHeader with /chat.

import AppHeader from "@/components/AppHeader";
import AutoDashboard from "@/components/AutoDashboard";
import { DashboardDataProvider } from "@/context/DashboardDataContext";

export const metadata = { title: "Dashboard — AnalystAI" };

export default function DashboardPage() {
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <div className="sticky top-0 z-10">
        <AppHeader />
      </div>
      <main>
        <DashboardDataProvider>
          <AutoDashboard />
        </DashboardDataProvider>
      </main>
    </div>
  );
}
