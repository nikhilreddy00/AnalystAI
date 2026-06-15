"""
Gap 4 — PNG chart renderer for the PowerPoint export.

Public entry: render_chart_to_png(config) -> bytes | None.

Uses matplotlib (+ seaborn for heatmaps) so the deck preserves the web
dashboard's semantic coloring (e.g., survival bars green/red, not all blue).
matplotlib is forced to the Agg backend so it works headless on the server.
matplotlib + seaborn are imported lazily inside the render entry point so
this module stays cheap to import on non-PPTX cold paths.
"""
from __future__ import annotations

import warnings
from io import BytesIO

from orchestrator.state import ChartConfig, ChartType

# matplotlib package shell is cheap to import (~5ms); pyplot is the expensive
# bit (~300ms), so we set the backend once at module init and defer pyplot to
# the render entry point.
import matplotlib as _mpl
_mpl.use("Agg")

# Brand accent purple.
DEFAULT_COLOR = "#534AB7"

# Quiet seaborn's DeprecationWarning noise on certain pandas combos.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="seaborn")


def render_chart_to_png(config: ChartConfig) -> bytes | None:
    """Render a ChartConfig to PNG bytes via matplotlib.

    Returns None for unsupported chart types or on render exception —
    caller falls back to a data table inside the chart slide. The deck
    never errors out on a single bad chart.
    """
    import matplotlib.pyplot as plt

    try:
        return _dispatch(config, plt)
    except Exception:
        return None


def _dispatch(config: ChartConfig, plt) -> bytes | None:
    """Type-dispatch. Returns None for unhandled types."""
    handlers = {
        ChartType.BAR: _render_bar,
        ChartType.LINE: _render_line,
        ChartType.PIE: _render_pie,
        ChartType.DONUT: _render_donut,
        ChartType.HORIZONTAL_BAR: _render_hbar,
        ChartType.SCATTER: _render_scatter,
        ChartType.HISTOGRAM: _render_histogram,
        ChartType.HEATMAP: _render_heatmap,
    }
    handler = handlers.get(config.chart_type)
    if handler is None:
        return None
    return handler(config, plt)


def _resolve_colors(config: ChartConfig, n: int) -> list[str]:
    """If config.colors is populated, cycle it to length n; else DEFAULT_COLOR repeated."""
    if not config.colors:
        return [DEFAULT_COLOR] * n
    return [config.colors[i % len(config.colors)] for i in range(n)]


def _finalize_axes(ax, value_format: str = "number") -> None:
    """Apply the look shared across all chart types: hidden spines, font sizes,
    and percent-formatted y-axis when applicable."""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(axis="both", labelsize=10)
    ax.xaxis.label.set_size(11)
    ax.yaxis.label.set_size(11)
    if value_format == "percent":
        import matplotlib.pyplot as plt
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))


def _fig_to_png(fig) -> bytes:
    """Save the figure to PNG bytes and close it to free memory."""
    import matplotlib.pyplot as plt
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _safe_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _render_bar(config: ChartConfig, plt) -> bytes | None:
    """Vertical bar chart with value labels on top of each bar."""
    if not config.data or not config.y_keys:
        return None
    x_key = config.x_key
    y_key = config.y_keys[0]
    labels = [str(r.get(x_key, "")) for r in config.data]
    values = [_safe_float(r.get(y_key)) for r in config.data]
    colors = _resolve_colors(config, len(values))

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    bars = ax.bar(labels, values, color=colors)

    fmt = "{:.1f}%" if config.value_format == "percent" else "{:g}"
    for bar, v in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            fmt.format(v),
            ha="center",
            va="bottom",
            fontsize=10,
            color="#333333",
        )

    if config.x_label:
        ax.set_xlabel(config.x_label)
    if config.y_label:
        ax.set_ylabel(config.y_label)
    _finalize_axes(ax, config.value_format)
    return _fig_to_png(fig)


def _render_line(config: ChartConfig, plt) -> bytes | None:
    """Line chart. One line per y_key. Color comes from config.colors[i] or DEFAULT_COLOR."""
    if not config.data or not config.y_keys:
        return None
    xs = [str(r.get(config.x_key, "")) for r in config.data]
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)

    colors = _resolve_colors(config, len(config.y_keys))
    for i, y_key in enumerate(config.y_keys):
        ys = [_safe_float(r.get(y_key)) for r in config.data]
        ax.plot(xs, ys, color=colors[i], marker="o", linewidth=2, label=y_key)

    if config.show_legend and len(config.y_keys) > 1:
        ax.legend(loc="best", fontsize=10, frameon=False)

    if config.x_label:
        ax.set_xlabel(config.x_label)
    if config.y_label:
        ax.set_ylabel(config.y_label)
    _finalize_axes(ax, config.value_format)
    return _fig_to_png(fig)


def _render_pie(config: ChartConfig, plt) -> bytes | None:
    """Pie chart with autopct labels. Uses per-slice colors from config.colors."""
    if not config.data or not config.y_keys:
        return None
    y_key = config.y_keys[0]
    labels = [str(r.get(config.x_key, "")) for r in config.data]
    values = [_safe_float(r.get(y_key)) for r in config.data]
    colors = _resolve_colors(config, len(values))

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    ax.pie(values, labels=labels, colors=colors, autopct="%1.1f%%", startangle=90, textprops={"fontsize": 11})
    ax.set_aspect("equal")
    return _fig_to_png(fig)


def _render_donut(config: ChartConfig, plt) -> bytes | None:
    """Donut chart — same as pie + a white circle in the center."""
    if not config.data or not config.y_keys:
        return None
    y_key = config.y_keys[0]
    labels = [str(r.get(config.x_key, "")) for r in config.data]
    values = [_safe_float(r.get(y_key)) for r in config.data]
    colors = _resolve_colors(config, len(values))

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    ax.pie(values, labels=labels, colors=colors, autopct="%1.1f%%", startangle=90, textprops={"fontsize": 11})
    ax.add_artist(plt.Circle((0, 0), 0.55, color="white"))
    ax.set_aspect("equal")
    return _fig_to_png(fig)


def _render_hbar(config: ChartConfig, plt) -> bytes | None:
    """Horizontal bar chart with value labels at bar ends."""
    if not config.data or not config.y_keys:
        return None
    y_key = config.y_keys[0]
    labels = [str(r.get(config.x_key, "")) for r in config.data]
    values = [_safe_float(r.get(y_key)) for r in config.data]
    colors = _resolve_colors(config, len(values))

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    bars = ax.barh(labels, values, color=colors)
    fmt = "{:.1f}%" if config.value_format == "percent" else "{:g}"
    for bar, v in zip(bars, values):
        ax.text(
            bar.get_width(),
            bar.get_y() + bar.get_height() / 2,
            f" {fmt.format(v)}",
            ha="left",
            va="center",
            fontsize=10,
            color="#333333",
        )
    if config.x_label:
        ax.set_xlabel(config.x_label)
    if config.y_label:
        ax.set_ylabel(config.y_label)
    _finalize_axes(ax, config.value_format)
    # For HBAR, value_format applies to the x-axis (the value direction).
    if config.value_format == "percent":
        ax.yaxis.set_major_formatter(plt.NullFormatter())
        ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:.0f}%"))
    return _fig_to_png(fig)


def _render_scatter(config: ChartConfig, plt) -> bytes | None:
    """Scatter plot with semi-transparent points."""
    if not config.data or not config.y_keys:
        return None
    y_key = config.y_keys[0]
    xs = [_safe_float(r.get(config.x_key)) for r in config.data]
    ys = [_safe_float(r.get(y_key)) for r in config.data]
    colors = _resolve_colors(config, len(xs))

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    ax.scatter(xs, ys, c=colors, alpha=0.7, s=40, edgecolors="none")
    if config.x_label:
        ax.set_xlabel(config.x_label)
    if config.y_label:
        ax.set_ylabel(config.y_label)
    _finalize_axes(ax, config.value_format)
    return _fig_to_png(fig)


def _render_histogram(config: ChartConfig, plt) -> bytes | None:
    """Histogram. When multiple y_keys are present (target-split case),
    draws overlapping bars with alpha=0.7 and a legend."""
    if not config.data or not config.y_keys:
        return None
    xs = [_safe_float(r.get(config.x_key)) for r in config.data]
    colors = _resolve_colors(config, len(config.y_keys))

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    width = (xs[1] - xs[0]) * 0.85 if len(xs) >= 2 else 1.0

    multi = len(config.y_keys) > 1
    for i, y_key in enumerate(config.y_keys):
        ys = [_safe_float(r.get(y_key)) for r in config.data]
        ax.bar(
            xs, ys,
            width=width,
            color=colors[i],
            alpha=0.7 if multi else 1.0,
            label=y_key,
            edgecolor="none",
        )

    if multi and config.show_legend:
        ax.legend(loc="best", fontsize=10, frameon=False)

    if config.x_label:
        ax.set_xlabel(config.x_label)
    if config.y_label:
        ax.set_ylabel(config.y_label)
    _finalize_axes(ax, config.value_format)
    return _fig_to_png(fig)


def _render_heatmap(config: ChartConfig, plt) -> bytes | None:
    """Correlation-style heatmap via seaborn. Data is wide-form:
    [{<x_key>: row_label, col1: val, col2: val, ...}, ...].
    Row labels = x_key values; column labels = config.y_keys."""
    if not config.data or not config.y_keys:
        return None

    import seaborn as sns
    import numpy as np

    row_labels = [str(r.get(config.x_key, "")) for r in config.data]
    col_labels = list(config.y_keys)
    matrix = np.array(
        [[_safe_float(r.get(col)) for col in col_labels] for r in config.data],
        dtype=float,
    )

    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=150)
    sns.heatmap(
        matrix,
        annot=True,
        fmt=".2f",
        cmap="RdBu_r",
        center=0,
        xticklabels=col_labels,
        yticklabels=row_labels,
        ax=ax,
        cbar=True,
        annot_kws={"fontsize": 9},
    )
    plt.setp(ax.get_xticklabels(), rotation=30, ha="right", fontsize=10)
    plt.setp(ax.get_yticklabels(), rotation=0, fontsize=10)
    return _fig_to_png(fig)
