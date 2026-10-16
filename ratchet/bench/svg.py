"""Figure 1: mean judge cost per verdict vs detection recall,
one point per configuration, as a standalone accessible SVG.

One series, so one hue (reference palette slot 1) and a direct label on every point instead
of a legend; each point carries a native ``<title>`` tooltip with its exact values; the
figure has ``role="img"``, a ``<title>`` and a ``<desc>`` listing every point (the text
alternative). Light and dark surfaces are both selected (``prefers-color-scheme``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from html import escape

__all__ = ["Point", "cost_vs_recall_svg"]

_W, _H = 640, 400
_L, _R, _T, _B = 72, 32, 72, 56  # plot margins
_CHAR_W = 7.2  # approximate label glyph width at 13px
_OFFSETS = (
    (0.0, -12.0, "middle"),
    (0.0, 22.0, "middle"),
    (12.0, 4.0, "start"),
    (-12.0, 4.0, "end"),
)
_STYLE = """
  .surface { fill: #fcfcfb; }
  .ink { fill: #0b0b0b; font: 13px system-ui, -apple-system, sans-serif; }
  .muted { fill: #52514e; font: 12px system-ui, -apple-system, sans-serif; }
  .grid { stroke: #e4e3df; stroke-width: 1; }
  .axis { stroke: #8a8984; stroke-width: 1; }
  .dot { fill: #2a78d6; stroke: #fcfcfb; stroke-width: 2; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink { fill: #f0efec; }
    .muted { fill: #c3c2b7; }
    .grid { stroke: #33322f; }
    .axis { stroke: #77766f; }
    .dot { fill: #3987e5; stroke: #1a1a19; }
  }
"""


@dataclass(frozen=True)
class Point:
    label: str
    cost_usd: float
    recall: float


def _nice_max(value: float) -> float:
    """The smallest 1/2/2.5/5 × 10^k at or above ``value`` (axis top)."""
    if value <= 0:
        return 1.0
    exponent = math.floor(math.log10(value))
    for step in (1.0, 2.0, 2.5, 5.0, 10.0):
        top = step * 10.0**exponent
        if top >= value * (1 - 1e-12):
            return top
    return 10.0 ** (exponent + 1)


def _box(label: str, lx: float, ly: float, anchor: str) -> tuple[float, float, float, float]:
    width = len(label) * _CHAR_W
    left = {"start": lx, "middle": lx - width / 2, "end": lx - width}[anchor]
    return left, ly - 12, left + width, ly + 3


def _overlaps(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _place(
    label: str, cx: float, cy: float, boxes: list[tuple[float, float, float, float]]
) -> tuple[float, float, str]:
    """A direct-label position that collides with no earlier label and stays on the canvas."""
    fallback = (cx + _OFFSETS[2][0], cy + _OFFSETS[2][1], _OFFSETS[2][2])
    for dx, dy, anchor in _OFFSETS:
        lx, ly = cx + dx, cy + dy
        box = _box(label, lx, ly, anchor)
        inside = box[0] >= 4 and box[2] <= _W - 4 and box[1] >= 44 and box[3] <= _T + (_H - _T - _B)
        if inside and not any(_overlaps(box, other) for other in boxes):
            boxes.append(box)
            return lx, ly, anchor
    boxes.append(_box(label, *fallback))
    return fallback


def _fmt_usd(value: float) -> str:
    return f"${value:.4f}" if value < 0.1 else f"${value:.2f}"


def cost_vs_recall_svg(points: list[Point], *, title: str, subtitle: str = "") -> str:
    pw, ph = _W - _L - _R, _H - _T - _B
    xmax = _nice_max(max((p.cost_usd for p in points), default=0.0) * 1.1)

    def x(v: float) -> float:
        return _L + pw * (v / xmax)

    def y(v: float) -> float:
        return _T + ph * (1 - v)

    desc = "; ".join(
        f"{p.label}: {_fmt_usd(p.cost_usd)} per verdict, recall {p.recall:.2f}" for p in points
    )
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" width="{_W}" '
        f'height="{_H}" role="img" aria-labelledby="fig-title fig-desc">',
        f'<title id="fig-title">{escape(title)}</title>',
        f'<desc id="fig-desc">{escape(subtitle + " " if subtitle else "")}{escape(desc)}</desc>',
        f"<style>{_STYLE}</style>",
        f'<rect class="surface" x="0" y="0" width="{_W}" height="{_H}" rx="6"/>',
        f'<text class="ink" x="{_L}" y="22" font-weight="600">{escape(title)}</text>',
    ]
    if subtitle:
        out.append(f'<text class="muted" x="{_L}" y="38">{escape(subtitle)}</text>')
    for i in range(6):
        v = i / 5
        out.append(f'<line class="grid" x1="{_L}" x2="{_W - _R}" y1="{y(v):.1f}" y2="{y(v):.1f}"/>')
        out.append(
            f'<text class="muted" x="{_L - 8}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.1f}</text>'
        )
        xv = xmax * i / 5
        out.append(
            f'<text class="muted" x="{x(xv):.1f}" y="{_T + ph + 18}" '
            f'text-anchor="middle">{_fmt_usd(xv)}</text>'
        )
    out += [
        f'<line class="axis" x1="{_L}" x2="{_W - _R}" y1="{_T + ph}" y2="{_T + ph}"/>',
        f'<line class="axis" x1="{_L}" x2="{_L}" y1="{_T}" y2="{_T + ph}"/>',
        f'<text class="ink" x="{_L + pw / 2:.1f}" y="{_H - 12}" text-anchor="middle">'
        "Mean judge cost per verdict (USD, Token Factory list prices)</text>",
        f'<text class="ink" transform="translate(18 {_T + ph / 2:.1f}) rotate(-90)" '
        'text-anchor="middle">Detection recall (R1–R6)</text>',
    ]
    boxes: list[tuple[float, float, float, float]] = []
    for p in sorted(points, key=lambda q: (q.cost_usd, q.recall)):
        cx, cy = x(p.cost_usd), y(p.recall)
        lx, ly, anchor = _place(p.label, cx, cy, boxes)
        tip = f"{p.label}: {_fmt_usd(p.cost_usd)} per verdict, recall {p.recall:.2f}"
        out.append(
            f'<g><title>{escape(tip)}</title><circle class="dot" cx="{cx:.1f}" cy="{cy:.1f}" '
            f'r="6"/><text class="ink" x="{lx:.1f}" y="{ly:.1f}" '
            f'text-anchor="{anchor}">{escape(p.label)}</text></g>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"
