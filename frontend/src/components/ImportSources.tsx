"use client";

import { useEffect, useId, useState } from "react";
import { RefreshCw } from "lucide-react";
import {
  createImport, deleteImport, getImport, listImports, refreshImport, replaceImportKey,
  setImportWindow, type ImportTable, type ImportView,
} from "@/lib/api";
import type { DataSource } from "@/types";

/** Phase 5.8c: Stripe and Shopify imported as datasets, got again on demand. A failed or
 * interrupted refresh keeps the last good copy and says so: amber, with the copy's time and the
 * reason in words, never green (GLOSSARY: Stale). The key is never shown again once saved. */

type Vendor = "stripe" | "shopify";

const SETUP: Record<Vendor, { label: string; steps: string[] }> = {
  stripe: {
    label: "Stripe",
    steps: [
      "In Stripe, open Developers → API keys and click Create restricted key.",
      "Set Read on Subscriptions, Invoices, Customers and Products (Accounts too, to show your account's name). Leave everything else at None.",
      "Paste the key (rk_live_… or rk_test_…). We recommend a read-only key; a live secret key (sk_live_…) is refused.",
    ],
  },
  shopify: {
    label: "Shopify",
    steps: [
      "In the Shopify Dev Dashboard (dev.shopify.com), create an app in the same organization as your store, with the scopes read_orders, read_products, read_locations.",
      "In the app's Settings → Protected customer data access, select Level 1 with the reason Analytics. Customers' names, emails and addresses aren't needed.",
      "Release the app version and install it on your store.",
      "Paste your store's address and the app's Client ID and Client secret. Shopify shares the last 60 days of orders.",
    ],
  },
};

function when(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
}

function minutesSince(iso: string | null): number {
  return iso ? Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000)) : 0;
}

interface Props { onConnectorReady: (source: DataSource) => void }

export default function ImportSources({ onConnectorReady }: Props) {
  const [imports, setImports] = useState<ImportView[]>([]);
  const [vendor, setVendor] = useState<Vendor>("stripe");
  const [form, setForm] = useState({ key: "", shop: "", client_id: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { listImports().then(setImports).catch(() => {}); }, []);

  // A refresh runs on the server: the page asks how it is going until it ends.
  const running = imports.filter((i) => i.state === "refreshing").map((i) => i.id).join(",");
  useEffect(() => {
    if (!running) return;
    const t = setInterval(async () => {
      const fresh = await Promise.all(running.split(",").map((id) => getImport(id).catch(() => null)));
      setImports((all) => all.map((i) => fresh.find((f) => f?.id === i.id) ?? i));
    }, 2000);
    return () => clearInterval(t);
  }, [running]);

  const replace = (next: ImportView) => setImports((all) => all.map((i) => (i.id === next.id ? next : i)));

  async function connect(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const made = await createImport({ vendor, key: form.key, ...(vendor === "shopify" ? { shop: form.shop, client_id: form.client_id } : {}) });
      setImports((all) => [made, ...all]);
      setForm({ key: "", shop: "", client_id: "" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "The import failed");
    } finally {
      setBusy(false);
    }
  }

  const setup = SETUP[vendor];
  return (
    <div className="space-y-4">
      <form onSubmit={connect} className="space-y-2.5" aria-label="Import a vendor account">
        <div className="grid grid-cols-2 gap-1 rounded-md border border-border bg-muted p-1" role="group" aria-label="Vendor">
          {(Object.keys(SETUP) as Vendor[]).map((v) => (
            <button key={v} type="button" aria-pressed={vendor === v} onClick={() => { setVendor(v); setError(null); }}
              className={`rounded px-2 py-1.5 text-xs font-medium ${vendor === v ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}>
              {SETUP[v].label}
            </button>
          ))}
        </div>
        <ol className="list-decimal space-y-1 pl-4 text-[11px] text-muted-foreground">
          {setup.steps.map((s) => <li key={s}>{s}</li>)}
        </ol>
        {vendor === "shopify" && (
          <>
            <Input label="Store address" placeholder="your-store.myshopify.com" value={form.shop} onChange={(v) => setForm({ ...form, shop: v })} />
            <Input label="Client ID" placeholder="From the app's Settings" value={form.client_id} onChange={(v) => setForm({ ...form, client_id: v })} />
          </>
        )}
        <Input label={vendor === "stripe" ? "Restricted key" : "Client secret"} type="password"
          placeholder={vendor === "stripe" ? "rk_live_… or rk_test_…" : "From the app's Settings"}
          value={form.key} onChange={(v) => setForm({ ...form, key: v })} />
        <button type="submit" disabled={busy || !form.key.trim() || (vendor === "shopify" && (!form.shop.trim() || !form.client_id.trim()))}
          className="w-full rounded-md bg-primary py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          {busy ? "Checking the key…" : `Import from ${setup.label}`}
        </button>
        {error && <div role="alert" className="rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs font-medium text-destructive">{error}</div>}
      </form>

      {imports.map((imp) => (
        <ImportCard key={imp.id} imp={imp} onChange={replace} onRemoved={(id) => setImports((all) => all.filter((i) => i.id !== id))}
          onAnalyse={(t) => onConnectorReady({ dataset_id: t.dataset_id, dataset_name: t.label })} />
      ))}
    </div>
  );
}

function ImportCard({ imp, onChange, onRemoved, onAnalyse }: {
  imp: ImportView; onChange: (i: ImportView) => void; onRemoved: (id: string) => void; onAnalyse: (t: ImportTable) => void;
}) {
  const [note, setNote] = useState<string | null>(null);
  const [rekey, setRekey] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [withTables, setWithTables] = useState(true);
  const [form, setForm] = useState({ key: "", shop: "", client_id: "" });
  const refreshing = imp.state === "refreshing";
  const failed = imp.state === "failed";

  async function act(f: () => Promise<ImportView>) {
    setNote(null);
    try { onChange(await f()); } catch (err) { setNote(err instanceof Error ? err.message : "That didn't work"); }
  }

  const status = refreshing
    ? `Getting the latest data… table ${imp.progress?.table ?? 1} of ${imp.progress?.of ?? imp.tables.length}${imp.progress?.name ? ` · ${imp.progress.name}` : ""} · ${minutesSince(imp.started_at)} min of 15`
    : failed ? (imp.said ?? "The last refresh didn't finish. The last good copy is kept.")
      : imp.last_success_at ? `Up to date as of ${when(imp.last_success_at)}` : "No copy yet";

  return (
    <section aria-label={`${imp.label} import`} className={`space-y-2 rounded-lg border bg-card p-3 ${failed ? "border-warning/60" : "border-border"}`}>
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-foreground">{imp.label} · {imp.account}</span>
        {imp.test_mode && <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">Test data</span>}
      </div>
      <p role="status" className={`text-xs ${failed ? "text-warning" : "text-muted-foreground"}`}>{status}</p>
      {imp.window_from && <p className="text-[11px] text-muted-foreground">Imports data from {imp.window_from} on (the last 12 months).</p>}
      <ul className="space-y-1.5">
        {imp.tables.map((t) => (
          <li key={t.name} className="flex items-start justify-between gap-2 text-xs">
            <div className="min-w-0">
              <div className="font-medium text-foreground">{t.label}</div>
              <div className="text-[11px] text-muted-foreground">
                {t.as_of ? `${(t.rows ?? 0).toLocaleString()} rows · as of ${when(t.as_of)}` : "No copy yet"}
                {t.filed === "unchanged" && " · no changes since the last copy"}
                {t.filed === "empty" && " · nothing to import"}
                {t.cut_from && ` · rows before ${t.cut_from} not included`}
              </div>
              {t.said && <div className="text-[11px] text-warning">{t.said}{t.as_of ? ` Showing data from ${when(t.as_of)}.` : ""}</div>}
            </div>
            <button type="button" disabled={!t.as_of || t.filed === "empty" || !t.rows} onClick={() => onAnalyse(t)}
              className="flex-shrink-0 rounded-md border border-border px-2 py-1 text-[11px] font-medium text-foreground hover:bg-accent disabled:opacity-40">
              Analyse
            </button>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" disabled={refreshing} onClick={() => act(() => refreshImport(imp.id))}
          className="inline-flex items-center gap-1 rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} /> Get latest data
        </button>
        <button type="button" onClick={() => setRekey(!rekey)} className="rounded-md border border-border px-2.5 py-1.5 text-xs text-foreground hover:bg-accent">Replace key</button>
        <label className="inline-flex items-center gap-1 text-[11px] text-muted-foreground">
          <input type="checkbox" checked={!!imp.window_from} onChange={(e) => act(() => setImportWindow(imp.id, e.target.checked))} />
          Last 12 months only
        </label>
        <button type="button" onClick={() => setConfirming(true)} className="ml-auto rounded-md px-2.5 py-1.5 text-xs text-destructive hover:bg-destructive/10">Delete</button>
      </div>
      {rekey && (
        <form className="space-y-2" onSubmit={(e) => { e.preventDefault(); act(() => replaceImportKey(imp.id, { vendor: imp.vendor, key: form.key, ...(imp.vendor === "shopify" ? { shop: form.shop, client_id: form.client_id } : {}) })).then(() => setRekey(false)); }}>
          {imp.vendor === "shopify" && (
            <>
              <Input label="Store address" placeholder="your-store.myshopify.com" value={form.shop} onChange={(v) => setForm({ ...form, shop: v })} />
              <Input label="Client ID" placeholder="From the app's Settings" value={form.client_id} onChange={(v) => setForm({ ...form, client_id: v })} />
            </>
          )}
          <Input label={imp.vendor === "stripe" ? "New restricted key" : "New client secret"} type="password" placeholder="Checked before it is saved" value={form.key} onChange={(v) => setForm({ ...form, key: v })} />
          <button type="submit" disabled={!form.key.trim()} className="rounded-md bg-primary px-2.5 py-1.5 text-xs font-medium text-primary-foreground disabled:opacity-50">Save the new key</button>
        </form>
      )}
      {confirming && (
        <div role="group" aria-label="Delete this import" className="space-y-2 rounded-md border border-destructive/30 p-2 text-xs">
          <p className="text-foreground">Delete this import and its saved key?</p>
          <label className="flex items-center gap-1.5 text-muted-foreground">
            <input type="checkbox" checked={withTables} onChange={(e) => setWithTables(e.target.checked)} />
            Also delete the {imp.tables.length} imported tables
          </label>
          <div className="flex gap-2">
            <button type="button" onClick={async () => { try { await deleteImport(imp.id, withTables); onRemoved(imp.id); } catch (err) { setNote(err instanceof Error ? err.message : "That didn't work"); } }}
              className="rounded-md bg-destructive px-2.5 py-1.5 font-medium text-destructive-foreground">Delete</button>
            <button type="button" onClick={() => setConfirming(false)} className="rounded-md border border-border px-2.5 py-1.5 text-foreground">Keep it</button>
          </div>
        </div>
      )}
      {note && <p role="alert" className="text-xs text-destructive">{note}</p>}
    </section>
  );
}

function Input({ label, placeholder, value, onChange, type = "text" }: {
  label: string; placeholder: string; value: string; onChange: (v: string) => void; type?: "text" | "password";
}) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-xs text-muted-foreground">{label}</label>
      <input id={id} type={type} value={value} autoComplete="off" placeholder={placeholder} onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground focus:border-ring focus:outline-none" />
    </div>
  );
}
