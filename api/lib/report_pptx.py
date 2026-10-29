"""
Gap 4 — Stakeholder-ready PowerPoint export.

Builds an 8-9 slide deck from an AnalysisSession:
  1. Title (navy bg)
  2. Key Metrics (KPI shapes, skipped if no KPIs)
  3. Executive Summary (bullets, numbers bolded)
  4–8. Chart slides (matplotlib raster, max 5)
  N+1. Recommended Actions (numbered list)
  Last. Closing (navy bg)

Charts are rendered via api.lib.chart_render so the web dashboard's
semantic colors carry over. Unsupported chart types fall back to a
data table inside the chart slide.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from io import BytesIO
from collections.abc import Iterable
from typing import Any

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from orchestrator.state import (
    AnalysisSession,
    ChartConfig,
    KPI,
    RecommendedAction,
)

# ── Palette + typography ──────────────────────────────────────────────────────
NAVY = RGBColor(0x1E, 0x27, 0x61)
NEAR_BLACK = RGBColor(0x1A, 0x1A, 0x1A)
BODY_GRAY = RGBColor(0x33, 0x33, 0x33)
CAPTION_GRAY = RGBColor(0x88, 0x88, 0x88)
LIGHT_GRAY = RGBColor(0xCC, 0xCC, 0xCC)
KPI_BG = RGBColor(0xF4, 0xF4, 0xF2)
ACCENT_PURPLE = RGBColor(0x53, 0x4A, 0xB7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

REPO_URL = "github.com/nikhilreddy00/analyst-ai"
TITLE_FONT = "Georgia"
BODY_FONT = "Calibri"

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)
MARGIN = Inches(0.75)
CONTENT_W = Inches(13.33 - 0.75 * 2)  # 11.83"

NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")


# ── Public entry ──────────────────────────────────────────────────────────────

def build_pptx(session: AnalysisSession) -> bytes:
    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    _add_title_slide(prs, session)
    _add_kpi_slide(prs, session)
    _add_summary_slide(prs, session)
    # Summary charts only: Data-tab charts (row counts) describe the table, not the answer.
    for chart in [c for c in session.chart_configs if c.placement != "data"][:5]:
        _add_chart_slide(prs, chart)
    _add_actions_slide(prs, session)
    _add_closing_slide(prs)

    buf = BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ── Slide builders ────────────────────────────────────────────────────────────

def _add_title_slide(prs: Presentation, session: AnalysisSession) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_background(slide, NAVY)

    name = session.dataset_name or "Dataset"
    headline = ""
    if session.insight_report and session.insight_report.executive_view:
        headline = session.insight_report.executive_view.headline or ""

    _textbox(
        slide, left=Inches(0.5), top=Inches(2.9), width=Inches(12.33), height=Inches(1.0),
        text=f"{name} Analysis",
        font_size=Pt(36), bold=True, color=WHITE, font_name=TITLE_FONT,
        align=PP_ALIGN.CENTER,
    )
    if headline:
        _textbox(
            slide, left=Inches(0.5), top=Inches(4.1), width=Inches(12.33), height=Inches(1.0),
            text=headline,
            font_size=Pt(18), color=WHITE, font_name=BODY_FONT,
            align=PP_ALIGN.CENTER,
        )
    date_str = datetime.now(tz=timezone.utc).strftime("%B %d, %Y")
    _textbox(
        slide, left=Inches(0.5), top=Inches(6.5), width=Inches(12.33), height=Inches(0.5),
        text=f"AnalystAI · {date_str}",
        font_size=Pt(12), color=LIGHT_GRAY, font_name=BODY_FONT,
        align=PP_ALIGN.CENTER,
    )


def _add_kpi_slide(prs: Presentation, session: AnalysisSession) -> None:
    kpis = session.analysis_findings.kpis if session.analysis_findings else []
    kpis = (kpis or [])[:5]
    if not kpis:
        return  # Skip slide entirely when no KPIs.

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _slide_title(slide, "Key Metrics")

    n = len(kpis)
    gap = Inches(0.3)
    total_gap = gap * (n - 1) if n > 1 else 0
    shape_w = (CONTENT_W - total_gap) // n
    shape_h = Inches(1.8)
    top = Inches(1.8)

    for i, kpi in enumerate(kpis):
        cur_left = MARGIN + i * (shape_w + gap)
        _add_kpi_shape(slide, kpi, cur_left, top, shape_w, shape_h)


def _add_kpi_shape(slide, kpi: KPI, left, top, width, height) -> None:
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = KPI_BG
    shape.line.color.rgb = ACCENT_PURPLE
    shape.line.width = Pt(1)
    shape.shadow.inherit = False

    tf = shape.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.TOP
    tf.margin_left = Inches(0.15)
    tf.margin_right = Inches(0.15)
    tf.margin_top = Inches(0.18)
    tf.margin_bottom = Inches(0.15)

    p_label = tf.paragraphs[0]
    p_label.alignment = PP_ALIGN.CENTER
    _set_run(p_label.add_run(), kpi.label, size=Pt(11), color=CAPTION_GRAY, font_name=BODY_FONT)

    p_val = tf.add_paragraph()
    p_val.alignment = PP_ALIGN.CENTER
    p_val.space_before = Pt(6)
    _set_run(p_val.add_run(), kpi.value, size=Pt(36), bold=True, color=NEAR_BLACK, font_name=BODY_FONT)

    if kpi.context:
        p_ctx = tf.add_paragraph()
        p_ctx.alignment = PP_ALIGN.CENTER
        p_ctx.space_before = Pt(6)
        _set_run(p_ctx.add_run(), kpi.context, size=Pt(10), color=CAPTION_GRAY, font_name=BODY_FONT)


def _add_summary_slide(prs: Presentation, session: AnalysisSession) -> None:
    bullets = _exec_summary_bullets(session)
    if not bullets:
        return
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _slide_title(slide, "Executive Summary")

    tb = slide.shapes.add_textbox(MARGIN, Inches(1.5), CONTENT_W, Inches(5.0))
    tf = tb.text_frame
    tf.word_wrap = True

    for i, bullet in enumerate(bullets[:5]):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = PP_ALIGN.LEFT
        p.space_after = Pt(12)
        _set_run(p.add_run(), "•  ", size=Pt(16), color=BODY_GRAY, font_name=BODY_FONT)
        for piece, is_num in _split_bold_numbers(bullet):
            _set_run(p.add_run(), piece, size=Pt(16), bold=is_num, color=BODY_GRAY, font_name=BODY_FONT)


def _add_chart_slide(prs: Presentation, chart: ChartConfig) -> None:
    """Render one chart slide. Image preferred, table fallback when unrenderable."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _slide_title(slide, chart.title, size=Pt(24))

    from api.lib.chart_render import render_chart_to_png
    png_bytes = render_chart_to_png(chart)

    if png_bytes:
        slide.shapes.add_picture(
            BytesIO(png_bytes),
            Inches(1.33), Inches(1.4),
            width=Inches(10.67),
        )
        if chart.source_finding:
            _textbox(
                slide, left=MARGIN, top=Inches(7.0), width=CONTENT_W, height=Inches(0.4),
                text=chart.source_finding,
                font_size=Pt(14), italic=True, color=BODY_GRAY, font_name=BODY_FONT,
                align=PP_ALIGN.CENTER,
            )
    else:
        keys = [chart.x_key] + chart.y_keys
        rows = chart.data[:15]
        _add_table(slide, keys, rows, left=MARGIN, top=Inches(1.5), width=CONTENT_W, height=Inches(5.0))


def _add_actions_slide(prs: Presentation, session: AnalysisSession) -> None:
    items = _action_items(session)
    if not items:
        return
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _slide_title(slide, "Recommended Actions")

    tb = slide.shapes.add_textbox(MARGIN, Inches(1.5), CONTENT_W, Inches(5.0))
    tf = tb.text_frame
    tf.word_wrap = True

    for i, (action, rationale) in enumerate(items[:4]):
        p_action = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p_action.alignment = PP_ALIGN.LEFT
        p_action.space_before = Pt(0 if i == 0 else 14)
        _set_run(p_action.add_run(), f"{i + 1}. {action}", size=Pt(16), bold=True, color=NEAR_BLACK, font_name=BODY_FONT)

        if rationale:
            p_rat = tf.add_paragraph()
            p_rat.alignment = PP_ALIGN.LEFT
            p_rat.space_before = Pt(2)
            _set_run(p_rat.add_run(), f"   {rationale}", size=Pt(14), color=BODY_GRAY, font_name=BODY_FONT)


def _add_closing_slide(prs: Presentation) -> None:
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _set_background(slide, NAVY)
    _textbox(
        slide, left=Inches(0.5), top=Inches(3.0), width=Inches(12.33), height=Inches(1.0),
        text="Thank you",
        font_size=Pt(36), bold=True, color=WHITE, font_name=TITLE_FONT,
        align=PP_ALIGN.CENTER,
    )
    _textbox(
        slide, left=Inches(0.5), top=Inches(4.2), width=Inches(12.33), height=Inches(0.5),
        text="Generated by AnalystAI · Full analyst report available on request",
        font_size=Pt(14), color=LIGHT_GRAY, font_name=BODY_FONT,
        align=PP_ALIGN.CENTER,
    )
    _textbox(
        slide, left=Inches(0.5), top=Inches(4.7), width=Inches(12.33), height=Inches(0.5),
        text=REPO_URL,
        font_size=Pt(14), color=LIGHT_GRAY, font_name=BODY_FONT,
        align=PP_ALIGN.CENTER,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _exec_summary_bullets(session: AnalysisSession) -> list[str]:
    """Source priority: executive_view.findings → split executive_summary into sentences."""
    if session.insight_report is None:
        return []
    ev = session.insight_report.executive_view
    if ev and ev.findings:
        return [c.text for c in ev.findings if c.text.strip()][:5]
    summary = session.insight_report.executive_summary or ""
    if not summary:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z])", summary.strip())
    return [p.strip() for p in parts if p.strip()][:5]


def _action_items(session: AnalysisSession) -> list[tuple[str, str]]:
    """Returns (action, rationale) tuples. Source priority:
    executive_view.action_items (action + expected_impact) →
    insight_report.recommended_actions (action + reasoning)."""
    if session.insight_report is None:
        return []
    ev = session.insight_report.executive_view
    if ev and ev.action_items:
        return [(a.action, a.expected_impact) for a in ev.action_items[:4] if a.action]
    legacy: list[RecommendedAction] = session.insight_report.recommended_actions or []
    return [(a.action, a.reasoning) for a in legacy[:4] if a.action]


def _split_bold_numbers(text: str) -> Iterable[tuple[str, bool]]:
    """Yield (piece, is_number) tuples by splitting on NUMBER_RE matches.
    The number tokens get rendered bold; everything else stays regular."""
    pos = 0
    for m in NUMBER_RE.finditer(text):
        if m.start() > pos:
            yield text[pos:m.start()], False
        yield m.group(0), True
        pos = m.end()
    if pos < len(text):
        yield text[pos:], False


def _slide_title(slide, text: str, size=Pt(28)) -> None:
    """Standard slide title. Georgia bold, NEAR_BLACK, left-aligned, top.
    No accent rule (the legacy indigo rectangle is removed in Gap 4)."""
    _textbox(
        slide, left=MARGIN, top=Inches(0.5), width=CONTENT_W, height=Inches(0.8),
        text=text,
        font_size=size, bold=True, color=NEAR_BLACK, font_name=TITLE_FONT,
        align=PP_ALIGN.LEFT,
    )


def _textbox(
    slide, left, top, width, height,
    text: str, font_size, bold: bool = False, italic: bool = False,
    color: RGBColor | None = None, font_name: str = BODY_FONT,
    align=PP_ALIGN.LEFT,
):
    tb = slide.shapes.add_textbox(left, top, width, height)
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    _set_run(p.add_run(), text, size=font_size, bold=bold, italic=italic, color=color, font_name=font_name)
    return tb


def _set_run(run, text: str, *, size, bold: bool = False, italic: bool = False,
             color: RGBColor | None = None, font_name: str = BODY_FONT) -> None:
    run.text = text
    run.font.size = size
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = font_name
    if color is not None:
        run.font.color.rgb = color


def _set_background(slide, color: RGBColor) -> None:
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def _add_table(slide, columns: list[str], rows: list[dict], left, top, width, height):
    """Plain data table used only as a chart-slide fallback."""
    if not rows:
        return None
    n_rows = len(rows) + 1
    n_cols = len(columns)
    table_shape = slide.shapes.add_table(n_rows, n_cols, left, top, width, height)
    tbl = table_shape.table

    for j, col in enumerate(columns):
        cell = tbl.cell(0, j)
        cell.text = str(col)
        for p in cell.text_frame.paragraphs:
            for run in p.runs:
                run.font.bold = True
                run.font.size = Pt(11)
                run.font.name = BODY_FONT

    for i, row in enumerate(rows, start=1):
        for j, col in enumerate(columns):
            cell = tbl.cell(i, j)
            cell.text = _fmt(row.get(col, ""))
            for p in cell.text_frame.paragraphs:
                for run in p.runs:
                    run.font.size = Pt(10)
                    run.font.name = BODY_FONT
    return table_shape


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        if v != v:
            return "—"
        return f"{v:.2f}" if not v.is_integer() else str(int(v))
    return str(v)
