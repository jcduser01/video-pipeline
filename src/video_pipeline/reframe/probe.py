"""Reframe probe runner — daily-driver (Ono-Sendai) entry point.

Glues real subject tracking (MediaPipe) + FFmpeg into the trust-model probe:

    raw landscape clip  ->  track subject  ->  crop plan  ->  reframed 9:16 mp4

This is the run the CEO accepts or rejects on real footage. It needs MediaPipe,
OpenCV, and an FFmpeg binary, so it does NOT execute in the JasonOS sandbox; the
pure pieces it calls (``build_crop_plan``, ``ffmpeg_crop_command``) are tested
there instead.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from .crop import ffmpeg_crop_command
from .plan import build_crop_plan
from .tracker import SubjectTracker


def reframe(
    input_path: str,
    output_path: str,
    out_w: int = 1080,
    out_h: int = 1920,
    mode: str = "static",
    tracker: Optional[SubjectTracker] = None,
    tracker_name: str = "opencv",
    dry_run: bool = False,
) -> list:
    """Reframe one clip. Returns the FFmpeg argv (and runs it unless dry_run).

    If ``tracker`` is None, one is constructed from ``tracker_name``:
    ``"opencv"`` (default — bundled Haar cascade, no model download) or
    ``"mediapipe"`` (Tasks API; downloads a model on first use).
    """
    if tracker is None:
        if tracker_name == "mediapipe":
            from .tracker import MediaPipeTracker
            tracker = MediaPipeTracker()
        elif tracker_name == "opencv":
            from .tracker import OpenCVFaceTracker
            tracker = OpenCVFaceTracker()
        else:
            raise ValueError(f"unknown tracker: {tracker_name!r}")

    src_w, src_h, duration = _probe_dimensions(input_path)
    subjects = tracker.track(input_path)
    plan = build_crop_plan(
        subjects, src_w, src_h, out_w=out_w, out_h=out_h, mode=mode, duration=duration
    )
    cmd = ffmpeg_crop_command(input_path, output_path, plan)
    if not dry_run:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        if Path(input_path).resolve() == out.resolve():
            # The `base` channel is rewritten in place, but FFmpeg can't edit a
            # file it's reading — render to a temp sibling, then atomically replace.
            tmp = out.with_name(f".{out.stem}.tmp{out.suffix}")
            subprocess.run(ffmpeg_crop_command(input_path, str(tmp), plan), check=True)
            os.replace(tmp, out)
        else:
            subprocess.run(cmd, check=True)
    return cmd


def _probe_dimensions(input_path: str):  # pragma: no cover - needs ffprobe + a file
    """Return (width, height, duration_seconds) via ffprobe."""
    import json

    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height:format=duration",
            "-of", "json", input_path,
        ],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    stream = data["streams"][0]
    duration = float(data.get("format", {}).get("duration", 0.0) or 0.0)
    return int(stream["width"]), int(stream["height"]), duration
