"use client";

import { useState } from "react";
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

// PROMPT-8 + PROMPT-7: shared accent so the chat sidebar matches the landing
// page's pipeline strip — one purple, used everywhere.
const ACCENT = "#534AB7";

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

import type { UploadResponse, ExistingSessionHint } from "@/types";

interface ConnectorSetupProps {
  onConnectorReady: (payload: {
    connector_config: Record<string, unknown>;
    file_hash: string;
    dataset_name: string;
  }) => void;
  onLoadExistingSession: (sessionId: string) => void;
}

export default function ConnectorSetup({ onConnectorReady, onLoadExistingSession }: ConnectorSetupProps) {
  const [type, setType] = useState<ConnectorType>("csv");
  const [config, setConfig] = useState<Partial<ConnectorConfig>>({});
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [testing, setTesting] = useState(false);
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [pendingUpload, setPendingUpload] = useState<UploadResponse | null>(null);

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
      const form = new FormData();
      form.append("file", file);

      const res = await fetch(`${API_URL}/connectors/upload`, {
        method: "POST",
        headers: await getAuthHeaders(),
        body: form,
      });
      const data = await res.json() as UploadResponse;
      if (!res.ok) throw new Error((data as unknown as { detail?: string }).detail || "Upload failed");
      setStatus({ ok: true, message: `✓ ${data.filename} uploaded` });
      if (data.existing_session) {
        setPendingUpload(data);
        return;
      }
      onConnectorReady({
        connector_config: data.connector_config,
        file_hash: data.file_hash,
        dataset_name: data.dataset_name,
      });
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setStatus({ ok: false, message });
      setPreview(null);
    } finally {
      setUploading(false);
    }
  }

  function acceptLoadPrevious(hint: ExistingSessionHint) {
    setPendingUpload(null);
    onLoadExistingSession(hint.session_id);
  }

  function acceptRunFresh(upload: UploadResponse) {
    setPendingUpload(null);
    onConnectorReady({
      connector_config: upload.connector_config,
      file_hash: upload.file_hash,
      dataset_name: upload.dataset_name,
    });
  }

  function cancelDedup() {
    setPendingUpload(null);
    // Intentionally do NOT delete the uploaded file from `uploads/` — orphan-file GC is out of scope.
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
        file_hash: "",
        dataset_name: body.table_name || body.spreadsheet_id || body.url || "",
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
      <div>
        <h2 className="text-sm font-semibold text-zinc-200 mb-0.5">Connect your data</h2>
        <p className="text-xs text-zinc-600">Choose a source type to get started</p>
      </div>

      {/* PROMPT-8 fix 1: all 4 tabs share the same surface, padding, font size,
          and accent. Active gets a purple border + bg tint; hover lifts the
          brightness without changing structure. */}
      <div className="grid grid-cols-2 gap-2">
        {SOURCE_TABS.map(({ key, label, Icon }) => {
          const active = type === key;
          return (
            <button
              key={key}
              onClick={() => { setType(key); setConfig({}); setStatus(null); if (key !== "csv") setPreview(null); }}
              className="group flex items-center gap-2 px-3 py-2.5 rounded-xl text-xs font-medium text-left transition-all duration-200 border bg-[#27272a] hover:bg-[#303035]"
              style={
                active
                  ? { borderColor: ACCENT, backgroundColor: `${ACCENT}26`, color: "#E6E3F8" }
                  : { borderColor: "#3a3a40", color: "#a1a1aa" }
              }
            >
              <Icon className="w-4 h-4 flex-shrink-0" style={{ color: active ? ACCENT : "#9ca3af" }} />
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
              className={`flex flex-col items-center justify-center gap-2 border-2 border-dashed rounded-xl min-h-[140px] p-5 cursor-pointer transition-all duration-200 ${
                uploading ? "bg-[#1f1f23]" : "bg-[#1a1a1d] hover:bg-[#1f1f23]"
              }`}
              style={{ borderColor: uploading ? ACCENT : "#3a3a40" }}
              onMouseEnter={(e) => { if (!uploading) e.currentTarget.style.borderColor = ACCENT; }}
              onMouseLeave={(e) => { if (!uploading) e.currentTarget.style.borderColor = "#3a3a40"; }}
            >
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center"
                style={{ backgroundColor: `${ACCENT}26`, color: "#C9C3F5" }}
              >
                <Upload className="w-5 h-5" />
              </div>
              <span className="text-sm font-medium text-zinc-300">
                {uploading ? "Uploading…" : "Click to upload or drag & drop"}
              </span>
              <span className="text-[11px] text-zinc-500">{ACCEPTED_FORMATS_LABEL}</span>
              <input
                type="file"
                accept={ACCEPTED_FORMATS}
                onChange={handleFileSelect}
                disabled={uploading}
                className="hidden"
              />
            </label>
          ) : (
            <div
              className="rounded-xl border bg-[#1a1a1d] p-3.5 flex items-start gap-3"
              style={{ borderColor: status?.ok ? ACCENT : "#3a3a40" }}
            >
              <div
                className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{ backgroundColor: `${ACCENT}26`, color: ACCENT }}
              >
                <FileSpreadsheet className="w-5 h-5" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-zinc-200 truncate" title={preview.file.name}>
                  {preview.file.name}
                </div>
                <div className="text-[11px] text-zinc-500 mt-0.5 flex items-center gap-2 flex-wrap">
                  <span>{formatBytes(preview.file.size)}</span>
                  {preview.columnCount !== null && (
                    <>
                      <span className="text-zinc-700">·</span>
                      <span>{preview.columnCount} column{preview.columnCount === 1 ? "" : "s"} detected</span>
                    </>
                  )}
                  {uploading && (
                    <>
                      <span className="text-zinc-700">·</span>
                      <span className="text-indigo-400">uploading…</span>
                    </>
                  )}
                </div>
              </div>
              <button
                onClick={clearPreview}
                disabled={uploading}
                aria-label="Remove file"
                className="w-7 h-7 inline-flex items-center justify-center rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
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
          <Field label="Table name" placeholder="orders" onChange={(v) => update("table_name", v)} />
        </div>
      )}

      {/* Google Sheets */}
      {type === "sheets" && (
        <div className="space-y-2.5">
          <Field label="Spreadsheet ID" placeholder="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms" onChange={(v) => update("spreadsheet_id", v)} />
          <Field label="Sheet name (optional)" placeholder="Sheet1" onChange={(v) => update("sheet_name", v)} />
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">Service account JSON</label>
            <textarea
              rows={3}
              placeholder='{"type": "service_account", ...}'
              onChange={(e) => update("service_account_json", e.target.value)}
              className="w-full rounded-xl bg-zinc-800/60 border border-zinc-700/50 text-zinc-200 text-xs p-2.5 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500/40 focus:border-indigo-500/40"
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
              <label className="block text-xs text-zinc-500 mb-1.5">Auth type</label>
              <select
                onChange={(e) => update("auth_type", e.target.value)}
                className="w-full rounded-xl bg-zinc-800/60 border border-zinc-700/50 text-zinc-300 text-xs px-2.5 py-2 focus:outline-none focus:ring-1 focus:ring-indigo-500/40"
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
          className="w-full py-2.5 rounded-xl text-white text-xs font-semibold disabled:opacity-50 transition-all"
          style={{ backgroundColor: ACCENT, boxShadow: `0 10px 30px -10px ${ACCENT}80` }}
        >
          {testing ? "Testing connection…" : "Test & Connect →"}
        </button>
      )}

      {status && (
        <div className={`rounded-xl px-3 py-2.5 text-xs font-medium ${
          status.ok
            ? "bg-emerald-500/10 border border-emerald-500/20 text-emerald-400"
            : "bg-red-500/10 border border-red-500/20 text-red-400"
        }`}>
          {status.message}
        </div>
      )}

      {pendingUpload?.existing_session && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 max-w-md shadow-xl">
            <h3 className="text-lg font-semibold mb-2">
              Previously analyzed dataset
            </h3>
            <p className="text-sm text-gray-600 mb-4">
              <span className="font-medium">{pendingUpload.filename}</span> was analyzed on{" "}
              {new Date(pendingUpload.existing_session.updated_at).toLocaleDateString()}.
              Load the previous analysis or run fresh?
            </p>
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={cancelDedup}
                className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => acceptRunFresh(pendingUpload)}
                className="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Run fresh
              </button>
              <button
                type="button"
                onClick={() => acceptLoadPrevious(pendingUpload.existing_session!)}
                className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                Load previous
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, placeholder, onChange }: { label: string; placeholder: string; onChange: (v: string) => void }) {
  return (
    <div>
      <label className="block text-xs text-zinc-500 mb-1.5">{label}</label>
      <input
        type="text"
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-xl bg-zinc-800/60 border border-zinc-700/50 text-zinc-200 text-xs px-3 py-2 placeholder:text-zinc-600 focus:outline-none focus:ring-1 focus:ring-indigo-500/40 focus:border-indigo-500/40 transition-all"
      />
    </div>
  );
}
