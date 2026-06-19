"""XMEML (Final Cut Pro 7 XML) builder — the Premiere-compatible handoff. Pure.

Adobe Premiere Pro does **not** import FCPXML; it imports the older **Final Cut
Pro 7 XML** interchange (``<!DOCTYPE xmeml>``, aka XMEML), which DaVinci Resolve
and Final Cut also read. This serializer is the project's default editor handoff,
so a reel opens straight in Premiere with no FCPXML→XML round-trip through
Resolve.

It consumes the same format-agnostic timeline as the FCPXML builder
(:mod:`~video_pipeline.fcpxml.timeline`): the base cut (KEEP segments over the
reframed clip) and the cut-time caption track. Two differences from FCPXML:

  - **Times are integer frames**, not rational seconds. Every clip's ``start`` /
    ``end`` (timeline) and ``in`` / ``out`` (source) are frame counts at the
    sequence ``timebase``.
  - **A/V sync is explicit.** Each base segment becomes a video clipitem on V1
    plus one audio clipitem per channel on A1/A2, tied together with ``<link>``
    blocks so they move as a unit. The caption overlay rides V2 with
    ``<alphatype>straight</alphatype>`` so Premiere keys its transparency.

Layout: **V1** base cut, **V2** captions (higher track = on top); **A1/A2** the
base cut's stereo audio. Music and the final mix stay with the editor.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple

from ..captions.cue import CaptionTrack
from ..roughcut.decision import DecisionList
from .document import Asset, file_uri
from .timeline import build_base_cut, remap_track, to_frames

XMEML_VERSION = "5"


# ── small builders ────────────────────────────────────────────────────────────

def _text(parent: ET.Element, tag: str, value) -> ET.Element:
    el = ET.SubElement(parent, tag)
    el.text = str(value)
    return el


def _rate(parent: ET.Element, fps: int) -> None:
    """``<rate><timebase>fps</timebase><ntsc>FALSE</ntsc></rate>`` — integer fps."""
    r = ET.SubElement(parent, "rate")
    _text(r, "timebase", fps)
    _text(r, "ntsc", "FALSE")


def _pathurl(src: str) -> str:
    """FCP7's ``file://localhost/<abs path>`` URL (vs FCPXML's ``file://``)."""
    uri = file_uri(src)  # file:///<percent-encoded abs path>
    return uri.replace("file://", "file://localhost", 1)


def _file_element(
    parent: ET.Element,
    *,
    file_id: str,
    asset: Asset,
    duration_frames: int,
    fps: int,
    width: int,
    height: int,
    define: bool,
) -> None:
    """A ``<file>`` — fully defined on first use, an empty id-ref afterward.

    FCP7 XML defines a media file once (name, pathurl, media characteristics) and
    references it by id on every later clipitem. ``define=False`` emits the bare
    ``<file id="..."/>`` back-reference.
    """
    f = ET.SubElement(parent, "file", {"id": file_id})
    if not define:
        return
    _text(f, "name", asset.name)
    _text(f, "pathurl", _pathurl(asset.src))
    _rate(f, fps)
    _text(f, "duration", duration_frames)
    media = ET.SubElement(f, "media")
    video = ET.SubElement(media, "video")
    _text(video, "duration", duration_frames)
    sc = ET.SubElement(video, "samplecharacteristics")
    _text(sc, "width", width)
    _text(sc, "height", height)
    if asset.has_audio:
        audio = ET.SubElement(media, "audio")
        asc = ET.SubElement(audio, "samplecharacteristics")
        _text(asc, "samplerate", asset.audio_rate)
        _text(asc, "depth", 16)
        _text(audio, "channelcount", asset.audio_channels)


def _link(parent: ET.Element, ref: str, mediatype: str, trackindex: int, clipindex: int) -> None:
    lk = ET.SubElement(parent, "link")
    _text(lk, "linkclipref", ref)
    _text(lk, "mediatype", mediatype)
    _text(lk, "trackindex", trackindex)
    _text(lk, "clipindex", clipindex)
    _text(lk, "groupindex", 1)


# ── document ──────────────────────────────────────────────────────────────────

def build_document(
    base_clips,
    *,
    reframed: Asset,
    overlay: Optional[Asset],
    width: int,
    height: int,
    fps: int,
    sequence_name: str,
) -> str:
    """Assemble the XMEML string from base clips + assets (pure)."""
    if not base_clips:
        raise ValueError("no base-cut clips to assemble")

    # Frame-exact, contiguous timeline: accumulate clip lengths in whole frames so
    # the base cut has no sub-frame gaps regardless of rounding.
    src_dur_f = to_frames(reframed.duration, fps)
    placed: List[dict] = []
    cursor = 0
    for i, clip in enumerate(base_clips):
        in_f = to_frames(clip.source_in, fps)
        out_f = to_frames(clip.source_out, fps)
        length = max(1, out_f - in_f)
        placed.append(
            {"g": i + 1, "in": in_f, "out": in_f + length,
             "start": cursor, "end": cursor + length}
        )
        cursor += length
    total_f = cursor

    xmeml = ET.Element("xmeml", {"version": XMEML_VERSION})
    sequence = ET.SubElement(xmeml, "sequence", {"id": sequence_name})
    _text(sequence, "name", sequence_name)
    _text(sequence, "duration", total_f)
    _rate(sequence, fps)
    _text(sequence, "in", -1)
    _text(sequence, "out", -1)

    media = ET.SubElement(sequence, "media")

    # ── video ──
    video = ET.SubElement(media, "video")
    vformat = ET.SubElement(video, "format")
    vsc = ET.SubElement(vformat, "samplecharacteristics")
    _rate(vsc, fps)
    _text(vsc, "width", width)
    _text(vsc, "height", height)
    _text(vsc, "anamorphic", "FALSE")
    _text(vsc, "pixelaspectratio", "square")
    _text(vsc, "fielddominance", "none")

    # V1 — base cut
    v1 = ET.SubElement(video, "track")
    for k, p in enumerate(placed):
        clip = base_clips[k]
        ci = ET.SubElement(v1, "clipitem", {"id": f"clipitem-v-{p['g']}"})
        _text(ci, "name", reframed.name)
        _text(ci, "enabled", "TRUE")
        _text(ci, "duration", src_dur_f)
        _rate(ci, fps)
        _text(ci, "start", p["start"])
        _text(ci, "end", p["end"])
        _text(ci, "in", p["in"])
        _text(ci, "out", p["out"])
        _text(ci, "alphatype", "none")
        _file_element(
            ci, file_id="file-1", asset=reframed, duration_frames=src_dur_f,
            fps=fps, width=width, height=height, define=(k == 0),
        )
        st = ET.SubElement(ci, "sourcetrack")
        _text(st, "mediatype", "video")
        # link the video clip to its two audio channels (move as a unit)
        _link(ci, f"clipitem-v-{p['g']}", "video", 1, p["g"])
        _link(ci, f"clipitem-a1-{p['g']}", "audio", 1, p["g"])
        _link(ci, f"clipitem-a2-{p['g']}", "audio", 2, p["g"])
    _text(v1, "enabled", "TRUE")
    _text(v1, "locked", "FALSE")

    # V2 — captions overlay (one connected clip spanning the cut, alpha keyed)
    if overlay is not None:
        v2 = ET.SubElement(video, "track")
        ci = ET.SubElement(v2, "clipitem", {"id": "clipitem-captions"})
        _text(ci, "name", "Captions")
        _text(ci, "enabled", "TRUE")
        _text(ci, "duration", total_f)
        _rate(ci, fps)
        _text(ci, "start", 0)
        _text(ci, "end", total_f)
        _text(ci, "in", 0)
        _text(ci, "out", total_f)
        _text(ci, "alphatype", "straight")
        _file_element(
            ci, file_id="file-2", asset=overlay, duration_frames=total_f,
            fps=fps, width=width, height=height, define=True,
        )
        st = ET.SubElement(ci, "sourcetrack")
        _text(st, "mediatype", "video")
        _text(v2, "enabled", "TRUE")
        _text(v2, "locked", "FALSE")

    # ── audio (base cut's stereo) ──
    audio = ET.SubElement(media, "audio")
    aformat = ET.SubElement(audio, "format")
    asc = ET.SubElement(aformat, "samplecharacteristics")
    _text(asc, "depth", 16)
    _text(asc, "samplerate", reframed.audio_rate)
    _text(audio, "numOutputChannels", reframed.audio_channels)

    for ch in (1, 2):
        at = ET.SubElement(audio, "track")
        for p in placed:
            ci = ET.SubElement(at, "clipitem", {"id": f"clipitem-a{ch}-{p['g']}"})
            _text(ci, "name", reframed.name)
            _text(ci, "enabled", "TRUE")
            _text(ci, "duration", src_dur_f)
            _rate(ci, fps)
            _text(ci, "start", p["start"])
            _text(ci, "end", p["end"])
            _text(ci, "in", p["in"])
            _text(ci, "out", p["out"])
            _file_element(
                ci, file_id="file-1", asset=reframed, duration_frames=src_dur_f,
                fps=fps, width=width, height=height, define=False,
            )
            stk = ET.SubElement(ci, "sourcetrack")
            _text(stk, "mediatype", "audio")
            _text(stk, "trackindex", ch)
            _link(ci, f"clipitem-v-{p['g']}", "video", 1, p["g"])
            _link(ci, f"clipitem-a1-{p['g']}", "audio", 1, p["g"])
            _link(ci, f"clipitem-a2-{p['g']}", "audio", 2, p["g"])
        _text(at, "enabled", "TRUE")
        _text(at, "locked", "FALSE")
        _text(at, "outputchannelindex", ch)

    body = ET.tostring(xmeml, encoding="unicode")
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n{body}\n'


# ── orchestrator (pure) ───────────────────────────────────────────────────────

def assemble_xmeml(
    decision: DecisionList,
    caption_track: Optional[CaptionTrack] = None,
    *,
    reframed_src: str,
    overlay_src: Optional[str] = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    sequence_name: Optional[str] = None,
    reframed_name: Optional[str] = None,
) -> Tuple[str, Optional[CaptionTrack]]:
    """Build the Premiere-compatible XMEML and the cut-time caption track.

    Mirrors :func:`~video_pipeline.fcpxml.document.assemble_fcpxml` exactly — same
    inputs, same base cut + caption remap — but serializes to FCP7 XML. Returns
    ``(xml, cut_caption_track)``; the cut-time track is what the caller renders to
    the overlay at ``overlay_src``.
    """
    from pathlib import Path

    base_clips = build_base_cut(decision, fps)

    reframed = Asset(
        id="file-1",
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
                id="file-2", name="captions", src=overlay_src,
                duration=decision.source_duration(), has_audio=False,
            )

    xml = build_document(
        base_clips,
        reframed=reframed,
        overlay=overlay,
        width=width,
        height=height,
        fps=fps,
        sequence_name=sequence_name or (decision.source or "video-pipeline cut"),
    )
    return xml, cut_track
