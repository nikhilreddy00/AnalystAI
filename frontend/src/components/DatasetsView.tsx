"use client";

// Phase 5.6b: datasets are first-class. Every upload or connection, its versions (content hash),
// the analyses on it and their pinned charts. "New analysis" needs no re-upload; deleting a
// dataset deletes its analyses (right to erasure).

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft, Database, Plus, Upload } from "lucide-react";
import AppHeader from "./AppHeader";
import ChartRenderer from "./ChartRenderer";
import ConfirmDialog from "./ConfirmDialog";
import { titleOf } from "./SessionSidebar";
import { deleteDataset, getDataset, listDatasets, renameDataset, uploadFile } from "@/lib/api";
import type { Dataset, DatasetDetail } from "@/types";

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const BTN = "inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-sm text-foreground hover:bg-accent";

function shape(ds: Dataset) {
  if (ds.kind === "live") return `Live ${ds.source} connection`;
  const v = ds.latest;
  return v ? `${v.rows?.toLocaleString() ?? "?"} rows × ${v.columns.length} columns` : "";
}

function DatasetList() {
  const [items, setItems] = useState<Dataset[] | null>(null);
  useEffect(() => {
    let live = true;
    listDatasets().then((d) => live && setItems(d));
    return () => {
      live = false;
    };
  }, []);
  if (items === null) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (!items.length) {
    return (
      <p className="text-sm text-muted-foreground">
        No datasets yet. <Link href="/chat" className="text-primary hover:underline">Upload a file or connect a source</Link> to start.
      </p>
    );
  }
  return (
    <ul className="grid gap-2 sm:grid-cols-2" aria-label="Datasets">
      {items.map((ds) => (
        <li key={ds.id}>
          <Link href={`/datasets?d=${ds.id}`} className="block rounded-lg border border-border bg-card p-4 hover:bg-accent" data-testid="dataset-card">
            <span className="flex items-center gap-2 font-medium text-foreground">
              <Database className="h-4 w-4 flex-shrink-0 text-muted-foreground" aria-hidden />
              <span className="truncate">{ds.name}</span>
            </span>
            <span className="mt-1 block text-xs text-muted-foreground">{shape(ds)}</span>
            <span className="mt-1 block text-xs text-muted-foreground">
              {ds.kind === "upload" && ds.latest && <>Version {ds.latest.version} · {ds.latest.content_hash.slice(0, 8)} · </>}
              {day(ds.created_at)} · {ds.analyses} {ds.analyses === 1 ? "analysis" : "analyses"}
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function DatasetPage({ id }: { id: string }) {
  const router = useRouter();
  const [ds, setDs] = useState<DatasetDetail | null | undefined>(undefined);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => getDataset(id).then(setDs), [id]);
  useEffect(() => {
    void load();
  }, [load]);

  if (ds === undefined) return <p className="text-sm text-muted-foreground">Loading…</p>;
  if (ds === null) {
    return (
      <p className="text-sm text-muted-foreground">
        This dataset no longer exists. <Link href="/datasets" className="text-primary hover:underline">All datasets</Link>
      </p>
    );
  }

  async function saveName() {
    const n = name.split(/\s+/).join(" ").trim();
    setRenaming(false);
    if (n && ds && n !== ds.name && (await renameDataset(ds.id, n))) await load();
  }

  async function newVersion(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file || !ds) return;
    try {
      const up = await uploadFile(file, ds.id);
      setNote(up.status === "existing" ? `That file is already version ${up.version}.` : `Saved as version ${up.version}.`);
      await load();
    } catch (err) {
      setNote(err instanceof Error ? err.message : "Upload failed");
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <Link href="/datasets" className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-3.5 w-3.5" /> All datasets
        </Link>
        {renaming ? (
          <input
            autoFocus
            aria-label="Dataset name"
            value={name}
            maxLength={120}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => void saveName()}
            onKeyDown={(e) => {
              if (e.key === "Enter") void saveName();
              if (e.key === "Escape") setRenaming(false);
            }}
            className="w-full max-w-md rounded-md border border-ring bg-background px-2.5 py-1.5 text-lg font-semibold text-foreground focus:outline-none"
          />
        ) : (
          <h1 className="text-lg font-semibold text-foreground">{ds.name}</h1>
        )}
        <p className="text-sm text-muted-foreground">{shape(ds)}</p>
        <div className="flex flex-wrap gap-2">
          <Link href={`/chat?dataset=${ds.id}`} className={`${BTN} border-primary/40 bg-primary text-primary-foreground hover:bg-primary/90`}>
            <Plus className="h-3.5 w-3.5" /> New analysis
          </Link>
          {ds.kind === "upload" && (
            <>
              <button type="button" className={BTN} onClick={() => fileRef.current?.click()}>
                <Upload className="h-3.5 w-3.5" /> Upload new version
              </button>
              <input ref={fileRef} type="file" className="hidden" aria-label="New version file"
                     accept=".csv,.tsv,.xlsx,.json,.parquet" onChange={(e) => void newVersion(e)} />
            </>
          )}
          <button type="button" className={BTN} onClick={() => { setName(ds.name); setRenaming(true); }}>Rename</button>
          <button type="button" className={`${BTN} text-destructive`} onClick={() => setConfirming(true)}>Delete</button>
        </div>
        {note && <p role="status" className="text-sm text-foreground">{note}</p>}
      </div>

      {ds.kind === "upload" && (
        <section aria-label="Versions" className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Versions</h2>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
                <tr><th className="px-3 py-2 font-medium">Version</th><th className="px-3 py-2 font-medium">Rows</th>
                  <th className="px-3 py-2 font-medium">Columns</th><th className="px-3 py-2 font-medium">Content hash</th>
                  <th className="px-3 py-2 font-medium">Uploaded</th></tr>
              </thead>
              <tbody>
                {[...ds.versions].reverse().map((v) => (
                  <tr key={v.id} className="border-t border-border">
                    <td className="px-3 py-2">{v.version}{v.id === ds.latest?.id && <span className="ml-1.5 text-xs text-success">latest</span>}</td>
                    <td className="px-3 py-2">{v.rows?.toLocaleString() ?? "?"}</td>
                    <td className="px-3 py-2">{v.columns.length}</td>
                    <td className="px-3 py-2 font-mono text-xs">{v.content_hash.slice(0, 12)}</td>
                    <td className="px-3 py-2">{day(v.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section aria-label="Analyses on this dataset" className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Analyses · {ds.threads.length}</h2>
        {ds.threads.length ? (
          <ul className="divide-y divide-border rounded-lg border border-border bg-card">
            {ds.threads.map((t) => (
              <li key={t.session_id}>
                <Link href={`/chat?t=${t.session_id}`} className="block px-3 py-2 text-sm text-foreground hover:bg-accent">
                  <span className="block truncate">{titleOf(t)}</span>
                  <span className="text-xs text-muted-foreground">{day(t.updated_at)}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">No analyses on this dataset yet.</p>
        )}
      </section>

      {ds.charts.length > 0 && (
        <section aria-label="Pinned charts" className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Pinned charts</h2>
          <div className="grid gap-3 xl:grid-cols-2">
            {ds.charts.map((c) => <ChartRenderer key={c.id} config={c} />)}
          </div>
        </section>
      )}

      {confirming && (
        <ConfirmDialog
          title="Delete this dataset?"
          body={<>
            <span className="font-medium text-foreground">{ds.name}</span>, its {ds.versions.length || ""} stored
            {ds.versions.length === 1 ? " file" : " files"} and {ds.threads.length === 1 ? "its 1 analysis" : `all ${ds.threads.length} analyses`} on it
            will be deleted, with every report, run history and pinned chart. This can&apos;t be undone.
          </>}
          confirmLabel="Delete dataset"
          onCancel={() => setConfirming(false)}
          onConfirm={() => {
            setConfirming(false);
            void deleteDataset(ds.id).then(() => router.push("/datasets"));
          }}
        />
      )}
    </div>
  );
}

export default function DatasetsView() {
  const d = useSearchParams().get("d");
  return (
    <div className="flex min-h-dvh flex-col bg-background text-foreground">
      <AppHeader />
      <main className="mx-auto w-full max-w-5xl flex-1 space-y-4 p-4 sm:p-6">
        {d ? <DatasetPage key={d} id={d} /> : (
          <>
            <h1 className="text-lg font-semibold">Datasets</h1>
            <DatasetList />
          </>
        )}
      </main>
    </div>
  );
}
