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


def _piecewise_linear_expr(keys, lo: int, hi: int) -> str:
    """Build an FFmpeg piecewise-LINEAR expression over ``(t_start, value)`` keys.

    Between consecutive keyframes the value ramps linearly; after the last it holds.
    A single key collapses to a bare constant (no conditional). Multi-key expressions
    are wrapped in ``clip(...,lo,hi)`` so the geometry can never leave the frame. The
    result is single-quoted by the caller, so commas inside are literal and must NOT
    also be backslash-escaped (doing both corrupts the filter).
    """
    if len(keys) == 1:
        return str(keys[0][1])
    expr = str(keys[-1][1])  # hold after the last keyframe
    for i in range(len(keys) - 2, -1, -1):
        t0, v0 = keys[i]
        t1, v1 = keys[i + 1]
        dt = t1 - t0
        if dt <= 1e-6:
            seg = str(v1)
        else:
            # v0 + (v1-v0)*(t-t0)/dt -> linear ramp over [t0, t1]
            seg = f"({v0}+({v1 - v0})*(t-{t0:.3f})/{dt:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"clip({expr},{lo},{hi})"  # belt-and-suspenders: stay in frame


def dynamic_filtergraph(plan: CropPlan) -> str:
    """Piecewise-LINEAR x(t) and y(t) crop: interpolate between keyframes (no steps).

    Each window is a keyframe (its x/y at ``t_start``). Between consecutive keyframes
    the crop ramps linearly on each axis that moves; an axis that is constant across
    every window collapses to a bare integer (so the legacy X-only, fixed-Y plan emits
    exactly its prior form). Under a Phase-5 Y composition lock the crop top moves too,
    so ``y`` becomes a piecewise expression the same way ``x`` does.
    """
    ws = plan.windows
    h = ws[0].h
    cw = ws[0].w
    max_x = plan.src_w - cw
    max_y = plan.src_h - h

    x_keys = [(w.t_start, w.x) for w in ws]
    y_keys = [(w.t_start, w.y) for w in ws]
    x_moves = any(w.x != ws[0].x for w in ws)
    y_moves = any(w.y != ws[0].y for w in ws)

    x_expr = _piecewise_linear_expr(x_keys, 0, max_x) if x_moves else str(ws[0].x)
    y_part = (
        f"y='{_piecewise_linear_expr(y_keys, 0, max_y)}'" if y_moves else f"y={ws[0].y}"
    )

    return (
        f"crop=w={cw}:h={h}:x='{x_expr}':{y_part},"
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
