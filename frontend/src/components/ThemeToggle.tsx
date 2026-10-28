"use client";

import { Moon, Sun } from "lucide-react";

/** Flips the `dark` class set by the pre-paint script in app/layout.tsx and remembers it. */
export default function ThemeToggle({ className = "" }: { className?: string }) {
  function toggle() {
    const dark = document.documentElement.classList.toggle("dark");
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      // storage blocked: the toggle still works for this page view
    }
  }
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label="Toggle dark mode"
      className={`inline-flex h-8 w-8 items-center justify-center rounded-md border border-border bg-card text-muted-foreground hover:text-foreground hover:bg-accent ${className}`}
    >
      <Sun className="h-4 w-4 dark:hidden" />
      <Moon className="hidden h-4 w-4 dark:block" />
    </button>
  );
}
