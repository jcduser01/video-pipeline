"""Caption exporters — portable SRT + the Remotion props contract. Pure.

Two outputs, both derived from a :class:`~video_pipeline.captions.cue.CaptionTrack`:

  - **SRT** (``cues_to_srt``) — the universal subtitle interchange. Imports into
    Premiere / Resolve / Final Cut / YouTube as an editable caption track; the
    portable fallback if Remotion styling is skipped for a job. Suppressed cues
    (``keep: false``) are omitted; indices renumber over the kept cues.

  - **Remotion props** (``track_to_remotion_props``) — the **style-layer input
    contract**. A single JSON object the ``remotion/`` project reads to render
    the styled caption overlay: resolved style, the safe-zone caption box (px),
    frame dimensions/fps, and each kept cue with frame-accurate in/out points and
    its emphasis word indices. This is the seam between the (pure, tested) Python
    timing/placement layer and the (Node/React, daily-driver) Remotion renderer.

Only kept cues cross either boundary; ``source/`` is never touched.
"""

from __future__ import annotations

import json
from typing import Optional

from .cue import CaptionTrack
from .placement import CaptionBox, caption_box, caption_box_avoiding
from .style import CaptionStyle


# ── SRT ───────────────────────────────────────────────────────────────────────

def _srt_timestamp(seconds: float) -> str:
    """``HH:MM:SS,mmm`` — SRT's comma-decimal timestamp."""
    if seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000.0))
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def cues_to_srt(track: CaptionTrack, uppercase: bool = False) -> str:
    """Render kept cues to an SRT document (renumbered 1..n)."""
    blocks = []
    for n, cue in enumerate(track.kept(), start=1):
        text = cue.text.upper() if uppercase else cue.text
        blocks.append(
            f"{n}\n"
            f"{_srt_timestamp(cue.start)} --> {_srt_timestamp(cue.end)}\n"
            f"{text}\n"
        )
    return "\n".join(blocks)


# ── Remotion props (style-layer contract) ─────────────────────────────────────

def seconds_to_frame(seconds: float, fps: int) -> int:
    """Frame index for a time (rounded to the nearest frame)."""
    return int(round(seconds * fps))


def _even_split(start: float, end: float, n: int):
    """Split [start, end] into n equal (start, end) sub-intervals."""
    if n <= 0:
        return []
    step = (end - start) / n
    return [(start + i * step, start + (i + 1) * step) for i in range(n)]


def _word_timings_frames(cue, fps: int):
    """Per-word frame windows **relative to the cue start**, for the karaoke
    highlight. Uses the cue's captured per-word timings when their count matches
    its words; otherwise even-splits the cue duration across the words (so a
    hand-edited or hand-added cue still highlights smoothly)."""
    n = len(cue.words)
    if n == 0:
        return []
    times = cue.word_times if len(cue.word_times) == n else _even_split(cue.start, cue.end, n)
    cue_frame = seconds_to_frame(cue.start, fps)
    out = []
    for ws, we in times:
        wf = max(0, seconds_to_frame(ws, fps) - cue_frame)
        wd = max(1, seconds_to_frame(we, fps) - cue_frame - wf)
        out.append({"from": wf, "durationInFrames": wd})
    return out


def track_to_remotion_props(
    track: CaptionTrack,
    style: CaptionStyle,
    box: CaptionBox,
    width: int,
    height: int,
    fps: int = 30,
    karaoke: bool = False,
    cue_boxes: Optional[dict] = None,
) -> dict:
    """Build the Remotion props object for the styled caption overlay.

    Times are converted to frames at ``fps``; ``durationInFrames`` per cue is at
    least 1 so a very short cue still renders. ``box`` (from
    :func:`~video_pipeline.captions.placement.caption_box`) is the track-level
    default placement. ``cue_boxes`` (optional, keyed by cue index) overrides the
    box for individual cues that must dodge an overlay (INI-089 caption-dodge); a
    cue with an entry emits its own ``box`` and the renderer prefers it over
    ``safeBox``. ``karaoke`` adds the top-level flag; ``wordTimings`` (per-word
    frame windows relative to each cue) is always emitted so the renderer can
    highlight the active word when karaoke is on.
    """
    cue_boxes = cue_boxes or {}
    out_cues = []
    for cue in track.kept():
        f_in = seconds_to_frame(cue.start, fps)
        f_out = seconds_to_frame(cue.end, fps)
        out_cue = {
            "index": cue.index,
            "text": cue.text.upper() if style.uppercase else cue.text,
            "words": [w.upper() for w in cue.words] if style.uppercase else list(cue.words),
            "emphasis": list(cue.emphasis),
            "from": f_in,
            "durationInFrames": max(1, f_out - f_in),
            "startSeconds": cue.start,
            "endSeconds": cue.end,
            "wordTimings": _word_timings_frames(cue, fps),
        }
        if cue.index in cue_boxes:
            out_cue["box"] = cue_boxes[cue.index].to_dict()
        out_cues.append(out_cue)

    return {
        # v3 (INI-089 caption-dodge): cues may carry an optional per-cue `box` that
        # overrides safeBox to clear an overlay. v2 (INI-088 Phase 2): style adds
        # the background-plate trio. Older consumers ignore the new keys.
        "schemaVersion": 3,
        "source": track.source,
        "identity": track.identity,
        "profile": track.profile,
        "fps": fps,
        "karaoke": bool(karaoke),
        "dimensions": {"width": width, "height": height},
        "safeBox": box.to_dict(),
        "style": style.to_dict(),
        "cues": out_cues,
    }


def write_remotion_props(props: dict, path) -> None:
    from pathlib import Path

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(props, indent=2) + "\n", encoding="utf-8")


def build_props_from_safezone(
    track: CaptionTrack,
    style: CaptionStyle,
    safezone_spec,
    fps: int = 30,
    position: Optional[str] = None,
    karaoke: Optional[bool] = None,
    h_offset: Optional[str] = None,
    avoid_windows=None,
) -> dict:
    """Convenience: derive the caption box from a safe-zone spec, then build props.

    Frame dimensions come from the spec's template image size (the profile's
    native frame). ``position`` defaults to the style's anchor; ``h_offset``
    defaults to the style's horizontal placement; ``karaoke`` defaults to
    ``style.karaoke``.

    ``avoid_windows`` (INI-089 caption-dodge) is an optional list of
    ``(x, y, w, h, start, end)`` overlay footprints (from
    ``overlay.occupancy.avoid_windows``). When given, each cue whose time window
    overlaps an overlay gets a relocated ``box`` that clears it; cues with no
    overlapping overlay keep the track default. Cheap and per-cue, so captions
    only move where an overlay actually sits.
    """
    pos = position or style.position
    h_off = h_offset or style.h_offset
    box = caption_box(safezone_spec, position=pos, h_offset=h_off)

    cue_boxes = None
    if avoid_windows:
        cue_boxes = {}
        for cue in track.kept():
            rects = [
                (x, y, w, h)
                for (x, y, w, h, s, e) in avoid_windows
                if min(e, cue.end) - max(s, cue.start) > 0
            ]
            if rects:
                cue_boxes[cue.index] = caption_box_avoiding(
                    safezone_spec, rects, position=pos, h_offset=h_off
                )

    return track_to_remotion_props(
        track, style, box,
        width=safezone_spec.image_width,
        height=safezone_spec.image_height,
        fps=fps,
        karaoke=style.karaoke if karaoke is None else karaoke,
        cue_boxes=cue_boxes,
    )
