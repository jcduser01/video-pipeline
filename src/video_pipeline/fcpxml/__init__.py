"""FCPXML handoff (INI-085 Phase 5).

Assembles the pipeline's accepted layers — the **base cut** (the rough-cut
decision file's KEEP segments) over the **reframed vertical clip**, plus the
**styled caption overlay** — onto labeled tracks in an FCPXML that opens in
Premiere Pro (primary), DaVinci Resolve, or Final Cut Pro. The editor's last
mile (pacing, transitions, music, final mix) stays with the CEO.

Two pure pieces, one daily-driver glue:

  - :mod:`~video_pipeline.fcpxml.timeline` — build the cut timeline from the
    decision file and **remap caption cues from source-time to cut-time** (the
    cut compresses time by dropping segments, so captions must move with it).
  - :mod:`~video_pipeline.fcpxml.xmeml` — the **Premiere-compatible** FCP7 XML
    (XMEML) builder and :func:`assemble_xmeml`. This is the **default** handoff
    format, since Premiere Pro does not import FCPXML.
  - :mod:`~video_pipeline.fcpxml.document` — the FCPXML 1.10 string builder and
    :func:`assemble_fcpxml` (the alternative format; Resolve / Final Cut).
  - :mod:`~video_pipeline.fcpxml.runner` — daily-driver glue: resolve media
    paths, pick the format, write the project plus the cut-time caption file for
    the aligned overlay render.

Both serializers consume the same format-agnostic timeline, so the base cut and
the source→cut caption remap are shared.
"""

from __future__ import annotations

from .document import Asset, assemble_fcpxml
from .timeline import (
    BaseClip,
    KeptSpan,
    build_base_cut,
    remap_track,
    source_to_cut,
)
from .xmeml import assemble_xmeml

__all__ = [
    "Asset",
    "assemble_fcpxml",
    "assemble_xmeml",
    "BaseClip",
    "KeptSpan",
    "build_base_cut",
    "remap_track",
    "source_to_cut",
]
