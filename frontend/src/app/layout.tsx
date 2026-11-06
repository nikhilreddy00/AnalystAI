import type { Metadata } from "next";
import localFont from "next/font/local";
import { Providers } from "./providers";
import "./globals.css";

// Bundled (IBM Plex, OFL: fonts/OFL.txt), never fetched from Google: a self-hosted install
// builds offline and its pages call no third party (CI's dev server once timed out on the fetch)
const plexSans = localFont({
  variable: "--font-plex-sans",
  src: [
    { path: "./fonts/ibm-plex-sans-latin-400-normal.woff2", weight: "400" },
    { path: "./fonts/ibm-plex-sans-latin-500-normal.woff2", weight: "500" },
    { path: "./fonts/ibm-plex-sans-latin-600-normal.woff2", weight: "600" },
  ],
});
const plexMono = localFont({
  variable: "--font-plex-mono",
  src: [
    { path: "./fonts/ibm-plex-mono-latin-400-normal.woff2", weight: "400" },
    { path: "./fonts/ibm-plex-mono-latin-500-normal.woff2", weight: "500" },
  ],
});

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

// Runs before paint so the saved/system theme applies without a flash.
// ThemeToggle (components/ThemeToggle.tsx) writes the same localStorage key.
const themeScript = `try{var t=localStorage.getItem("theme");if(t==="dark"||(t!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches))document.documentElement.classList.add("dark")}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${plexSans.variable} ${plexMono.variable} h-full antialiased`}
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body suppressHydrationWarning className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
