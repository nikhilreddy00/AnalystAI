"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "@/context/AuthContext";

/**
 * BUG-04 + BUG-11: Client-side providers wrapper.
 * - Moved "use client" out of layout.tsx so it can export metadata server-side.
 * - BUG-11: QueryClient created inside useState to prevent sharing across SSR requests.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(() => new QueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>{children}</AuthProvider>
    </QueryClientProvider>
  );
}
