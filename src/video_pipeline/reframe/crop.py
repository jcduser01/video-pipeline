"""Crop plan -> FFmpeg command.

Pure string/argument assembly (no subprocess here, so it is unit-testable).
``probe.py`` runs the returned argv. A static plan becomes a single
``crop=...,scale=...`` filter; a dynamic plan becomes a time-keyed ``crop`` whose
``x`` is a piecewise expression over ``t`` (FFmpeg evaluates per frame).
"""

from __future__ import annotations

from typing import List

from .plan import CropPlan


def _scale_filter(out_w: int, out_h: int) -> str:
    return f"scale={out_w}:{out_h}:flags=lanczos"


def static_filtergraph(plan: CropPlan) -> str:
    w = plan.windows[0]
    return (
        f"crop={w.w}:{w.h}:{w.x}:{w.y},"
        f"{_scale_filter(plan.out_w, plan.out_h)}"
    )


def dynamic_filtergraph(plan: CropPlan) -> str:
    """Piecewise-constant x(t) crop. Each segment holds its x until the next.

    The x value is single-quoted in the filtergraph, so commas inside the
    expression are literal — they must NOT also be backslash-escaped (doing both
    corrupts the filter). Consecutive windows that share the same x are collapsed
    so the expression stays compact (the plan's dead-band makes many identical).
    """
    ws = plan.windows
    h = ws[0].h
    cw = ws[0].w
    y = ws[0].y

    # collapse consecutive equal-x windows into segments: (t_end, x)
    segs: list = []
    for w in ws:
        if segs and segs[-1][1] == w.x:
            segs[-1] = (w.t_end, w.x)
        else:
            segs.append((w.t_end, w.x))

    # nested if(lt(t, t_end), x, <rest>) over segment boundaries
    expr = str(segs[-1][1])
    for t_end, x in reversed(segs[:-1]):
        expr = f"if(lt(t,{t_end:.3f}),{x},{expr})"
    return (
        f"crop=w={cw}:h={h}:x='{expr}':y={y},"
        f"{_scale_filter(plan.out_w, plan.out_h)}"
    )


def filtergraph(plan: CropPlan) -> str:
    if plan.mode == "static" or len(plan.windows) == 1:
        return static_filtergraph(plan)
    return dynamic_filtergraph(plan)


def ffmpeg_crop_command(
    input_path: str,
    output_path: str,
    plan: CropPlan,
    crf: int = 18,
    preset: str = "medium",
) -> List[str]:
    """Assemble the FFmpeg argv that renders the reframed vertical video.

    Audio is stream-copied (the reframe is a spatial-only operation; no
    speech-based edits happen here — that is the rough-cut phase).
    """
    return [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-vf", filtergraph(plan),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path,
    ]
