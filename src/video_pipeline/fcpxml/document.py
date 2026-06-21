"""FCPXML 1.10 document builder + the assemble orchestrator. Pure.

Emits an FCPXML that opens in Premiere Pro (primary), DaVinci Resolve (free —
full FCPXML import confirmed), or Final Cut Pro. The document is deliberately
conservative: a single project format, whole-asset ``asset-clip`` references, and
rational frame-exact times (``frameDuration = 1/fps``, every time an integer
multiple of it), so it imports cleanly across editors rather than relying on
editor-specific niceties.

Layout (two labeled tracks):

  - **Base Cut** — the decision file's KEEP segments laid end-to-end on the
    ``<spine>``, each a separate ``asset-clip`` referencing the **reframed**
    vertical clip (the reframe is baked in — the CEO already accepted it on real
    footage — so no lossy FCPXML transform). Separate clips = the editor can drop
    a transition between cuts.
  - **Captions** — the styled Remotion overlay as one connected ``asset-clip``
    (``lane="1"``, custom role ``Captions``) spanning the cut, anchored to the
    first base clip. The overlay must be rendered from the **cut-time** caption
    track (see :func:`~video_pipeline.fcpxml.timeline.remap_track`) so it lines up
    with the compressed timeline.

The audio rides on the base-cut clips (``audioRole="dialogue"``); music and the
final mix are the editor's last mile.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.request import pathname2url

from ..captions.cue import CaptionTrack
from ..roughcut.decision import DecisionList
from .timeline import BaseClip, build_base_cut, remap_track, to_frames

FCPXML_VERSION = "1.10"


# ── rational frame time ───────────────────────────────────────────────────────

def frame_duration_str(fps: int) -> str:
    """The ``frameDuration`` for ``fps`` as an FCPXML rational (``1/fps s``)."""
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps!r}")
    return f"1/{fps}s"


def time_str(seconds: float, fps: int) -> str:
    """An FCPXML time: ``seconds`` snapped to the frame grid, as ``frames/fps s``.

    ``0`` renders as ``0s`` (FCPXML's canonical zero). Every other value is an
    exact integer multiple of the frame duration, so ``value / frameDuration`` is
    a whole number of frames in any importer.
    """
    frames = to_frames(seconds, fps)
    if frames == 0:
        return "0s"
    return f"{frames}/{fps}s"


def file_uri(path: str) -> str:
    """Absolute ``file://`` URI for a media path (kept as-is if already a URI)."""
    s = str(path)
    if "://" in s:
        return s
    abspath = os.path.abspath(os.path.expanduser(s))
    return "file://" + pathname2url(abspath)


# ── assets ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Asset:
    """A media asset referenced by the timeline (the reframed clip or overlay)."""

    id: str
    name: str
    src: str
    duration: float
    has_audio: bool = True
    audio_channels: int = 2
    audio_rate: int = 48000


def _format_name(width: int, height: int, fps: int) -> str:
    return f"FFVideoFormat{width}x{height}p{fps}"


# ── document builder ──────────────────────────────────────────────────────────

def build_document(
    base_clips: List[BaseClip],
    *,
    reframed: Asset,
    overlay: Optional[Asset],
    composite: Optional[Asset] = None,
    width: int,
    height: int,
    fps: int,
    event_name: str,
    project_name: str,
) -> str:
    """Assemble the FCPXML string from pre-computed base clips + assets.

    ``overlay`` (the cut-time caption layer) is optional; when absent the document
    is base-cut only. ``composite`` (the flattened all-layers render) is optional;
    when present it rides the top lane as a **disabled guide clip** (SADD §7) — off
    by default so it never overrides the editable stack; the editor toggles it on
    to compare against the assembled reference. Returns a complete UTF-8 FCPXML
    document (declaration + DOCTYPE + tree).
    """
    if not base_clips:
        raise ValueError("no base-cut clips to assemble")

    total = round(base_clips[-1].offset + base_clips[-1].duration, 6)
    fdur = frame_duration_str(fps)

    fcpxml = ET.Element("fcpxml", {"version": FCPXML_VERSION})
    resources = ET.SubElement(fcpxml, "resources")
    ET.SubElement(
        resources,
        "format",
        {
            "id": "r1",
            "name": _format_name(width, height, fps),
            "frameDuration": fdur,
            "width": str(width),
            "height": str(height),
            "colorSpace": "1-1-1 (Rec. 709)",
        },
    )

    def _asset_el(asset: Asset) -> None:
        attrs = {
            "id": asset.id,
            "name": asset.name,
            "start": "0s",
            "duration": time_str(asset.duration, fps),
            "hasVideo": "1",
            "format": "r1",
            "videoSources": "1",
        }
        if asset.has_audio:
            attrs.update(
                {
                    "hasAudio": "1",
                    "audioSources": "1",
                    "audioChannels": str(asset.audio_channels),
                    "audioRate": str(asset.audio_rate),
                }
            )
        el = ET.SubElement(resources, "asset", attrs)
        ET.SubElement(
            el, "media-rep", {"kind": "original-media", "src": file_uri(asset.src)}
        )

    _asset_el(reframed)
    if overlay is not None:
        _asset_el(overlay)
    if composite is not None:
        _asset_el(composite)

    library = ET.SubElement(fcpxml, "library")
    event = ET.SubElement(library, "event", {"name": event_name})
    project = ET.SubElement(event, "project", {"name": project_name})
    sequence = ET.SubElement(
        project,
        "sequence",
        {
            "format": "r1",
            "duration": time_str(total, fps),
            "tcStart": "0s",
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(sequence, "spine")

    first_el = None
    for n, clip in enumerate(base_clips, start=1):
        el = ET.SubElement(
            spine,
            "asset-clip",
            {
                "ref": reframed.id,
                "offset": time_str(clip.offset, fps),
                "name": f"cut {n}",
                "start": time_str(clip.source_in, fps),
                "duration": time_str(clip.duration, fps),
                "tcFormat": "NDF",
                "audioRole": "dialogue",
            },
        )
        if first_el is None:
            first_el = el

    # Captions: one connected clip on lane 1, anchored to the first base clip.
    # Its offset is in the parent clip's local time (origin = the parent's
    # ``start``), so offset == first clip's source_in aligns the overlay head with
    # the sequence head; it spans the whole cut.
    if overlay is not None and first_el is not None:
        ET.SubElement(
            first_el,
            "asset-clip",
            {
                "ref": overlay.id,
                "lane": "1",
                "offset": time_str(base_clips[0].source_in, fps),
                "name": "Captions",
                "start": "0s",
                "duration": time_str(total, fps),
                "role": "Captions",
            },
        )

    # Composite guide clip: the flattened render on the top lane, disabled by
    # default (SADD §7). A reference the editor can toggle on to compare; it never
    # contributes to the active edit.
    if composite is not None and first_el is not None:
        ET.SubElement(
            first_el,
            "asset-clip",
            {
                "ref": composite.id,
                "lane": "2" if overlay is not None else "1",
                "offset": time_str(base_clips[0].source_in, fps),
                "name": "Composite (guide)",
                "start": "0s",
                "duration": time_str(total, fps),
                "enabled": "0",
                "role": "Composite",
            },
        )

    body = ET.tostring(fcpxml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n{body}\n'


# ── orchestrator (pure) ───────────────────────────────────────────────────────

def assemble_fcpxml(
    decision: DecisionList,
    caption_track: Optional[CaptionTrack] = None,
    *,
    reframed_src: str,
    overlay_src: Optional[str] = None,
    composite_src: Optional[str] = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    event_name: str = "JasonOS",
    project_name: Optional[str] = None,
    reframed_name: Optional[str] = None,
) -> Tuple[str, Optional[CaptionTrack]]:
    """Build the FCPXML and the cut-time caption track in one pure call.

    Lays the decision file's KEEP segments end-to-end over ``reframed_src``; if a
    ``caption_track`` and ``overlay_src`` are given, remaps the cues to cut time
    (so the overlay aligns with the compressed timeline) and references the
    overlay as the Captions track. Returns ``(xml, cut_caption_track)`` — the
    cut-time track is what the caller renders to the overlay at ``overlay_src``;
    it is ``None`` when no captions were supplied.
    """
    base_clips = build_base_cut(decision, fps)
    total = round(base_clips[-1].offset + base_clips[-1].duration, 6)

    reframed = Asset(
        id="r2",
        name=reframed_name or Path(str(reframed_src)).stem or "cut",
        src=reframed_src,
        duration=decision.source_duration(),
        has_audio=True,
    )

    cut_track: Optional[CaptionTrack] = None
    overlay: Optional[Asset] = None
    if caption_track is not None:
        cut_track = remap_track(caption_track, decision, fps)
        if overlay_src is not None and cut_track.kept():
            overlay = Asset(
                id="r3",
                name="captions",
                src=overlay_src,
                duration=total,
                has_audio=False,
            )

    composite: Optional[Asset] = None
    if composite_src is not None:
        composite = Asset(
            id="r4", name="composite", src=composite_src,
            duration=total, has_audio=False,
        )

    xml = build_document(
        base_clips,
        reframed=reframed,
        overlay=overlay,
        composite=composite,
        width=width,
        height=height,
        fps=fps,
        event_name=event_name,
        project_name=project_name or (decision.source or "video-pipeline cut"),
    )
    return xml, cut_track
