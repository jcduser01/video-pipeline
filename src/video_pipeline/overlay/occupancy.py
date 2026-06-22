"""overlay.occupancy — the cross-layer descriptor (INI-089 Phase A). Pure.

An overlay occupies a rectangle of the frame for a window of time. The compositor
needs that rect to *place* the overlay; caption placement and safe-zone QC need it
to *stay aware* of it — captions dodge a region an overlay is sitting on, and QC
flags an overlay intruding on the danger zone. Rather than have each branch read
another's pixels (which the pipeline forbids), the overlay step emits this
descriptor and the others consume it (SADD §3.3 occupancy mechanism).

The rect is **geometric**, derived from the placement keyword (or the explicit
PiP rect) — not safe-zone-clipped. That is deliberate: a full-bleed overlay
occupies the whole frame even though captions still draw inside the safe zone on
top of it; the occupancy says "this region is busy from t0 to t1", and the caption
layer decides what to do about it. A matted PiP (Phase C) keeps the geometric PiP
rect either way (the matte changes the pixels, not the footprint).

Coordinate convention matches :class:`~video_pipeline.safezone.spec.SafeZoneSpec`:
integer pixel-edge, origin top-left, x right / y down, half-open ``x0<=x<x1``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .decision import OverlayItem, OverlayList

OCCUPANCY_VERSION = "1.0"


def resolve_rect(item: OverlayItem, image_width: int, image_height: int) -> Tuple[int, int, int, int]:
    """The pixel rect ``(x, y, w, h)`` an overlay occupies in the profile frame.

    * ``full-bleed`` — the whole frame.
    * ``bottom-half`` — the lower half (top edge at the vertical midpoint).
    * ``pip-rect`` — the item's explicit rect, clamped into the frame.

    Geometric only (no safe-zone clipping — see the module docstring).
    """
    w, h = image_width, image_height
    if item.placement == "full-bleed":
        return (0, 0, w, h)
    if item.placement == "bottom-half":
        mid = h // 2
        return (0, mid, w, h - mid)
    if item.placement == "pip-rect":
        if item.rect is None:  # pragma: no cover - decision.py enforces this
            raise ValueError("pip-rect overlay has no rect")
        x, y, rw, rh = item.rect
        # Clamp into the frame so a hand-edited rect can never push the overlay
        # (or its occupancy) off-canvas.
        x = max(0, min(x, w))
        y = max(0, min(y, h))
        rw = max(1, min(rw, w - x))
        rh = max(1, min(rh, h - y))
        return (x, y, rw, rh)
    raise ValueError(f"unknown placement {item.placement!r}")  # pragma: no cover


@dataclass(frozen=True)
class OccupancyItem:
    """One overlay's footprint: a rect held over a source-time window."""

    index: int
    kind: str
    placement: str
    start: float
    end: float
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

    def covers(self, t: float) -> bool:
        return self.start <= t < self.end

    def intersects_rect(self, x0: float, y0: float, x1: float, y1: float) -> bool:
        """True if this footprint overlaps rectangle ``[x0,x1) x [y0,y1)``."""
        return (
            min(self.x1, x1) - max(self.x, x0) > 0
            and min(self.y1, y1) - max(self.y, y0) > 0
        )

    def to_dict(self) -> dict:
        return {
            "i": self.index,
            "kind": self.kind,
            "placement": self.placement,
            "start": self.start,
            "end": self.end,
            "rect": {"x": self.x, "y": self.y, "w": self.width, "h": self.height},
        }


def build_occupancy(
    overlays: OverlayList, image_width: int, image_height: int
) -> List[OccupancyItem]:
    """Resolve every overlay in ``overlays`` to an :class:`OccupancyItem`."""
    out: List[OccupancyItem] = []
    for item in overlays.segments:
        x, y, w, h = resolve_rect(item, image_width, image_height)
        out.append(
            OccupancyItem(
                index=item.index,
                kind=item.kind,
                placement=item.placement,
                start=item.start,
                end=item.end,
                x=x,
                y=y,
                width=w,
                height=h,
            )
        )
    return out


def active_at(items: List[OccupancyItem], t: float) -> List[OccupancyItem]:
    """The footprints on screen at source time ``t``."""
    return [it for it in items if it.covers(t)]


def rects_active_in_window(
    items: List[OccupancyItem], start: float, end: float
) -> List[Tuple[int, int, int, int]]:
    """The ``(x, y, w, h)`` rects whose window overlaps ``[start, end)``.

    What a caption cue spanning ``[start, end)`` must dodge: every overlay on screen
    during any part of the cue. Used to feed
    :func:`~video_pipeline.captions.placement.caption_box_avoiding`.
    """
    out: List[Tuple[int, int, int, int]] = []
    for it in items:
        if min(it.end, end) - max(it.start, start) > 0:
            out.append((it.x, it.y, it.width, it.height))
    return out


def avoid_windows(
    items: List[OccupancyItem],
) -> List[Tuple[int, int, int, int, float, float]]:
    """Flatten footprints to ``(x, y, w, h, start, end)`` tuples.

    Plain data the caption export consumes without importing the overlay package —
    keeps the consumer (captions) decoupled from the producer's types.
    """
    return [(it.x, it.y, it.width, it.height, it.start, it.end) for it in items]


def occupancy_to_dict(
    items: List[OccupancyItem],
    *,
    profile: Optional[str],
    image_width: int,
    image_height: int,
) -> dict:
    """The serializable ``overlay.occupancy`` descriptor caption/QC consume."""
    return {
        "occupancy_version": OCCUPANCY_VERSION,
        "profile": profile,
        "image": {"width": image_width, "height": image_height},
        "items": [it.to_dict() for it in items],
        "coordinate_convention": (
            "integer pixel-edge; origin top-left; x right, y down; "
            "rects are half-open x0<=x<x1, y0<=y<y1; start/end are source-time seconds"
        ),
    }


def occupancy_to_json(
    items: List[OccupancyItem],
    *,
    profile: Optional[str],
    image_width: int,
    image_height: int,
) -> str:
    return (
        json.dumps(
            occupancy_to_dict(
                items, profile=profile, image_width=image_width, image_height=image_height
            ),
            indent=2,
            sort_keys=False,
        )
        + "\n"
    )
