"""Crop-plan computation — subject centres -> a stabilised crop window.

Pure and fully unit-tested. Given per-frame subject centres and the source
dimensions, produce a 9:16 (profile-aspect) crop window that:
  - has the exact output aspect ratio,
  - is clamped inside the source frame (never crops outside the footage),
  - is stabilised so the reframe pans *smoothly* rather than snapping.

Two modes:
  - ``static``  (probe default) — one robust window for the whole clip.
  - ``dynamic`` — a smooth pan that follows the subject. Built only from frames
    where the subject was actually detected (gaps are interpolated across, not
    snapped to a centred guess), zero-phase smoothed, velocity-capped, then
    reduced to a few linear keyframes. The crop x interpolates linearly between
    keyframes — continuous motion, no steps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import List, Optional, Tuple

from .tracker import FrameSubject


@dataclass(frozen=True)
class CropWindow:
    """In dynamic mode this is a keyframe: x is the crop left edge at ``t_start``;
    the renderer interpolates linearly to the next keyframe's x by ``t_end``."""

    t_start: float
    t_end: float
    x: int
    y: int
    w: int
    h: int

    @property
    def aspect(self) -> float:
        return self.w / self.h


@dataclass(frozen=True)
class CropPlan:
    src_w: int
    src_h: int
    out_w: int
    out_h: int
    mode: str
    windows: List[CropWindow]


# ── geometry helpers ──────────────────────────────────────────────────────────

def crop_dims(src_w: int, src_h: int, out_w: int, out_h: int) -> Tuple[int, int]:
    """Largest crop of (src_w, src_h) matching the out aspect, even dimensions."""
    cw = src_h * out_w / out_h
    if cw <= src_w:
        crop_w, crop_h = cw, float(src_h)
    else:
        crop_w, crop_h = float(src_w), src_w * out_h / out_w
    crop_w = min(src_w, int(round(crop_w / 2) * 2))
    crop_h = min(src_h, int(round(crop_h / 2) * 2))
    return crop_w, crop_h


def clamp_center(cx: float, crop_w: int, src_w: int) -> float:
    """Clamp a desired centre x so the crop window stays inside the frame."""
    lo = crop_w / 2
    hi = src_w - crop_w / 2
    if hi < lo:
        return src_w / 2
    return min(max(cx, lo), hi)


def window_x(cx: float, crop_w: int, src_w: int) -> int:
    """Integer left edge for a clamped centre."""
    x = int(round(clamp_center(cx, crop_w, src_w) - crop_w / 2))
    return min(max(x, 0), src_w - crop_w)


def ema_smooth(values: List[float], alpha: float) -> List[float]:
    """Exponential moving average. alpha in (0, 1]; higher = less smoothing."""
    if not values:
        return []
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _ema_zero_phase(values: List[float], alpha: float) -> List[float]:
    """Forward+backward EMA = zero phase lag (no directional bias)."""
    fwd = ema_smooth(values, alpha)
    back = ema_smooth(list(reversed(fwd)), alpha)
    return list(reversed(back))


def _interp(grid: List[float], ts: List[float], xs: List[float]) -> List[float]:
    """Linear interpolation of (ts, xs) onto grid; holds the end values."""
    out: List[float] = []
    j = 0
    n = len(ts)
    for t in grid:
        if t <= ts[0]:
            out.append(xs[0])
            continue
        if t >= ts[-1]:
            out.append(xs[-1])
            continue
        while j + 1 < n and ts[j + 1] < t:
            j += 1
        # ts[j] <= t <= ts[j+1]
        t0, t1 = ts[j], ts[j + 1]
        x0, x1 = xs[j], xs[j + 1]
        f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        out.append(x0 + (x1 - x0) * f)
    return out


def _douglas_peucker(ts: List[float], xs: List[float], tol: float) -> List[int]:
    """Indices to keep so the polyline x(t) is within ``tol`` of the original."""
    n = len(xs)
    if n <= 2:
        return list(range(n))
    keep = {0, n - 1}
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        t0, t1 = ts[a], ts[b]
        x0, x1 = xs[a], xs[b]
        dmax, idx = -1.0, -1
        for i in range(a + 1, b):
            xlin = x0 if t1 == t0 else x0 + (x1 - x0) * (ts[i] - t0) / (t1 - t0)
            d = abs(xs[i] - xlin)
            if d > dmax:
                dmax, idx = d, i
        if dmax > tol and idx != -1:
            keep.add(idx)
            stack.append((a, idx))
            stack.append((idx, b))
    return sorted(keep)


def sample_x(plan: CropPlan, t: float) -> float:
    """Crop left edge at time t (mirrors the renderer: linear between keyframes)."""
    ws = plan.windows
    if len(ws) == 1:
        return float(ws[0].x)
    ts = [w.t_start for w in ws]
    xs = [w.x for w in ws]
    if t <= ts[0]:
        return float(xs[0])
    if t >= ts[-1]:
        return float(xs[-1])
    for i in range(len(ts) - 1):
        if ts[i] <= t <= ts[i + 1]:
            f = (t - ts[i]) / (ts[i + 1] - ts[i]) if ts[i + 1] > ts[i] else 0.0
            return xs[i] + (xs[i + 1] - xs[i]) * f
    return float(xs[-1])


def _robust_center(subjects: List[FrameSubject], src_w: int) -> float:
    if not subjects:
        return src_w / 2
    confident = [s.cx for s in subjects if s.confidence > 0]
    xs = confident if confident else [s.cx for s in subjects]
    return float(median(xs))


def _crop_y(
    subjects: List[FrameSubject],
    src_h: int,
    crop_h: int,
    subject_y_frac: Optional[float],
    pan_y: Optional[float] = None,
) -> int:
    """Top edge of the crop window.

    ``pan_y`` (INI-091 manual pan) — when given — is the crop *centre* as a normalized
    source fraction (0–1); it overrides the subject-derived anchor entirely. Otherwise
    ``subject_y_frac`` (INI-090 framing) anchors the subject's vertical centre at that
    fraction of the crop height; ``None`` centres the crop (legacy). Either way the
    result is clamped so the window stays inside the source frame — so a full-height
    crop (no vertical slack) is always ``y == 0``.
    """
    if pan_y is not None:
        y = int(round(pan_y * src_h - crop_h / 2))
        return min(max(y, 0), max(0, src_h - crop_h))
    if subject_y_frac is None:
        return (src_h - crop_h) // 2
    cys = [s.cy for s in subjects if s.confidence > 0]
    cy = float(median(cys)) if cys else src_h / 2.0
    y = int(round(cy - subject_y_frac * crop_h))
    return min(max(y, 0), src_h - crop_h)


# ── plan builders ─────────────────────────────────────────────────────────────

def _static_plan(subjects, src_w, src_h, out_w, out_h, crop_w, crop_h, y, duration,
                 pan_x=None) -> CropPlan:
    # pan_x (INI-091 manual pan) is the crop centre as a normalized source fraction;
    # it overrides the subject-derived centre. None keeps the legacy robust centre.
    cx = pan_x * src_w if pan_x is not None else _robust_center(subjects, src_w)
    x = window_x(cx, crop_w, src_w)
    t_start = subjects[0].t if subjects else 0.0
    t_end = duration if duration is not None else (subjects[-1].t if subjects else 0.0)
    return CropPlan(
        src_w, src_h, out_w, out_h, "static",
        [CropWindow(t_start, max(t_end, t_start), x, y, crop_w, crop_h)],
    )


def build_crop_plan(
    subjects: List[FrameSubject],
    src_w: int,
    src_h: int,
    out_w: int = 1080,
    out_h: int = 1920,
    mode: str = "static",
    grid_fps: float = 15.0,
    smoothing_seconds: float = 0.6,
    max_pan_frac_per_s: float = 0.12,
    simplify_tol_px: float = 3.0,
    duration: Optional[float] = None,
    scale: float = 1.0,
    subject_y_frac: Optional[float] = None,
    pan_x: Optional[float] = None,
    pan_y: Optional[float] = None,
) -> CropPlan:
    """Build a crop plan from subject centres.

    Args:
        subjects:           per-frame subject centres (may be empty -> centred).
        src_w, src_h:       source dimensions.
        out_w, out_h:       output (profile) dimensions; sets the crop aspect.
        mode:               "static" | "dynamic".
        grid_fps:           resample rate for the dynamic smoothing pass.
        smoothing_seconds:  zero-phase EMA time constant (larger = smoother).
        max_pan_frac_per_s: max pan speed as a fraction of src_w per second
                            (caps how fast the crop can travel -> eased moves).
        simplify_tol_px:    keyframe simplification tolerance (Douglas-Peucker).
        duration:           clip duration (for the final keyframe's end time).
        scale:              punch-in factor (INI-090 framing). 1.0 = widest native
                            crop; > 1.0 punches in (crop = native / scale -> subject
                            larger). < 1.0 clamps to native — no pull-back past the
                            source bounds (no fill), so native is the widest framing.
        subject_y_frac:     where the subject's vertical centre sits in the crop
                            (0=top, 1=bottom). Only bites when crop_h < src_h.
                            None keeps the legacy centred crop.
        pan_x:              INI-091 manual pan. Crop CENTRE x as a normalized source
                            fraction (0–1); overrides the subject-derived centre. None
                            keeps subject-derived centring (the default). In dynamic
                            mode an explicit pan_x pins the whole clip to that x (a
                            manual pan is a deliberate fixed framing, not a follow).
        pan_y:              INI-091 manual pan. Crop CENTRE y as a normalized source
                            fraction (0–1); overrides subject_y_frac. None keeps the
                            subject-derived / centred vertical anchor.
    """
    crop_w, crop_h = crop_dims(src_w, src_h, out_w, out_h)
    if scale > 1.0:
        # Punch in: a smaller crop of the same aspect, scaled up to the output.
        crop_w = min(src_w, max(2, int(round(crop_w / scale / 2)) * 2))
        crop_h = min(src_h, max(2, int(round(crop_h / scale / 2)) * 2))
    # scale <= 1.0 keeps the native (widest no-fill) crop.
    y = _crop_y(subjects, src_h, crop_h, subject_y_frac, pan_y=pan_y)

    confident = [s for s in subjects if s.confidence > 0]

    # An explicit horizontal pan is a fixed manual framing: the crop x is pinned, so
    # the dynamic follow is bypassed and a single static window carries the pan.
    if mode == "static" or len(confident) < 2 or pan_x is not None:
        return _static_plan(subjects, src_w, src_h, out_w, out_h, crop_w, crop_h, y,
                            duration, pan_x=pan_x)

    if mode != "dynamic":
        raise ValueError(f"unknown mode: {mode!r}")

    # 1. keyframes from DETECTED frames only (clamped centres). Gaps -> interpolated.
    conf_t = [s.t for s in confident]
    conf_c = [clamp_center(s.cx, crop_w, src_w) for s in confident]

    t0 = conf_t[0]
    t_end = duration if duration is not None else subjects[-1].t
    if t_end <= t0:
        return _static_plan(subjects, src_w, src_h, out_w, out_h, crop_w, crop_h, y, duration)

    # 2. dense uniform grid + linear interpolation across detection gaps
    n_steps = max(2, int(math.ceil((t_end - t0) * grid_fps)) + 1)
    grid = [t0 + i * (t_end - t0) / (n_steps - 1) for i in range(n_steps)]
    dense = _interp(grid, conf_t, conf_c)

    # 3. zero-phase smoothing
    dt = (t_end - t0) / (n_steps - 1)
    alpha = 1.0 - math.exp(-dt / max(smoothing_seconds, 1e-6))
    dense = _ema_zero_phase(dense, alpha)

    # 4. velocity cap (bounded pan speed -> a far move eases over more time)
    max_step = max_pan_frac_per_s * src_w * dt
    for i in range(1, len(dense)):
        lo, hi = dense[i - 1] - max_step, dense[i - 1] + max_step
        dense[i] = min(max(dense[i], lo), hi)
    for i in range(len(dense) - 2, -1, -1):  # backward pass keeps it symmetric
        lo, hi = dense[i + 1] - max_step, dense[i + 1] + max_step
        dense[i] = min(max(dense[i], lo), hi)

    # 5. simplify to a few linear keyframes
    keep = _douglas_peucker(grid, dense, simplify_tol_px)

    windows: List[CropWindow] = []
    for k, gi in enumerate(keep):
        t_start = grid[gi]
        t_next = grid[keep[k + 1]] if k + 1 < len(keep) else t_end
        x = window_x(dense[gi], crop_w, src_w)
        windows.append(CropWindow(t_start, max(t_next, t_start), x, y, crop_w, crop_h))

    # collapse identical-x neighbours (purely constant stretches need no keyframe)
    collapsed: List[CropWindow] = []
    for w in windows:
        if collapsed and collapsed[-1].x == w.x:
            prev = collapsed[-1]
            collapsed[-1] = CropWindow(prev.t_start, w.t_end, prev.x, y, crop_w, crop_h)
        else:
            collapsed.append(w)
    return CropPlan(src_w, src_h, out_w, out_h, "dynamic", collapsed)
