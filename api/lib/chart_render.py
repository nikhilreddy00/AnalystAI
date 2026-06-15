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

# Brand accent — matches the KPI shape border in report_pptx.py.
DEFAULT_COLOR = "#534AB7"

# Quiet seaborn's DeprecationWarning noise on certain pandas combos.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="seaborn")


def render_chart_to_png(config: ChartConfig) -> bytes | None:
    """Render a ChartConfig to PNG bytes via matplotlib.

    Returns None for unsupported chart types or on render exception —
    caller falls back to a data table inside the chart slide. The deck
    never errors out on a single bad chart.
    """
    # Lazy imports: matplotlib has ~300ms cold-start; we don't pay it on
    # routes that never call render_chart_to_png.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: F401  (imported for side effects + downstream use)

    try:
        return _dispatch(config, plt)
    except Exception:
        return None


def _dispatch(config: ChartConfig, plt) -> bytes | None:
    """Type-dispatch. Returns None for unhandled types."""
    # Stubs filled in by Tasks 2 + 3.
    return None


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
    if hasattr(ax, "xaxis"):
        ax.xaxis.label.set_size(11)
    if hasattr(ax, "yaxis"):
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
