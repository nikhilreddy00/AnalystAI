"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, LogOut } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { fetchHealth } from "@/lib/api";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { href: "/chat", label: "Analysis" },
  { href: "/datasets", label: "Datasets" },
  { href: "/dashboard", label: "Dashboard" },
];

/** Top bar shared by /chat and /dashboard. `start` sits before the brand (e.g. a
 *  history-drawer button); `children` sit before the theme toggle. */
export default function AppHeader({
  start,
  children,
}: {
  start?: React.ReactNode;
  children?: React.ReactNode;
}) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const initials = (user?.displayName || user?.email || "U").slice(0, 2).toUpperCase();
  // Audit B4: a development server without Postgres keeps work in memory only. Say so.
  const [unsaved, setUnsaved] = useState(false);
  useEffect(() => {
    fetchHealth()
      .then((h) => setUnsaved(h?.persistent === false))
      .catch(() => setUnsaved(false));
  }, []);

  return (
    <header className="flex h-12 flex-shrink-0 items-center gap-1 border-b border-border bg-card px-3 sm:gap-2 sm:px-4">
      {start}
      <Link href="/" className="flex items-center gap-2 font-semibold text-foreground">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <BarChart3 className="h-3.5 w-3.5" />
        </span>
        <span className="hidden sm:inline">AnalystAI</span>
      </Link>
      <nav className="ml-1 flex min-w-0 gap-0.5 overflow-x-auto [scrollbar-width:none] sm:ml-2" aria-label="Main">
        {NAV.map(({ href, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`whitespace-nowrap rounded-md px-2 py-1.5 text-sm sm:px-2.5 ${
                active ? "bg-muted text-foreground font-medium" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="flex-1" />
      {unsaved && (
        <span
          role="status"
          title="This server has no database: analyses are lost when it restarts."
          className="rounded-md border border-warning/40 bg-warning/10 px-2 py-0.5 text-xs font-medium text-warning"
        >
          Not saved
        </span>
      )}
      {children}
      <ThemeToggle />
      {user && (
        <div className="flex items-center gap-1">
          <span
            className="hidden h-8 w-8 items-center justify-center rounded-full bg-muted text-[11px] font-semibold text-muted-foreground sm:flex"
            title={user.email ?? undefined}
          >
            {initials}
          </span>
          <button
            type="button"
            onClick={() => logout()}
            aria-label="Sign out"
            title="Sign out"
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      )}
    </header>
  );
}
