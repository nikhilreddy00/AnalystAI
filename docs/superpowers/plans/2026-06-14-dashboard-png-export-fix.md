# Dashboard PNG Export Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten the dashboard full-PNG export so it captures only KPIs + chart grid + (optional) filter-chip indicator + footer. Exclude FilterSidebar, records-count text, and interactive chip chrome.

**Architecture:** Restructure the `sectionRef` snapshot region in `AutoDashboard.tsx` so the FilterSidebar lives outside it; render the cross-filter chip row conditionally inside the snapshot region (with X icons + Reset button marked `data-html2canvas-ignore`). Extend `downloadAsPng.ts` with an optional `footer` argument that paints a small footer band into the resulting PNG via an offscreen canvas.

**Tech Stack:** Next.js 16 App Router, React 19, `html-to-image` (preserved — Tailwind v4 `oklch()` compatibility), TypeScript strict.

**Spec:** [docs/superpowers/specs/2026-06-14-dashboard-png-export-fix-design.md](../specs/2026-06-14-dashboard-png-export-fix-design.md)

---

## Task 1: PNG export tighten (single task, two files)

Both files are tightly coupled — the caller in `AutoDashboard.tsx` depends on the new `options.footer` argument in `downloadAsPng.ts`. Split into atomic steps within one task.

**Files:**
- Modify: `frontend/src/lib/downloadAsPng.ts`
- Modify: `frontend/src/components/AutoDashboard.tsx`

### Step 1: Extend `downloadElementAsPng` signature with optional footer

- [ ] **Edit `frontend/src/lib/downloadAsPng.ts`** — replace the entire file body with the version below.

```ts
// Phase 4: client-side PNG export. Uses html-to-image (not html2canvas) because
// Tailwind v4 / shadcn use modern CSS color functions like oklch() / lab() /
// color-mix() that html2canvas cannot parse — html-to-image handles them natively.
//
// D6: default background swapped to white to match the new light dashboard at
// /dashboard (D1). The dashboard is the only PNG-export consumer today.
//
// Gap 3: optional `footer` paints a small footer band below the captured image
// via an offscreen canvas. Used by the full-dashboard export; per-chart export
// omits it (call without options).

export async function downloadElementAsPng(
  element: HTMLElement,
  filename: string,
  options?: { backgroundColor?: string; footer?: string | null },
): Promise<void> {
  const backgroundColor = options?.backgroundColor ?? "#ffffff";
  const footer = options?.footer ?? null;

  // Dynamic import keeps the library out of the initial bundle — only loaded
  // when a user actually clicks download.
  const { toPng } = await import("html-to-image");

  const dataUrl = await toPng(element, {
    backgroundColor,
    pixelRatio: 2, // sharper exports
    cacheBust: true,
    filter: (node) => {
      // Skip any element opted-out of the snapshot
      return !(node instanceof HTMLElement && node.dataset.html2canvasIgnore !== undefined);
    },
  });

  const safeName = filename.replace(/[^a-z0-9_\-]/gi, "_") || "chart";
  const finalUrl = footer ? await paintFooter(dataUrl, footer, backgroundColor) : dataUrl;

  const link = document.createElement("a");
  link.href = finalUrl;
  link.download = `${safeName}.png`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

// FOOTER_H is in source-px; we multiply by the toPng pixelRatio (2) so the
// footer band visually matches the rendered dashboard's resolution.
const FOOTER_H = 48;
const PIXEL_RATIO = 2;

async function paintFooter(
  dataUrl: string,
  footer: string,
  backgroundColor: string,
): Promise<string> {
  const img = await loadImage(dataUrl);
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight + FOOTER_H * PIXEL_RATIO;

  const ctx = canvas.getContext("2d");
  if (!ctx) return dataUrl; // graceful no-op if 2d context unavailable

  ctx.fillStyle = backgroundColor;
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0);

  ctx.font = `${12 * PIXEL_RATIO}px ui-sans-serif, system-ui, sans-serif`;
  ctx.fillStyle = "#9ca3af"; // zinc-400
  ctx.textBaseline = "middle";
  ctx.fillText(footer, 20 * PIXEL_RATIO, img.naturalHeight + (FOOTER_H * PIXEL_RATIO) / 2);

  return canvas.toDataURL("image/png");
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}
```

- [ ] **Verify per-chart caller still typechecks** — the existing call at `AutoDashboard.tsx:181` (`downloadElementAsPng(el, title)`) must still work because `options` is optional.

### Step 2: Restructure `AutoDashboard.tsx` snapshot region

This step moves `sectionRef` from the outer wrapper down to the right column only, and converts the chip row to a conditional render inside the right column.

- [ ] **Edit `frontend/src/components/AutoDashboard.tsx`** — replace lines 256–360 (from `{/* The snapshot region */}` through the closing `</div>` of the outer snapshot wrapper, i.e. the `</div>` just before `</section>`) with the block below.

```tsx
      {/* Snapshot region restructure (Gap 3):
          - FilterSidebar lives OUTSIDE sectionRef (and is also marked
            data-html2canvas-ignore as belt-and-suspenders).
          - sectionRef now wraps ONLY the right column (KPIs + chart grid).
          - The cross-filter chip row renders conditionally INSIDE sectionRef
            when filters are active. Interactive chrome (X icons, Reset button)
            is marked data-html2canvas-ignore so the PNG sees flat chip labels. */}
      <div className="flex gap-4">
        <div data-html2canvas-ignore>
          <FilterSidebar />
        </div>
        <div ref={sectionRef} className="flex-1 min-w-0 space-y-4">
          {/* Cross-filter chip row — only in DOM (and PNG) when filters active */}
          {activeFilterCount > 0 && (
            <div className="flex items-center justify-between gap-3 rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm flex-wrap">
              <div className="flex items-center gap-2 text-sm text-zinc-700 flex-wrap">
                <Filter className="w-4 h-4 text-indigo-600 flex-shrink-0" />
                <span className="font-semibold">Filtered by</span>
                <div className="flex items-center gap-1.5 flex-wrap pl-1">
                  {Object.entries(filters).map(([key, f]) => (
                    <button
                      key={key}
                      onClick={() => setFilter(key, null)}
                      className="inline-flex items-center gap-1.5 bg-indigo-50 border border-indigo-200 text-indigo-700 text-xs font-medium px-2.5 py-1 rounded-full hover:bg-indigo-100 transition-colors"
                      title="Click to remove this filter"
                    >
                      <span className="text-indigo-500">{key}</span>
                      <span>:</span>
                      <span className="font-semibold">{formatFilterChip(f)}</span>
                      <span data-html2canvas-ignore className="inline-flex">
                        <X className="w-3 h-3" />
                      </span>
                    </button>
                  ))}
                </div>
              </div>
              <button
                onClick={clearFilters}
                data-html2canvas-ignore
                className="inline-flex items-center gap-1.5 text-xs bg-zinc-50 hover:bg-zinc-100 border border-zinc-200 text-zinc-700 px-3 py-1.5 rounded-lg font-semibold transition-colors"
              >
                <RotateCcw className="w-3 h-3" />
                Reset
              </button>
            </div>
          )}

          {/* KPI row */}
          {hasBriefing && latest?.briefing && <KPISection briefing={latest.briefing} />}

          {/* Records indicator — excluded from PNG */}
          {hasActiveFilters && (
            <div data-html2canvas-ignore className="text-xs text-zinc-500 mb-2 px-6">
              Showing {filteredRows.length.toLocaleString()} of {rowCount.toLocaleString()} records
            </div>
          )}

          {/* Chart grid */}
          {visible.length > 0 ? (
            <ChartGrid
              charts={visible}
              filteredRows={filteredRows}
              hasActiveFilters={hasActiveFilters}
              onElementClick={setFilter}
              chartRefs={chartRefs}
              busy={busy}
              onDownloadChart={handleDownloadChart}
              editMode={editMode}
              visibleOrder={visibleOrder}
              onMoveUp={(id) => moveUp(id, visibleOrder)}
              onMoveDown={(id) => moveDown(id, visibleOrder)}
              onHide={(id) => hide(id, visibleOrder)}
            />
          ) : (
            <div className="rounded-2xl border border-dashed border-zinc-300 bg-white p-8 text-center text-sm text-zinc-500">
              {totalPool > 0
                ? "All charts are hidden. Restore some below in edit mode."
                : "No charts on the latest analysis. Run an overview in the chat to populate the grid."}
            </div>
          )}

          {/* Hidden-chart restore panel — only shows in edit mode, excluded from PNG */}
          {editMode && hidden.length > 0 && (
            <div
              className="rounded-2xl border border-zinc-200 bg-white shadow-sm overflow-hidden"
              data-html2canvas-ignore
            >
              <div className="px-5 py-3 border-b border-zinc-200 flex items-center gap-2 text-sm font-semibold text-zinc-800">
                <EyeOff className="w-4 h-4 text-zinc-500" />
                Hidden charts ({hidden.length})
              </div>
              <div className="p-5 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {hidden.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => restore(c.id, visibleOrder)}
                    className="text-left text-xs bg-zinc-50 hover:bg-indigo-50 border border-zinc-200 hover:border-indigo-300 text-zinc-700 hover:text-indigo-700 px-3 py-2.5 rounded-lg transition-colors flex items-center gap-2"
                  >
                    <Plus className="w-3 h-3 flex-shrink-0" />
                    <span className="truncate">{c.title}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
```

**Key diffs vs. the original block:**

| Original | New |
|---|---|
| `<div ref={sectionRef}>` wraps both filter row and `<div className="flex">` | `<div ref={sectionRef}>` is now the right column (`flex-1 min-w-0`) only |
| Filter row always renders, marked `data-html2canvas-ignore` | Filter row renders only when `activeFilterCount > 0`, marker dropped |
| `<X />` icon inside chip is bare | `<X />` wrapped in `<span data-html2canvas-ignore>` |
| Reset button has no marker | Reset button has `data-html2canvas-ignore` |
| Reset button has `disabled={activeFilterCount === 0}` | Reset button has no disabled prop (the whole row is conditional on `activeFilterCount > 0`) |
| `<FilterSidebar />` bare | `<FilterSidebar />` wrapped in `<div data-html2canvas-ignore>` |
| Records indicator bare | Records indicator carries `data-html2canvas-ignore` |
| "Cross-filter" label + hint | "Filtered by" label (terser, matches the chip-row's new conditional role); the hint span ("click a bar or pie slice...") is removed because the row no longer shows when no filters are active |

### Step 3: Update `handleDownloadAll` to pass footer + dataset-aware filename

- [ ] **Replace `handleDownloadAll` at `frontend/src/components/AutoDashboard.tsx:166-174`** with the version below.

```tsx
  const handleDownloadAll = async () => {
    if (!sectionRef.current) return;
    setBusy("section");
    try {
      const rawName = latest?.raw_query ? latest.raw_query.slice(0, 60) : "analysis";
      const today = new Date().toISOString().split("T")[0];
      const footer = `${rawName} · Generated by AnalystAI · ${today}`;
      const slug =
        rawName
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "") || "analysis";
      const filename = `dashboard_${slug}_${today}`;
      await downloadElementAsPng(sectionRef.current, filename, { footer });
    } finally {
      setBusy(null);
    }
  };
```

- [ ] **Leave `handleDownloadChart` unchanged** — it still calls `downloadElementAsPng(el, title)` with no options, which means no footer rendered for per-chart exports. This is the expected behavior.

### Step 4: Typecheck

- [ ] **Run typecheck:** `cd frontend && npm run build`
- [ ] Expected: build succeeds, no TypeScript errors. (Specifically, the new `options?` arg means both the new call and the existing per-chart call typecheck.)

### Step 5: Manual smoke test

Verify the export against this matrix on a running dev server (`cd frontend && npm run dev`, with backend live):

- [ ] **Scenario A — no filters:** Navigate to `/dashboard` with at least one KPI row + 2+ charts visible. Click the "PNG" button in the header. Open the downloaded file.
  - **Pass criteria:** Image contains KPI row + chart grid + footer line. Image does NOT contain the FilterSidebar, the chip row, "Showing X of Y records", or the page header buttons. Filename matches `dashboard_<slug>_<YYYY-MM-DD>.png`.
- [ ] **Scenario B — one equality filter:** Click a bar or pie slice on a chart to apply a cross-filter. Then click "PNG".
  - **Pass criteria:** Image contains the chip row at top showing the active filter chip(s) **without** the small `X` icon and **without** the Reset button. KPIs + grid + footer present below. No sidebar.
- [ ] **Scenario C — multi-kind filters:** Apply a sidebar slider range AND a cross-filter click. Click "PNG".
  - **Pass criteria:** All active filter chips render in the chip row, no interactive chrome. KPIs reflect filtered values. Charts re-aggregated.
- [ ] **Scenario D — per-chart PNG:** Hover a chart card, click the small "PNG" button. Open the downloaded file.
  - **Pass criteria:** Single chart card image. No footer. Filename = chart title slug.
- [ ] **Scenario E — edit mode + full PNG:** Click "Edit layout", then click "PNG".
  - **Pass criteria:** Edit-mode overlay (move/hide buttons) and the hidden-charts restore panel are absent from the PNG. KPIs + grid + footer present.

If any scenario fails, fix and re-test before commit.

### Step 6: Commit

- [ ] **Stage + commit both files together:**

```bash
git add frontend/src/lib/downloadAsPng.ts frontend/src/components/AutoDashboard.tsx
git commit -m "$(cat <<'EOF'
feat(dashboard): tighten PNG export — visuals only + footer

Gap 3. Restructure sectionRef so FilterSidebar lives outside it; render
the cross-filter chip row conditionally inside the snapshot with X icons
and Reset button marked data-html2canvas-ignore so the PNG sees flat
chip labels. Mark the records indicator as ignored. Extend downloadAsPng
with optional footer painted via offscreen canvas.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- ✅ FilterSidebar excluded — Step 2 wraps it in `data-html2canvas-ignore` AND moves it outside sectionRef.
- ✅ Records indicator excluded — Step 2 marks the `<div>` with `data-html2canvas-ignore`.
- ✅ Chip row conditional + interactive chrome stripped from PNG — Step 2 makes the chip row conditional on `activeFilterCount > 0`, marks X icon span + Reset button as ignored.
- ✅ Footer rendered into PNG — Step 1 implements `paintFooter` via offscreen canvas; Step 3 builds + passes the footer string.
- ✅ Dataset-aware filename — Step 3 derives slug from `latest.raw_query`.
- ✅ Per-chart export unchanged — `handleDownloadChart` left intact; `options` arg is optional.
- ✅ `html-to-image` preserved — Step 1 keeps the same library, only adds canvas post-processing.

**Placeholder scan:** No "TBD", "TODO", or vague steps. Every code block is complete and copy-pasteable.

**Type consistency:** `downloadElementAsPng(element, filename, options?)` — both the new full-dashboard call (`{ footer }`) and the existing per-chart call (no third arg) typecheck against the same signature.

**Verification:** Step 5 covers the matrix from the spec exactly.
