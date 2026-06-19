"""Caption placement — a safe-zone-aware caption box. Pure, unit-tested.

The style layer needs to know *where* on the frame captions may sit. Instagram's
safe zone is a polygon with a lower-right notch (the action buttons), so a naive
"bottom band" would collide with it. This module derives a rectangle that is
**guaranteed fully inside** the safe region (``spec.rect_clear`` holds), placed at
the requested vertical anchor.

The returned :class:`CaptionBox` is in the safe-zone spec's pixel space (the
profile's native frame, e.g. 1080×1920). Remotion positions the caption block
inside this box. Because Reels notch the *lower-right*, a ``lower-third`` caption
box is automatically narrowed/shifted to clear the notch — captions never land
under the buttons.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

from ..safezone.spec import SafeZoneSpec

# Fraction of the safe-zone height each vertical third occupies for the anchor.
_ANCHOR_BANDS = {
    "upper-third": (0.06, 0.34),
    "center": (0.36, 0.64),
    "lower-third": (0.66, 0.94),
}


@dataclass(frozen=True)
class CaptionBox:
    """A rectangle (pixel-edge, half-open) the caption block must fit within."""

    x: int
    y: int
    width: int
    height: int

    @property
    def x1(self) -> int:
        return self.x + self.width

    @property
    def y1(self) -> int:
        return self.y + self.height

    @property
    def cx(self) -> float:
        return self.x + self.width / 2.0

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}


def _safe_span_at_row(spec: SafeZoneSpec, y: int) -> Tuple[int, int]:
    """Widest contiguous safe x-span [x0, x1) at integer row ``y`` (or (0,0))."""
    best: Tuple[int, int] = (0, 0)
    for b in spec.bands:
        if b.y0 <= y < b.y1:
            if (b.x1 - b.x0) > (best[1] - best[0]):
                best = (b.x0, b.x1)
    return best


def _intersect_span(rows: List[int], spec: SafeZoneSpec) -> Tuple[int, int]:
    """Intersection of the safe x-spans across every row in ``rows``.

    A box spanning these rows is clear iff its x-range lies within this
    intersection — so this is the widest notch-free width available in the band.
    """
    x0 = 0
    x1 = spec.image_width
    for y in rows:
        s0, s1 = _safe_span_at_row(spec, y)
        x0 = max(x0, s0)
        x1 = min(x1, s1)
        if x0 >= x1:
            return (0, 0)
    return (x0, x1)


def caption_box(
    spec: SafeZoneSpec,
    position: str = "lower-third",
    margin_frac: float = 0.04,
) -> CaptionBox:
    """Derive a caption box at ``position``, guaranteed inside the safe region.

    ``margin_frac`` insets the box horizontally from the available safe width
    (breathing room from the polygon edge). The vertical band is the requested
    third of the safe-zone bounding box; the horizontal extent is the notch-free
    intersection across that band's rows, so a lower-third box clears the
    action-button notch automatically.
    """
    if position not in _ANCHOR_BANDS:
        raise ValueError(f"position {position!r} not in {tuple(_ANCHOR_BANDS)}")

    bx0, by0, bx1, by1 = spec.bounding_box
    safe_h = by1 - by0
    lo_frac, hi_frac = _ANCHOR_BANDS[position]
    y = int(round(by0 + lo_frac * safe_h))
    y1 = int(round(by0 + hi_frac * safe_h))
    y1 = max(y + 1, y1)

    rows = list(range(y, y1))
    sx0, sx1 = _intersect_span(rows, spec)
    if sx1 <= sx0:
        # Degenerate (heavily notched band): fall back to the bbox width inset.
        sx0, sx1 = bx0, bx1

    margin = int(round((sx1 - sx0) * margin_frac))
    x = sx0 + margin
    x1 = sx1 - margin
    if x1 <= x:
        x, x1 = sx0, sx1

    box = CaptionBox(x=x, y=y, width=x1 - x, height=y1 - y)
    return box
