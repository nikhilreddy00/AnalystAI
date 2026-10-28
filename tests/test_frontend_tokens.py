"""Phase 3.5 guard: frontend colours come from the theme tokens in globals.css.

Raw Tailwind palette classes (zinc-, indigo-, …), gradients and white/black fills only
work in one theme, which is how the old UI ended up half light, half dark. Brand marks
that must keep their own colours (the Google logo) are allowed by file.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "frontend" / "src"
PALETTE = (
    "zinc|slate|gray|neutral|stone|indigo|purple|violet|blue|sky|teal|cyan|"
    "emerald|green|lime|amber|yellow|orange|red|rose|pink"
)
RAW = re.compile(
    rf"(?<![\w-])(?:[a-z-]+:)*(?:bg|text|border|ring|from|to|via|fill|stroke|divide|outline|shadow)"
    rf"-(?:{PALETTE})-\d{{2,3}}\b|bg-gradient-to-|(?<![\w-])(?:text|bg)-(?:white|black)\b(?!/)"
)
HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
HEX_ALLOWED = {"app/globals.css", "app/auth/page.tsx"}  # token definitions; Google's logo


def _files():
    for p in SRC.rglob("*"):
        if p.suffix in {".tsx", ".ts", ".css"} and "components/ui/" not in p.as_posix():
            yield p, p.relative_to(SRC).as_posix()


def test_no_raw_palette_classes():
    hits = [f"{rel}:{n}: {m.group(0)}"
            for p, rel in _files()
            for n, line in enumerate(p.read_text().splitlines(), 1)
            for m in RAW.finditer(line)]
    assert not hits, "use theme tokens (bg-card, text-muted-foreground, …):\n" + "\n".join(hits)


def test_no_hex_colours_outside_tokens():
    hits = [f"{rel}:{n}: {m.group(0)}"
            for p, rel in _files() if rel not in HEX_ALLOWED
            for n, line in enumerate(p.read_text().splitlines(), 1)
            for m in HEX.finditer(line)
            if "GENERIC" not in line and not line.lstrip().startswith(("//", "*", '"#'))]
    assert not hits, "define colours as tokens in globals.css:\n" + "\n".join(hits)
