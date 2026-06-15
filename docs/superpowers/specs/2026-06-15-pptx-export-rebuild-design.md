# PowerPoint Export Rebuild — Design

**Date:** 2026-06-15
**Gap:** 4 (PowerPoint Export Quality)
**Status:** Spec approved

---

## Problem

The current PPTX export ([api/lib/report_pptx.py](../../../api/lib/report_pptx.py), 298 lines) produces a deck that is unsuitable for stakeholder presentations:

- Title slide subtitle shows the raw query ("Continue with imputation") — internal context.
- Slide 2 is a schema/dtypes table — metadata, not a finding.
- Executive summary slide is one giant paragraph.
- Technical findings slide carries code backticks and statistical notation (analyst-tab content).
- Recommended actions table has "reasoning" and "impact" columns with duplicated text.
- Chart slides use python-pptx native charts that drop the web dashboard's semantic colors (survival bars all default blue instead of green/red).
- Every slide title has an indigo accent rectangle below it — visual noise that screams "AI-generated".
- Y-axis on percent-type charts doesn't show `%`.
- No KPI slide. No closing slide.

## Goals

The PPTX deck should be presentation-ready for a stakeholder meeting:
- Auto-adapts to any dataset.
- All content sourced from `AnalysisSession`; no hardcoded values except the product GitHub URL.
- 8-9 slides total, in a fixed structure (title → KPIs → exec summary → up to 5 charts → actions → closing).
- Charts use matplotlib so the web dashboard's semantic coloring (and `%` y-axis where relevant) is preserved.
- Typography: Georgia for titles, Calibri for body. No accent rules under titles. Navy background for title + closing; white for content slides.

## Non-goals

- Backward compatibility with the existing deck. The user explicitly asks for a full rebuild; legacy slide functions (`_add_briefing_slide`, `_add_findings_slide`) are deleted.
- Editable PowerPoint native charts. We deliberately switch to matplotlib raster images to preserve semantic colors.
- Frontend changes. The endpoint signature (`GET /dashboard/latest/report.pptx`) and caller (`downloadLatestReportPptx`) stay identical.
- Schema slide (column/dtype/missing %), raw-data preview, methodology, caveats, reproducibility code, principal analyst review. The user explicitly says these belong in the web app, not the deck.
- A `dataset_name` derivation chain — `AnalysisSession.dataset_name` already exists. We use it directly with a `"Dataset"` fallback.

## Design

### 1. Slide structure (8-9 slides, fixed order)

Slide dimensions stay 13.33" × 7.5" (16:9 widescreen). Every slide uses `prs.slide_layouts[6]` (blank). Margins: 0.75" all sides → content area 11.83" × 6.0".

| # | Slide | Background | Content source |
|---|---|---|---|
| 1 | **Title** | Navy `#1E2761` | `session.dataset_name or "Dataset"` + `executive_view.headline` + today |
| 2 | **Key Metrics** | White | `analysis_findings.kpis[:5]` — skipped if empty |
| 3 | **Executive Summary** | White | `executive_view.findings[:5]` → fallback `executive_summary` sentence-split |
| 4–8 | **Charts** (one per slide, max 5) | White | `session.chart_configs[:5]` |
| N+1 | **Recommended Actions** | White | `executive_view.action_items[:4]` → fallback `recommended_actions[:4]` |
| LAST | **Closing** | Navy `#1E2761` | hardcoded "Thank you" + product URL |

### 2. Visual constants (module-level in `report_pptx.py`)

```python
NAVY = RGBColor(0x1E, 0x27, 0x61)         # title + closing background
NEAR_BLACK = RGBColor(0x1A, 0x1A, 0x1A)   # primary text
BODY_GRAY = RGBColor(0x33, 0x33, 0x33)    # body text
CAPTION_GRAY = RGBColor(0x88, 0x88, 0x88) # captions, KPI label, etc.
KPI_BG = RGBColor(0xF4, 0xF4, 0xF2)       # KPI shape fill
ACCENT_PURPLE = RGBColor(0x53, 0x4A, 0xB7) # KPI shape border only
REPO_URL = "github.com/nikhilreddy00/analyst-ai"
TITLE_FONT = "Georgia"
BODY_FONT = "Calibri"
```

### 3. Per-slide spec

**Slide 1 — Title (navy):**
- Background: NAVY fill on the slide background (via `slide.background.fill.solid()`).
- Line 1: `f"{session.dataset_name or 'Dataset'} Analysis"` — 36pt Georgia bold white, centered horizontally, vertically anchored ~3.0" from top.
- Line 2: `executive_view.headline` (empty string if absent) — 18pt Calibri white, centered, ~4.2" from top.
- Line 3 (bottom): `f"AnalystAI · {datetime.now(tz=timezone.utc).strftime('%B %d, %Y')}"` — 12pt Calibri light gray (#aaa) centered, ~6.5" from top.
- **No accent rectangle.**

**Slide 2 — Key Metrics (white, skipped if `kpis` empty):**
- Title: `"Key Metrics"` — 28pt Georgia bold #1A1A1A, left-aligned at margin (0.75", 0.5").
- KPI shapes: up to 5 `MSO_SHAPE.ROUNDED_RECTANGLE` shapes laid out horizontally. Each shape:
  - Width: `(11.83 - 0.3 * (n - 1)) / n` inches (0.3" gap, n = `len(kpis[:5])`).
  - Height: 1.8" inches. Top: 1.8".
  - Fill: KPI_BG. Border: 1px ACCENT_PURPLE.
  - Text content (3 paragraphs in the shape's text_frame, anchored top):
    1. KPI `label` — 11pt Calibri CAPTION_GRAY, centered.
    2. KPI `value` — 36pt Calibri bold NEAR_BLACK, centered.
    3. KPI `context` — 10pt Calibri CAPTION_GRAY, centered. **All KPI contexts render in neutral gray** (per scope decision; our `context` field is free-form and lacks a direction signal).

**Slide 3 — Executive Summary (white):**
- Title: `"Executive Summary"` — 28pt Georgia bold #1A1A1A, left at (0.75", 0.5").
- Content: bullet list (max 5 bullets, each ≤ 2 lines).
  - Bullet source priority: `executive_view.findings[:5]` if non-empty; else split `report.executive_summary` on sentence boundaries (`. ` followed by capital letter or end-of-string), take first 5.
  - Render each bullet as a paragraph in a single text_frame at (0.75", 1.5", 11.83", 5.0").
  - 16pt Calibri BODY_GRAY for bullet text; bullet marker `"•  "` prepended.
  - **Bold the key number(s)** in each bullet by splitting the bullet text on a regex match for numeric tokens (`r"\d+(?:\.\d+)?%?"`) and applying `run.font.bold = True` to those runs. Non-numeric runs stay regular.
  - Paragraph spacing: `space_after = Pt(12)` for breathing room.

**Slide 4–8 — Charts (white, one per chart, max 5):**
- Title: `chart.title` — 24pt Georgia bold #1A1A1A, left at (0.75", 0.5"). **No accent rule.**
- Chart image: `render_chart_to_png(chart)` returns PNG bytes. Inserted via `slide.shapes.add_picture(BytesIO(png_bytes), Inches(1.33), Inches(1.4), width=Inches(10.67))`. Height auto-scales (matplotlib output is 1500×825 = 10.67" × 5.87" at 150dpi; should fit comfortably in the 6.0" content area).
- Takeaway line (only if chart rendered successfully, not a table fallback):
  - Text: `chart.source_finding` (already a string field).
  - 14pt italic Calibri BODY_GRAY (#666), centered, at (0.75", 7.0", 11.83", 0.4").
- **Fallback when `render_chart_to_png` returns None** (composed chart, unsupported type, or render exception): render a small data table inside the chart slide. Suppress the takeaway line.

**Slide N+1 — Recommended Actions (white):**
- Title: `"Recommended Actions"` — 28pt Georgia bold #1A1A1A, left at (0.75", 0.5").
- Item source priority: `executive_view.action_items[:4]` (each has `action` + `expected_impact`); else fallback to `recommended_actions[:4]` (each has `action` + `reasoning`).
- Layout: numbered list, **not** a table. Each item rendered as two paragraphs in a single text_frame at (0.75", 1.5", 11.83", 5.0"):
  - Paragraph 1: `f"{i+1}. {action}"` — 16pt Calibri bold NEAR_BLACK.
  - Paragraph 2: `f"   {rationale}"` — 14pt Calibri BODY_GRAY (#666). Indented via `paragraph.level = 1` or via prepended spaces.
  - `space_after = Pt(14)` after the rationale to create the 0.2" gap between items.

**Slide LAST — Closing (navy):**
- Background: NAVY.
- Line 1: `"Thank you"` — 36pt Georgia bold white, centered, ~3.0" from top.
- Line 2: `"Generated by AnalystAI · Full analyst report available on request"` — 14pt Calibri light gray (#ccc), centered, ~4.2" from top.
- Line 3: `REPO_URL` — 14pt Calibri light gray (#ccc), centered, ~4.7" from top.

### 4. Chart rendering helper

New module: **`api/lib/chart_render.py`** (~180 lines).

Public API:
```python
def render_chart_to_png(config: ChartConfig) -> bytes | None:
    """Render a ChartConfig to PNG via matplotlib.
    Returns PNG bytes, or None if the chart type can't be rendered
    (caller renders a table fallback). Uses config.colors directly
    (already per-bar semantic where applicable in the visualization agent)."""
```

**Output uniform across types:**
- Figure: `plt.subplots(figsize=(10, 5.5), dpi=150)` → 1500×825 raster.
- Save: `fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")`.
- Cleanup: `plt.close(fig)` after every render (matplotlib leaks figures otherwise).
- Spines: top + right hidden.
- Tick label size: 10pt. Axis label size: 11pt. No chart title drawn (the slide title carries it).
- Y-axis `%` formatting when `config.value_format == "percent"`:
  ```python
  ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))
  ```

**Per-type handling:**

| `chart_type` | Render |
|---|---|
| `BAR` | `ax.bar(labels, values, color=colors)` + value labels on top (`%.1f%%` if percent, else `%g`). |
| `HORIZONTAL_BAR` | `ax.barh(labels, values, color=colors)` + value labels at bar end. |
| `LINE` | `ax.plot(xs, ys, color=colors[0] if colors else DEFAULT_COLOR, marker="o", linewidth=2)`. |
| `PIE` | `ax.pie(values, labels=labels, colors=colors, autopct="%1.1f%%")`. |
| `DONUT` | Same as pie + `ax.add_artist(plt.Circle((0,0), 0.55, color="white"))`. |
| `SCATTER` | `ax.scatter(xs, ys, c=colors or DEFAULT_COLOR, alpha=0.7)`. |
| `HISTOGRAM` | Per-y_key series, overlapping `ax.bar(...)` with `alpha=0.7`, legend on. |
| `HEATMAP` | `seaborn.heatmap(matrix, annot=True, fmt=".2f", cmap="RdBu_r", center=0, ax=ax)`. |
| `COMPOSED`, unknown | Return `None`. |

Color resolution helper:
```python
DEFAULT_COLOR = "#534AB7"

def _resolve_colors(config: ChartConfig, n: int) -> list[str]:
    if not config.colors:
        return [DEFAULT_COLOR] * n
    return [config.colors[i % len(config.colors)] for i in range(n)]
```

**Heatmap data shape** — our `ChartConfig.data` for heatmap is wide-form: `[{row_label: "...", col_label_1: 0.5, ...}, ...]`. Helper unpacks into a 2D matrix, row labels = unique `x_key` values, column labels = `y_keys`.

**Histogram data shape** — for target-split histograms: `[{bin_center: 25, female: 12, male: 8}, ...]`. Helper iterates `y_keys`, drawing each as `ax.bar(bin_centers, values, alpha=0.7, color=colors[i], label=y_keys[i])`.

**Failure mode** — if any helper raises (matplotlib choke on weird data), the public `render_chart_to_png` catches and returns `None`. The deck never errors out on a single bad chart.

**matplotlib backend** — backend must be `Agg` (non-interactive). Set at module top before any `pyplot` import:
```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

**Warning suppression** — seaborn triggers DeprecationWarnings on certain pandas combinations. Suppress at module top:
```python
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="seaborn")
```

### 5. Dependencies

Add to `pyproject.toml` under `[project] dependencies`:
- `matplotlib>=3.9`
- `seaborn>=0.13`

Both are lazy-imported **inside** `render_chart_to_png` (not at module load) to keep `chart_render.py` cheap to import. matplotlib's ~300ms startup cost only hits when a user clicks "PowerPoint".

`uv.lock` regenerated via `uv sync`.

### 6. Files touched

| File | Action | LoC (approx.) |
|---|---|---|
| `pyproject.toml` | Add matplotlib + seaborn to dependencies | +2 |
| `uv.lock` | Regenerated | many |
| `api/lib/chart_render.py` | **NEW** — `render_chart_to_png` + per-type helpers | ~180 |
| `api/lib/report_pptx.py` | **REWRITE** — new palette, new slide builders, no accent rule, KPI + closing slides | ~340 |
| `api/routes/dashboard.py` | **NO CHANGE** — same endpoint, same signature | 0 |
| `tests/test_report_pptx.py` | **NEW** — smoke test (5 assertions) | ~80 |

No frontend changes. The dashboard "PowerPoint" button at [AutoDashboard.tsx:241](../../../frontend/src/components/AutoDashboard.tsx#L241) keeps calling `downloadLatestReportPptx()`.

### 7. Risks / open questions

- **`session.dataset_name` may be `None`** → fallback `"Dataset"`. Title reads "Dataset Analysis" which is awkward but won't crash.
- **`executive_view` may be `None`** → fallback chain documented per slide (legacy fields).
- **matplotlib font availability** — chart labels render in matplotlib's default `DejaVu Sans`, not Georgia. Slide titles (rendered via python-pptx) DO use Georgia. This is an intentional asymmetry — we don't try to ship Georgia as a matplotlib font.
- **Seaborn warning noise** — suppressed via `warnings.filterwarnings`.
- **No automated visual tests.** The smoke test asserts structure (slide count, background colors, no legacy accent rectangles), not pixel layout. Visual verification is manual.
- **Out of scope for this gap:** dataset-name backfill (right now upload flow may not set `dataset_name` — that's a separate concern).

## Verification

**Automated:**
1. `uv sync` succeeds with the new deps.
2. `uv run pytest tests/test_report_pptx.py -v` passes (smoke test on fixture session).
3. `uv run pytest -v` shows 25 prior tests + 1 new test = 26 passing, no regressions.

**Manual (user runs):**
4. Run an overview analysis against a CSV in `/chat`.
5. From `/dashboard`, click "PowerPoint".
6. Verify the slide-by-slide pass criteria in the matrix below.

| Slide | Pass criteria |
|---|---|
| 1 — Title | Navy bg; white "{Dataset Name} Analysis" 36pt Georgia bold centered; white headline 18pt; gray "AnalystAI · {date}" 12pt bottom; **no purple accent line**. |
| 2 — Key Metrics | White bg; 28pt left-aligned title; 4-5 light-gray rounded rectangles in a row, each with label/value/context. |
| 3 — Executive Summary | White bg; 3-5 bullets, each ≤2 lines; numbers in bullets are bold. No giant paragraph. |
| 4-8 — Charts | White bg; chart image ~80% slide width centered; **semantic colors preserved**; y-axis shows `%` for percent charts; italic gray takeaway line below. |
| N+1 — Actions | White bg; numbered "1." through "N."; each item is `{action}` bold + `{rationale}` regular; no table. |
| Last — Closing | Navy bg; "Thank you" 36pt white; product line + URL below 14pt gray. |
