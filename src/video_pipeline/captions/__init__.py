"""Captions phase (INI-085 Phase 3).

Captions are **two layers, kept separate** (shaping brief §3.3):

  - **Timing layer** — ``chunk``: a word-level transcript (the ``roughcut``
    ``Transcriber`` seam — mlx-whisper on the daily driver) becomes 2–4-word
    cues, with glossary mishear->canonical correction applied first so proper
    nouns land right on the first pass (the DoD item). Pure, unit-tested.
  - **Style layer** — ``style`` + ``remotion``: layered repo-resident config
    (``config/caption-styles/`` global + per-identity) resolves the look; the
    bundled Remotion project renders a styled overlay from the props contract.
    Style is config; rendering is a daily-driver/Node seam (like mlx-whisper).

The **caption file** (``cue.CaptionTrack``) is the product — a human-editable
YAML round-trip; fix ``text:`` and re-render. ``placement`` derives a caption box
guaranteed inside the safe-zone polygon (notch-aware); ``export`` emits portable
SRT + the Remotion props JSON. Only ``runner``, the mlx-whisper transcriber, and
the Remotion subprocess need native deps; everything else is pure and tested.
"""

from __future__ import annotations

from .chunk import apply_glossary_to_words, chunk_transcript
from .cue import CaptionTrack, Cue
from .export import (
    build_props_from_safezone,
    cues_to_srt,
    seconds_to_frame,
    track_to_remotion_props,
    write_remotion_props,
)
from .placement import CaptionBox, caption_box
from .remotion import remotion_render_command, render_overlay
from .style import CaptionStyle, load_caption_style

__all__ = [
    "Cue",
    "CaptionTrack",
    "CaptionStyle",
    "load_caption_style",
    "chunk_transcript",
    "apply_glossary_to_words",
    "CaptionBox",
    "caption_box",
    "cues_to_srt",
    "track_to_remotion_props",
    "build_props_from_safezone",
    "write_remotion_props",
    "seconds_to_frame",
    "remotion_render_command",
    "render_overlay",
]
