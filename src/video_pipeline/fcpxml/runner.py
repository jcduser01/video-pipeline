"""FCPXML runner — assembles the handoff project. Daily-driver glue.

Wires the pure pieces into the Phase-5 flow:

    decision file + caption file + reframed clip + caption overlay
        ->  base cut on a labeled spine track (over the reframed clip)
        +   cut-time caption track  ->  Captions overlay clip
        =   out/<project>.fcpxml   (opens in Premiere / Resolve / Final Cut)

The assembly itself is pure (:func:`~video_pipeline.fcpxml.document.assemble_fcpxml`)
and unit-tested; this module only reads/writes files and resolves media paths, so
it carries no native dependency. The referenced media (the reframed clip and the
caption overlay) must exist on the editing machine for the project to relink.

**The caption overlay must be rendered from the cut-time track.** Because the
base cut drops segments, source-timed captions would drift; this runner writes a
**cut-time caption file** (``<project>.captions.cut.yml``) next to the FCPXML.
Render it with ``captions-render`` to produce the overlay the FCPXML references —
then the captions line up with the compressed timeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..captions.cue import CaptionTrack
from ..roughcut.decision import DecisionList
from .document import assemble_fcpxml
from .xmeml import assemble_xmeml

# Handoff formats. ``premiere`` (FCP7 XML / XMEML) is the default — Premiere Pro
# does not import FCPXML. ``fcpxml`` targets Resolve / Final Cut.
FORMATS = ("premiere", "fcpxml")
_SUFFIX = {"premiere": ".xml", "fcpxml": ".fcpxml"}


def assemble_project(
    decision_path: str,
    output_path: str,
    *,
    reframed_clip: str,
    caption_path: Optional[str] = None,
    overlay_path: Optional[str] = None,
    composite_path: Optional[str] = None,
    cut_caption_path: Optional[str] = None,
    fmt: str = "premiere",
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    event_name: str = "JasonOS",
    project_name: Optional[str] = None,
) -> dict:
    """Assemble the editor project (and the cut-time caption file) from disk.

    Reads the decision file (and the caption file, if given) and writes the
    handoff in ``fmt`` — ``premiere`` (FCP7 XML / XMEML, the default, opens
    natively in Premiere Pro) or ``fcpxml`` (Resolve / Final Cut). When captions
    are supplied it also writes the **cut-time** caption file the overlay should
    be rendered from. Returns a dict of the paths written plus a timeline summary.
    """
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r} (use one of {FORMATS})")

    decision = DecisionList.read(decision_path)

    track: Optional[CaptionTrack] = None
    if caption_path:
        track = CaptionTrack.read(caption_path).reindex()

    out = Path(output_path)
    # Default the overlay path the project references (rendered from the cut track).
    resolved_overlay = overlay_path
    if track is not None and resolved_overlay is None:
        resolved_overlay = str(out.with_suffix("").with_suffix(".captions.mov"))

    assemble = assemble_xmeml if fmt == "premiere" else assemble_fcpxml
    kwargs = dict(
        reframed_src=reframed_clip,
        overlay_src=resolved_overlay,
        composite_src=composite_path,
        width=width,
        height=height,
        fps=fps,
    )
    # The two serializers name the project arg differently (sequence vs project).
    if fmt == "premiere":
        kwargs["sequence_name"] = project_name
    else:
        kwargs["project_name"] = project_name
        kwargs["event_name"] = event_name

    xml, cut_track = assemble(decision, track, **kwargs)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")

    result = {
        "format": fmt,
        "project": str(out),
        "clips": len([s for s in decision.segments if s.keep]),
        "kept_duration": decision.kept_duration(),
        "overlay": resolved_overlay if cut_track and cut_track.kept() else None,
        "cut_captions": None,
    }

    # Write the cut-time caption file so the overlay can be rendered aligned.
    if cut_track is not None and cut_track.kept():
        cut_path = Path(
            cut_caption_path
            or str(out.with_suffix("").with_suffix(".captions.cut.yml"))
        )
        cut_path.parent.mkdir(parents=True, exist_ok=True)
        cut_track.write(cut_path)
        result["cut_captions"] = str(cut_path)

    return result
