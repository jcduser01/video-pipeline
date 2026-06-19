"""Captions runner — daily-driver (Ono-Sendai) glue.

Wires the pure pieces into the Phase-3 flow:

    media -> transcribe -> chunk (glossary) -> caption file -> SRT + Remotion props
                                                            -> (styled overlay render)

Transcription (mlx-whisper) and the Remotion render need native deps, so the
orchestration here is exercised on the daily driver; the pure pieces it calls
(``chunk_transcript``, the caption round-trip, ``caption_box``, the exporters)
are unit-tested in the sandbox. A precomputed Whisper-JSON transcript can be
passed in to skip the MLX step — the same cached ``work/`` transcript the
rough-cut phase produces re-uses here, so captions never re-transcribe.

Captions need real words (filler/false-start lexicons need text), so the
silence-only transcriber is **not** valid here — supply mlx-whisper or a cached
transcript JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ..glossary import load_glossary
from ..roughcut.transcript import (
    Transcriber,
    Transcript,
    transcript_from_whisper_dict,
)
from ..safezone.spec import SafeZoneSpec
from .chunk import chunk_transcript
from .cue import CaptionTrack
from .export import (
    build_props_from_safezone,
    cues_to_srt,
    write_remotion_props,
)
from .style import CaptionStyle, load_caption_style


def load_transcript_json(path: str) -> Transcript:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return transcript_from_whisper_dict(data)


def build_caption_track(
    transcript: Transcript,
    source: str,
    identity: str,
    profile: str,
    style: CaptionStyle,
    glossary=None,
) -> CaptionTrack:
    """Pure assembly: transcript + style + glossary -> a CaptionTrack."""
    cues = chunk_transcript(transcript, style=style, glossary=glossary)
    return CaptionTrack(
        source=source,
        cues=cues,
        identity=identity,
        profile=profile,
        style_ref=f"caption-styles/{identity}",
        language=transcript.language,
    )


def make_captions(  # pragma: no cover - daily-driver orchestration (native deps + footage)
    input_path: str,
    caption_out: str,
    identity: str,
    profile: str,
    config_root: str,
    transcript_json: Optional[str] = None,
    transcriber: Optional[Transcriber] = None,
    style_overrides: Optional[dict] = None,
    srt_out: Optional[str] = None,
    props_out: Optional[str] = None,
    safezone_spec_path: Optional[str] = None,
    fps: int = 30,
) -> CaptionTrack:
    """Produce (and persist) the caption file; optionally SRT + Remotion props.

    The caption file is always written. ``srt_out`` writes a portable SRT;
    ``props_out`` (with ``safezone_spec_path``) writes the Remotion style-layer
    props with a safe-zone-derived caption box.
    """
    style = load_caption_style(config_root, identity, overrides=style_overrides)
    glossary = load_glossary(config_root, identity)

    if transcript_json:
        transcript = load_transcript_json(transcript_json)
    elif transcriber is not None:
        transcript = transcriber.transcribe(input_path)
    else:
        raise ValueError(
            "captions need a word-level transcript: pass transcript_json or an "
            "mlx-whisper transcriber (the silence fallback has no words)."
        )

    track = build_caption_track(
        transcript,
        source=Path(input_path).name,
        identity=identity,
        profile=profile,
        style=style,
        glossary=glossary,
    )

    Path(caption_out).parent.mkdir(parents=True, exist_ok=True)
    track.write(caption_out)

    if srt_out:
        Path(srt_out).parent.mkdir(parents=True, exist_ok=True)
        Path(srt_out).write_text(cues_to_srt(track, uppercase=False), encoding="utf-8")

    if props_out:
        if not safezone_spec_path:
            raise ValueError("props_out requires safezone_spec_path")
        spec = SafeZoneSpec.from_json(Path(safezone_spec_path).read_text(encoding="utf-8"))
        props = build_props_from_safezone(track, style, spec, fps=fps)
        write_remotion_props(props, props_out)

    return track
