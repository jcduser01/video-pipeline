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


_H_OFFSETS = ("clear-notch", "center")


def caption_box(
    spec: SafeZoneSpec,
    position: str = "lower-third",
    margin_frac: float = 0.04,
    h_offset: str = "clear-notch",
) -> CaptionBox:
    """Derive a caption box at ``position``, guaranteed inside the safe region.

    ``margin_frac`` insets the box horizontally from the available safe width
    (breathing room from the polygon edge). The vertical band is the requested
    third of the safe-zone bounding box; the horizontal extent is the notch-free
    intersection across that band's rows, so a lower-third box clears the
    action-button notch automatically.

    ``h_offset`` (INI-088 Phase 3) chooses how the box sits horizontally when the
    notch shrinks the available span:

    * ``"clear-notch"`` (default) — use the full notch-free span. At lower-third
      this is wider but biased toward the non-notched (left) side.
    * ``"center"`` — keep the box symmetric about the safe-area center, narrowing
      both sides equally so the block stays frame-centered while still clearing
      the notch. Identical to ``clear-notch`` for bands the notch doesn't touch.
    """
    if position not in _ANCHOR_BANDS:
        raise ValueError(f"position {position!r} not in {tuple(_ANCHOR_BANDS)}")
    if h_offset not in _H_OFFSETS:
        raise ValueError(f"h_offset {h_offset!r} not in {_H_OFFSETS}")

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

    if h_offset == "center":
        # Symmetric about the safe-area center: the largest centered span that
        # still fits inside the notch-free [sx0, sx1].
        cx = (bx0 + bx1) / 2.0
        half = min(cx - sx0, sx1 - cx)
        if half > 0:
            sx0, sx1 = int(round(cx - half)), int(round(cx + half))

    margin = int(round((sx1 - sx0) * margin_frac))
    x = sx0 + margin
    x1 = sx1 - margin
    if x1 <= x:
        x, x1 = sx0, sx1

    box = CaptionBox(x=x, y=y, width=x1 - x, height=y1 - y)
    return box


# ── overlay-aware placement (INI-089 caption-dodge) ────────────────────────────
#
# An overlay sitting on the frame is a region captions should avoid for the span
# it is on screen. The overlay step emits ``overlay.occupancy`` (geometric rects +
# windows); the caption layer consumes it here. Per cue, the caller passes the
# overlay rects active during that cue's window and gets a box clear of them.

# Where to look for a clear band when the requested anchor is blocked, in order of
# preference (keep the caption as close to its intended position as possible).
_DODGE_PREFERENCE = {
    "lower-third": ("lower-third", "upper-third", "center"),
    "upper-third": ("upper-third", "lower-third", "center"),
    "center": ("center", "upper-third", "lower-third"),
}


def _box_hits_rect(box: CaptionBox, x: int, y: int, w: int, h: int) -> bool:
    """True if ``box`` overlaps the rect ``(x, y, w, h)`` (positive area)."""
    return box.x < x + w and x < box.x1 and box.y < y + h and y < box.y1


def caption_box_avoiding(
    spec: SafeZoneSpec,
    avoid_rects,
    position: str = "lower-third",
    margin_frac: float = 0.04,
    h_offset: str = "clear-notch",
) -> CaptionBox:
    """A caption box at (or near) ``position`` that clears the overlay rects.

    Tries the requested anchor first; if the box there overlaps any rect in
    ``avoid_rects`` (each ``(x, y, w, h)`` in profile pixel space — e.g. an
    ``overlay.occupancy`` rect active during the cue), it relocates to the next
    preferred anchor (lower → upper → center, etc.) until it finds a clear band.

    Captions over an overlay are an advisory QC concern, not a hard failure
    (consistent with the safe-zone QC philosophy), so if **every** anchor is
    blocked — e.g. a full-bleed overlay covers the frame — it returns the box at
    the requested position as a best effort (QC will flag the overlap).
    """
    rects = [tuple(int(v) for v in r[:4]) for r in avoid_rects]
    for pos in _DODGE_PREFERENCE.get(position, (position,)):
        box = caption_box(spec, position=pos, margin_frac=margin_frac, h_offset=h_offset)
        if not any(_box_hits_rect(box, *r) for r in rects):
            return box
    return caption_box(spec, position=position, margin_frac=margin_frac, h_offset=h_offset)
