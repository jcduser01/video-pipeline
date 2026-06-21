"""Rough-cut runner — daily-driver (Ono-Sendai) glue.

Wires the pure pieces into the Phase-2 flow:

    media  ->  transcribe  ->  propose  ->  write decision file  ->  (rough render)

Transcription (mlx-whisper) and the FFmpeg render need native deps / a real
binary, so the orchestration here is exercised on the daily driver; the pure
pieces it calls (``propose``, ``concat_filtergraph``, decision round-trip) are
unit-tested in the sandbox. A precomputed Whisper-JSON transcript can be passed
in to skip the MLX step (e.g. re-proposing from a cached ``work/`` transcript).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Optional

from .decision import DecisionList
from .propose import ProposeConfig, propose
from .render import ffmpeg_roughcut_command
from .transcript import (
    MLXWhisperTranscriber,
    SilenceTranscriber,
    Transcriber,
    Transcript,
    transcript_from_whisper_dict,
)


def build_transcriber(name: str) -> Transcriber:
    """Construct a transcriber by name: 'mlx-whisper' (default) or 'silence'."""
    if name in ("mlx-whisper", "mlx", "whisper"):
        return MLXWhisperTranscriber()
    if name == "silence":
        return SilenceTranscriber()
    raise ValueError(f"unknown transcriber: {name!r} (use 'mlx-whisper' or 'silence')")


def probe_duration(media_path: str) -> float:  # pragma: no cover - needs ffprobe + a file
    """Clip duration in seconds via ffprobe."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", media_path],
        capture_output=True, text=True, check=True,
    ).stdout
    return float(json.loads(out).get("format", {}).get("duration", 0.0) or 0.0)


def load_transcript_json(path: str) -> Transcript:
    """Load a Whisper-shaped transcript JSON from disk."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return transcript_from_whisper_dict(data)


def make_rough_cut(  # pragma: no cover - daily-driver orchestration (native deps + footage)
    input_path: str,
    decision_out: str,
    render_out: Optional[str] = None,
    transcript_json: Optional[str] = None,
    transcriber: Optional[Transcriber] = None,
    transcriber_name: str = "mlx-whisper",
    config: Optional[ProposeConfig] = None,
    profile: Optional[str] = None,
    dry_run: bool = False,
) -> DecisionList:
    """Produce (and persist) the decision file; optionally render the rough cut.

    If ``transcript_json`` is given it is used directly; otherwise ``transcriber``
    (or one built from ``transcriber_name`` — ``"mlx-whisper"`` default, or
    ``"silence"`` for the ASR-free dead-air fallback) transcribes ``input_path``.
    The decision file is always written to ``decision_out``. If ``render_out`` is
    set, the rough cut is rendered there (unless ``dry_run``).
    """
    cfg = config or ProposeConfig()

    if transcript_json:
        transcript = load_transcript_json(transcript_json)
    else:
        transcriber = transcriber or build_transcriber(transcriber_name)
        transcript = transcriber.transcribe(input_path)

    duration = probe_duration(input_path)
    decision = propose(
        transcript, duration=duration, config=cfg,
        source=Path(input_path).name, profile=profile,
    )
    Path(decision_out).parent.mkdir(parents=True, exist_ok=True)
    decision.write(decision_out)

    if render_out:
        cmd = ffmpeg_roughcut_command(input_path, render_out, decision)
        if not dry_run:
            Path(render_out).parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(cmd, check=True)

    return decision


def render_from_decision(  # pragma: no cover - needs ffmpeg + footage
    decision_path: str,
    input_path: str,
    output_path: str,
    dry_run: bool = False,
) -> list:
    """Re-render the rough cut from a (possibly hand-edited) decision file.

    This is the round-trip: edit ``keep:`` flags / boundaries, re-render, and the
    cut changes accordingly. Returns the FFmpeg argv (and runs it unless dry_run).
    """
    decision = DecisionList.read(decision_path)
    cmd = ffmpeg_roughcut_command(input_path, output_path, decision)
    if not dry_run:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if Path(input_path).resolve() == out.resolve():
            # `base` is rewritten in place; FFmpeg can't read+write the same file,
            # so render to a temp sibling and atomically replace.
            tmp = out.with_name(f".{out.stem}.tmp{out.suffix}")
            subprocess.run(
                ffmpeg_roughcut_command(input_path, str(tmp), decision), check=True
            )
            os.replace(tmp, out)
        else:
            subprocess.run(cmd, check=True)
    return cmd
