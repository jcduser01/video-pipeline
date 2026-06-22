"""Layer stack -> FFmpeg composite command.

Pure string/argument assembly (no subprocess here, so it is unit-testable);
``runner.py`` runs the returned argv — the same split the rough-cut renderer uses.
The composite flattens the base video and any transparent overlay layers (the
caption .mov, future overlays), stacked bottom-to-top by z-order, into a single
previewable .mp4.

This is a **preview/handoff intermediate** written to ``review/`` — NOT the final
cut (that is ``render/``, filled by the editor from their NLE). ``source/`` and
the layer files are read-only inputs; the composite is written to a fresh path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence


def composite_filtergraph(n_overlays: int) -> str:
    """Chain ``overlay`` filters stacking ``n_overlays`` over input 0 (the base).

    Inputs are assumed ordered base, then overlays in low->high z-order, so each
    overlay lands on top of the ones before it. Returns the ``filter_complex``
    string whose final video pad is ``[outv]`` (mapped by the caller). Empty
    string when there are no overlays (the base is mapped directly).
    """
    parts: List[str] = []
    prev = "[0:v]"
    for i in range(1, n_overlays + 1):
        out = "[outv]" if i == n_overlays else f"[ov{i}]"
        # format=auto lets ffmpeg pick yuva/rgba so straight-alpha overlays key
        # cleanly over the base; overlay at 0,0 (layers are full-frame).
        parts.append(f"{prev}[{i}:v]overlay=0:0:format=auto{out}")
        prev = out
    return ";".join(parts)


def ffmpeg_composite_command(
    base_path: str,
    overlay_paths: Sequence[str],
    output_path: str,
    crf: int = 18,
    preset: str = "medium",
    audio_bitrate: str = "192k",
) -> List[str]:
    """Assemble the FFmpeg argv that flattens base + overlays into ``output_path``.

    Overlays are stacked in the given order (low->high z-order) over the base; the
    base's audio is carried through (``0:a?`` — optional, so a silent base still
    renders). With no overlays the base is simply re-encoded (a one-layer
    composite). Re-encodes libx264 + AAC at a quality-biased crf/preset because
    the composite is a deliverable-quality preview, not the rough cut's throwaway.
    """
    if not base_path:
        raise ValueError("composite needs a base video")

    cmd: List[str] = ["ffmpeg", "-y", "-i", base_path]
    for p in overlay_paths:
        cmd += ["-i", p]

    if overlay_paths:
        cmd += [
            "-filter_complex", composite_filtergraph(len(overlay_paths)),
            "-map", "[outv]",
        ]
    else:
        cmd += ["-map", "0:v"]

    cmd += [
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        output_path,
    ]
    return cmd


# ── timed / placed overlays (INI-089 Phase A) ──────────────────────────────────
#
# The caption compositor above stacks full-frame, full-duration transparent layers
# (``overlay=0:0``). The overlay subsystem needs each layer positioned, scaled, and
# gated to a time window, with an optional fade — so it gets its own filtergraph
# builder rather than overloading the caption one. Still pure argv assembly; the
# runner executes it on the daily driver.


@dataclass(frozen=True)
class PlacedOverlay:
    """A resolved overlay ready to composite: an asset plus where/when/how.

    ``path`` is the asset file. ``(x, y, width, height)`` is the destination rect in
    the base frame's pixel space (the overlay is scaled to ``width x height`` and
    placed at ``x, y`` — from the ``overlay.occupancy`` rect). ``start`` / ``end`` are
    the on-screen window in the **composite's** timebase (seconds); the caller
    remaps from source time when handing off a cut. ``fade`` > 0 cross-fades the
    layer's alpha in and out over that many seconds (0 = hard cut). ``loop`` is True
    for a still image (a single-frame input must be looped to persist across the
    window); False for a video/animated asset.
    """

    path: str
    x: int
    y: int
    width: int
    height: int
    start: float
    end: float
    fade: float = 0.0
    loop: bool = False


def _ft(t: float) -> str:
    """Format a time/duration for a filtergraph (trim trailing zeros, keep it short)."""
    return f"{float(t):.3f}".rstrip("0").rstrip(".") or "0"


def timed_overlay_filtergraph(overlays: Sequence[PlacedOverlay]) -> str:
    """``filter_complex`` stacking positioned/timed/scaled overlays on the base.

    Input 0 is the base; overlays are inputs ``1..n`` in low->high z-order. Each
    overlay is scaled to its rect, optionally alpha-faded in/out, then composited at
    its ``(x, y)`` and gated to ``enable='between(t,start,end)'`` so it only shows in
    its window. The final video pad is ``[outv]``. Empty string for no overlays.
    """
    if not overlays:
        return ""
    parts: List[str] = []
    prev = "[0:v]"
    n = len(overlays)
    for i, ov in enumerate(overlays, start=1):
        # Scale the layer to its destination rect. format=yuva420p guarantees an
        # alpha channel so the fade (and straight-alpha keying) behave.
        chain = f"[{i}:v]scale={ov.width}:{ov.height}"
        if ov.fade > 0:
            chain += (
                ",format=yuva420p,"
                f"fade=t=in:st={_ft(ov.start)}:d={_ft(ov.fade)}:alpha=1,"
                f"fade=t=out:st={_ft(ov.end - ov.fade)}:d={_ft(ov.fade)}:alpha=1"
            )
        scaled = f"[ovs{i}]"
        parts.append(chain + scaled)
        out = "[outv]" if i == n else f"[ov{i}]"
        parts.append(
            f"{prev}{scaled}overlay={ov.x}:{ov.y}:"
            f"enable='between(t,{_ft(ov.start)},{_ft(ov.end)})':format=auto{out}"
        )
        prev = out
    return ";".join(parts)


def ffmpeg_timed_composite_command(
    base_path: str,
    overlays: Sequence[PlacedOverlay],
    output_path: str,
    crf: int = 18,
    preset: str = "medium",
    audio_bitrate: str = "192k",
) -> List[str]:
    """FFmpeg argv flattening base + timed/placed overlays into ``output_path``.

    Mirrors :func:`ffmpeg_composite_command` but each overlay is positioned, scaled,
    windowed, and optionally faded (see :func:`timed_overlay_filtergraph`). Still
    images (``loop=True``) get a per-input ``-loop 1`` so they persist across their
    window; the output length still follows the base (the overlay filter ends with
    its main input). With no overlays the base is re-encoded (a one-layer composite).
    Audio handling (duck/mute per overlay) is layered on by the runner/CLI; this
    builder carries the base audio through (``0:a?``).
    """
    if not base_path:
        raise ValueError("composite needs a base video")

    cmd: List[str] = ["ffmpeg", "-y"]
    cmd += ["-i", base_path]
    for ov in overlays:
        if ov.loop:
            cmd += ["-loop", "1"]
        cmd += ["-i", ov.path]

    if overlays:
        cmd += [
            "-filter_complex", timed_overlay_filtergraph(overlays),
            "-map", "[outv]",
        ]
    else:
        cmd += ["-map", "0:v"]

    cmd += [
        "-map", "0:a?",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        output_path,
    ]
    return cmd
