import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Providers } from "./providers";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

/**
 * BUG-04: Root layout is now a SERVER component.
 * "use client" was moved to providers.tsx so metadata exports work correctly.
 * BUG-05: Metadata is now rendered server-side as Next.js intends.
 */
export const metadata: Metadata = {
  title: "AnalystAI — AI Data Analyst",
  description:
    "Multi-agent AI system that eliminates the need for a data analyst in small companies.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${geistSans.variable} ${geistMono.variable} h-full antialiased dark`}>
      <body suppressHydrationWarning className="min-h-full flex flex-col bg-zinc-950">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
