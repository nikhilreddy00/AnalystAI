"use client";

import { useId, useState } from "react";
import {
  Code,
  Database,
  FileSpreadsheet,
  Sheet,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";
import { API_URL, getAuthHeaders } from "@/lib/api";

type ConnectorType = "csv" | "sql" | "sheets" | "api";

interface ConnectorConfig {
  type: ConnectorType;
  file_path?: string;
  connection_string?: string;
  table_name?: string;
  spreadsheet_id?: string;
  sheet_name?: string;
  service_account_json?: string;
  url?: string;
  auth_type?: string;
  auth_value?: string;
  pagination_key?: string;
}


const SOURCE_TABS: { key: ConnectorType; label: string; Icon: LucideIcon }[] = [
  { key: "csv",    label: "CSV / Excel",    Icon: FileSpreadsheet },
  { key: "sql",    label: "SQL Database",   Icon: Database },
  { key: "sheets", label: "Google Sheets",  Icon: Sheet },
  { key: "api",    label: "REST API",       Icon: Code },
];

const ACCEPTED_FORMATS = ".csv,.xlsx,.xls,.tsv,.json,.parquet";
const ACCEPTED_FORMATS_LABEL = "CSV, XLSX, XLS, TSV, JSON, Parquet — max 50 MB";

interface FilePreview {
  file: File;
  columnCount: number | null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Best-effort column-count preview without uploading. Works for text-format
// files we can sniff a header from; returns null otherwise (binary formats).
async function sniffColumnCount(file: File): Promise<number | null> {
  const name = file.name.toLowerCase();
  if (name.endsWith(".csv") || name.endsWith(".tsv") || name.endsWith(".txt")) {
    try {
      const slice = file.slice(0, 8192);
      const text = await slice.text();
      const firstLine = text.split(/\r?\n/).find((line) => line.trim().length > 0);
      if (!firstLine) return null;
      const delim = name.endsWith(".tsv") ? "\t" : ",";
      return firstLine.split(delim).length;
    } catch {
      return null;
    }
  }
  if (name.endsWith(".json")) {
    try {
      const slice = file.slice(0, 16384);
      const text = await slice.text();
      const parsed = JSON.parse(text);
      const sample = Array.isArray(parsed) ? parsed[0] : parsed;
      if (sample && typeof sample === "object") return Object.keys(sample).length;
    } catch {
      return null;
    }
  }
  return null;
}

import type { DataSource, UploadResponse } from "@/types";
import { uploadFile as apiUpload } from "@/lib/api";

interface ConnectorSetupProps {
  /** The data a new analysis is about; `upload` says how an upload was filed (Phase 5.6b). */
  onConnectorReady: (source: DataSource, upload?: UploadResponse) => void;
}

export default function ConnectorSetup({ onConnectorReady }: ConnectorSetupProps) {
  const [type, setType] = useState<ConnectorType>("csv");
  const [config, setConfig] = useState<Partial<ConnectorConfig>>({});
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [preview, setPreview] = useState<FilePreview | null>(null);

  const update = (key: string, value: string) =>
    setConfig((prev) => ({ ...prev, [key]: value }));

  async function handleFileSelect(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    // Show the user what was selected before the upload completes.
    const columnCount = await sniffColumnCount(file);
    setPreview({ file, columnCount });
    setStatus(null);
    await uploadFile(file);
    // Allow re-selecting the same filename later.
    e.target.value = "";
  }

  async function uploadFile(file: File) {
    setUploading(true);
    try {
      const data = await apiUpload(file);
      setStatus({ ok: true, message: `✓ ${data.filename} uploaded` });
      onConnectorReady({ dataset_version_id: data.dataset_version_id, dataset_name: data.dataset_name }, data);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setStatus({ ok: false, message });
      setPreview(null);
    } finally {
      setUploading(false);
    }
  }

  function clearPreview() {
    setPreview(null);
    setStatus(null);
  }

  async function handleTest() {
    setTesting(true);
    setStatus(null);
    try {
      const body: ConnectorConfig = { type, ...config } as ConnectorConfig;

      const res = await fetch(`${API_URL}/connectors/test`, {
        method: "POST",
        headers: await getAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify(body),
      });
      const data = await res.json();
      setStatus({ ok: data.success, message: data.message });
      if (data.success) onConnectorReady({
        connector_config: body as unknown as Record<string, unknown>,
        dataset_name: body.table_name || body.spreadsheet_id || body.url || (body.type === "sql" ? "Database" : ""),
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Connection test failed";
      setStatus({ ok: false, message });
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-1 rounded-md border border-border bg-muted p-1 sm:grid-cols-4" role="group" aria-label="Source type">
        {SOURCE_TABS.map(({ key, label, Icon }) => {
          const active = type === key;
          return (
            <button
              key={key}
              type="button"
              aria-pressed={active}
              onClick={() => { setType(key); setConfig({}); setStatus(null); if (key !== "csv") setPreview(null); }}
              className={`flex items-center justify-center gap-1.5 rounded px-2 py-1.5 text-xs font-medium ${
                active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="h-3.5 w-3.5 flex-shrink-0" />
              <span className="truncate">{label}</span>
            </button>
          );
        })}
      </div>

      {/* CSV / file upload */}
      {type === "csv" && (
        <div className="space-y-2">
          {/* PROMPT-8 fix: taller drop zone (min-h-[140px]) — this is the primary
              action on the page. Once a file is picked, the zone swaps to a
              metadata card (filename / size / columns / dismiss). */}
          {!preview ? (
            <label
              className={`flex min-h-[140px] cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-[1.5px] border-dashed p-5 text-center focus-within:border-ring ${
                uploading ? "border-primary bg-brand-soft" : "border-border hover:border-primary hover:bg-accent"
              }`}
            >
              <Upload className="h-5 w-5 text-primary" />
              <span className="text-sm font-medium text-foreground">
                {uploading ? "Uploading…" : "Click to upload or drag & drop"}
              </span>
              <span className="text-[11px] text-muted-foreground">{ACCEPTED_FORMATS_LABEL}</span>
              <input
                type="file"
                accept={ACCEPTED_FORMATS}
                onChange={handleFileSelect}
                disabled={uploading}
                className="sr-only"
              />
            </label>
          ) : (
            <div className={`flex items-start gap-3 rounded-lg border bg-card p-3 ${status?.ok ? "border-success/50" : "border-border"}`}>
              <FileSpreadsheet className="mt-0.5 h-5 w-5 flex-shrink-0 text-primary" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-foreground truncate" title={preview.file.name}>
                  {preview.file.name}
                </div>
                <div className="text-[11px] text-muted-foreground mt-0.5 flex items-center gap-2 flex-wrap">
                  <span>{formatBytes(preview.file.size)}</span>
                  {preview.columnCount !== null && (
                    <>
                      <span className="text-foreground">·</span>
                      <span>{preview.columnCount} column{preview.columnCount === 1 ? "" : "s"} detected</span>
                    </>
                  )}
                  {uploading && (
                    <>
                      <span className="text-foreground">·</span>
                      <span className="text-primary">uploading…</span>
                    </>
                  )}
                </div>
              </div>
              <button
                onClick={clearPreview}
                disabled={uploading}
                aria-label="Remove file"
                className="w-7 h-7 inline-flex items-center justify-center rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>
      )}

      {/* SQL */}
      {type === "sql" && (
        <div className="space-y-2.5">
          <Field label="Connection string" placeholder="postgresql://user:pass@host:5432/db" onChange={(v) => update("connection_string", v)} />
          <Field label="Table name (optional)" placeholder="Leave blank to ask questions across the whole database" onChange={(v) => update("table_name", v)} />
        </div>
      )}

      {/* Google Sheets */}
      {type === "sheets" && (
        <div className="space-y-2.5">
          <Field label="Spreadsheet ID" placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" onChange={(v) => update("spreadsheet_id", v)} />
          <Field label="Sheet name (optional)" placeholder="Sheet1" onChange={(v) => update("sheet_name", v)} />
          <div>
            <label htmlFor="sheets-sa-json" className="block text-xs text-muted-foreground mb-1.5">Service account JSON</label>
            <textarea
              id="sheets-sa-json"
              rows={3}
              placeholder='{"type": "service_account", ...}'
              onChange={(e) => update("service_account_json", e.target.value)}
              className="w-full rounded-md bg-background border border-input text-foreground text-xs p-2.5 resize-none focus:outline-none focus:border-ring"
            />
          </div>
        </div>
      )}

      {/* REST API */}
      {type === "api" && (
        <div className="space-y-2.5">
          <Field label="Endpoint URL" placeholder="https://api.example.com/data" onChange={(v) => update("url", v)} />
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label htmlFor="api-auth-type" className="block text-xs text-muted-foreground mb-1.5">Auth type</label>
              <select
                id="api-auth-type"
                onChange={(e) => update("auth_type", e.target.value)}
                className="w-full rounded-md bg-background border border-input text-muted-foreground text-xs px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-primary/40"
              >
                <option value="">None</option>
                <option value="bearer">Bearer token</option>
                <option value="api_key">API key</option>
                <option value="basic">Basic auth</option>
              </select>
            </div>
            <Field label="Auth value" placeholder="token or user:pass" onChange={(v) => update("auth_value", v)} />
          </div>
          <Field label="Pagination key (optional)" placeholder="next_url" onChange={(v) => update("pagination_key", v)} />
        </div>
      )}

      {type !== "csv" && (
        <button
          onClick={handleTest}
          disabled={testing}
          type="button"
          className="w-full rounded-md bg-primary py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          {testing ? "Testing connection…" : "Test and connect"}
        </button>
      )}

      {status && (
        <div role="status" className={`rounded-md px-3 py-2 text-xs font-medium ${
          status.ok
            ? "bg-success/10 border border-success/20 text-success"
            : "bg-destructive/10 border border-destructive/20 text-destructive"
        }`}>
          {status.message}
        </div>
      )}

    </div>
  );
}

function Field({ label, placeholder, onChange }: { label: string; placeholder: string; onChange: (v: string) => void }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="block text-xs text-muted-foreground mb-1.5">{label}</label>
      <input
        id={id}
        type="text"
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-md bg-background border border-input text-foreground text-xs px-3 py-2 placeholder:text-muted-foreground focus:outline-none focus:border-ring transition-all"
      />
    </div>
  );
}
