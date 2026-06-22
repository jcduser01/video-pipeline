"""Overlay runner — resolve overlay.def → composite, emit occupancy (INI-089).

The integration layer both phases converge on. It is the bridge from the editable
``overlay.def`` (the product) to the two things the rest of the pipeline needs:

  - the **composite** — each overlay resolved to a :class:`~video_pipeline.composite.
    render.PlacedOverlay` (placement → pixel rect, kind → loop, transition → fade)
    and handed to the timed-overlay ffmpeg argv;
  - the **`overlay.occupancy`** descriptor — written next to the work files so
    caption placement and safe-zone QC stay aware of where/when overlays sit.

The resolution (``resolve_placed_overlays``) is pure and unit-tested; the ffmpeg
invocation is the daily-driver seam (mirrors ``composite/runner.py``), kept thin so
the interesting logic stays testable in the sandbox.

Audio note: image and card overlays carry no audio; the base audio is carried
through (``0:a?``). Per-overlay ``audio=keep|duck|mute`` mixing for *video* overlays
(amix / sidechain ducking) is the next follow-on — until then a video overlay's own
audio is dropped (``mute`` semantics), which is the safe default for an overlay
laid over narration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

from ..composite.render import PlacedOverlay, ffmpeg_timed_composite_command
from .decision import OverlayList
from .occupancy import build_occupancy, occupancy_to_json, resolve_rect

# Kinds whose asset is a single still frame and must be looped to persist across the
# overlay's window. A rendered card tile is a 1-frame ProRes .mov, so it loops too.
_LOOPING_KINDS = ("image", "card")


def resolve_placed_overlays(
    overlays: OverlayList, image_width: int, image_height: int
) -> List[PlacedOverlay]:
    """Resolve each ``overlay.def`` item to a :class:`PlacedOverlay`.

    Geometry comes from the placement (via :func:`occupancy.resolve_rect`), ``loop``
    from the kind (stills/cards loop), the window and fade straight from the item.
    List order is the z-order (later items composite on top). Raises ``ValueError``
    if an item has no ``src`` (nothing to composite).
    """
    placed: List[PlacedOverlay] = []
    for item in overlays.segments:
        if not item.src:
            raise ValueError(
                f"overlay #{item.index} ({item.kind}) has no src — nothing to composite"
            )
        x, y, w, h = resolve_rect(item, image_width, image_height)
        placed.append(
            PlacedOverlay(
                path=item.src,
                x=x,
                y=y,
                width=w,
                height=h,
                start=item.start,
                end=item.end,
                fade=item.fade,
                loop=item.kind in _LOOPING_KINDS,
            )
        )
    return placed


def write_occupancy(
    overlays: OverlayList,
    image_width: int,
    image_height: int,
    path,
    *,
    profile: Optional[str] = None,
) -> str:
    """Write the ``overlay.occupancy`` descriptor JSON. Returns the path."""
    items = build_occupancy(overlays, image_width, image_height)
    text = occupancy_to_json(
        items, profile=profile or overlays.profile,
        image_width=image_width, image_height=image_height,
    )
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return str(p)


def render_overlays(
    base_path: str,
    overlays: OverlayList,
    output_path: str,
    image_width: int,
    image_height: int,
    *,
    crf: int = 18,
    preset: str = "medium",
    occupancy_path: Optional[str] = None,
    dry_run: bool = False,
) -> List[str]:
    """Build, and unless ``dry_run`` run, the timed-overlay composite. Returns argv.

    The occupancy descriptor (if ``occupancy_path`` is given) is always written — it
    is a cheap, pure descriptor the caption/QC steps need, independent of whether the
    expensive ffmpeg render runs. ``dry_run`` skips only the subprocess. ``output_path``
    is a fresh file (``review/composite.mp4``), never the base.
    """
    placed = resolve_placed_overlays(overlays, image_width, image_height)
    cmd = ffmpeg_timed_composite_command(
        base_path, placed, output_path, crf=crf, preset=preset
    )
    if occupancy_path:
        write_occupancy(overlays, image_width, image_height, occupancy_path)
    if dry_run:
        return cmd
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return cmd
