"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BarChart3, LogOut } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { href: "/chat", label: "Analysis" },
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

  return (
    <header className="flex h-12 flex-shrink-0 items-center gap-2 border-b border-border bg-card px-3 sm:px-4">
      {start}
      <Link href="/" className="flex items-center gap-2 font-semibold text-foreground">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <BarChart3 className="h-3.5 w-3.5" />
        </span>
        <span className="hidden sm:inline">AnalystAI</span>
      </Link>
      <nav className="ml-2 flex gap-0.5" aria-label="Main">
        {NAV.map(({ href, label }) => {
          const active = pathname === href;
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? "page" : undefined}
              className={`rounded-md px-2.5 py-1.5 text-sm ${
                active ? "bg-muted text-foreground font-medium" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </nav>
      <div className="flex-1" />
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
