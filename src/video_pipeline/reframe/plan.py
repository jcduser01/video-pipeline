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
    reduced to a few linear keyframes. The crop x (and, under a composition lock,
    the crop y) interpolate linearly between keyframes — continuous motion, no
    steps.

Composition lock (INI-091 Phase 5)
----------------------------------
Dynamic mode gains a ``lock`` axis (``"none"`` | ``"x"`` | ``"y"`` | ``"both"``):

  - ``"none"`` (default) — the legacy behaviour, unchanged: the dynamic follow
    smooths the X crop-centre onto the subject; Y is the fixed ``_crop_y`` anchor.
    An explicit ``pan_x`` still pins the whole clip to a single static window.
  - ``"x"`` / ``"y"`` / ``"both"`` — *Composition Lock*. The box set in Phase 4
    (carried as the framing model's ``pan_x``/``pan_y``) establishes the subject's
    **relative placement** in the crop. The engine then moves the crop on each
    *locked* axis to HOLD that relative placement as the subject moves (the same
    dense-grid → zero-phase smooth → velocity-cap → Douglas-Peucker pipeline, now
    per locked axis). An *unlocked* axis stays fixed at the box's pan value on that
    axis. Locking neither (``"none"`` with explicit pan, or unlocking both) is
    Static. The crop is always clamped inside the source frame.
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


def window_y(cy: float, crop_h: int, src_h: int) -> int:
    """Integer top edge for a clamped vertical centre (mirrors :func:`window_x`).

    Used by the dynamic composition lock when the Y axis follows the subject; the
    clamp math is axis-agnostic (:func:`clamp_center` is reused on the height axis).
    """
    y = int(round(clamp_center(cy, crop_h, src_h) - crop_h / 2))
    return min(max(y, 0), src_h - crop_h)


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


def sample_y(plan: CropPlan, t: float) -> float:
    """Crop top edge at time t (mirrors :func:`sample_x` on the vertical axis).

    Under a Y-axis composition lock the crop top moves per keyframe; otherwise
    every window shares one ``y`` and this is constant. Linear between keyframes,
    matching the renderer's piecewise-linear y(t)."""
    ws = plan.windows
    if len(ws) == 1:
        return float(ws[0].y)
    ts = [w.t_start for w in ws]
    ys = [w.y for w in ws]
    if t <= ts[0]:
        return float(ys[0])
    if t >= ts[-1]:
        return float(ys[-1])
    for i in range(len(ts) - 1):
        if ts[i] <= t <= ts[i + 1]:
            f = (t - ts[i]) / (ts[i + 1] - ts[i]) if ts[i + 1] > ts[i] else 0.0
            return ys[i] + (ys[i + 1] - ys[i]) * f
    return float(ys[-1])


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

def _smooth_centre_track(
    ts: List[float],
    centres: List[float],
    grid: List[float],
    dt: float,
    smoothing_seconds: float,
    max_step: float,
) -> List[float]:
    """The dynamic stabilisation pass for one axis: interpolate across gaps onto a
    dense grid, zero-phase smooth, then velocity-cap (forward+backward).

    Factored out of :func:`build_crop_plan` so the X follow and the Phase-5 Y follow
    run identical maths — the legacy X path is byte-for-byte this sequence.
    """
    dense = _interp(grid, ts, centres)
    alpha = 1.0 - math.exp(-dt / max(smoothing_seconds, 1e-6))
    dense = _ema_zero_phase(dense, alpha)
    for i in range(1, len(dense)):
        lo, hi = dense[i - 1] - max_step, dense[i - 1] + max_step
        dense[i] = min(max(dense[i], lo), hi)
    for i in range(len(dense) - 2, -1, -1):  # backward pass keeps it symmetric
        lo, hi = dense[i + 1] - max_step, dense[i + 1] + max_step
        dense[i] = min(max(dense[i], lo), hi)
    return dense


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


def _box_centre(pan: Optional[float], src: int, crop: int, subjects, axis: str) -> float:
    """The set box's crop centre on one axis, clamped inside the source frame.

    ``pan`` (the box's normalized pan on this axis) wins when given; otherwise the
    subject-derived robust centre seeds the box (so a lock with no explicit box still
    holds the subject's natural placement). ``None`` pan with no subject -> frame mid.
    """
    if pan is not None:
        centre = pan * src
    else:
        conf = [(s.cx if axis == "x" else s.cy) for s in subjects if s.confidence > 0]
        centre = float(median(conf)) if conf else src / 2.0
    return clamp_center(centre, crop, src)


def _subject_ref(subjects, axis: str, src: int) -> float:
    """Median confident subject centre on one axis (the box-set-time reference)."""
    conf = [(s.cx if axis == "x" else s.cy) for s in subjects if s.confidence > 0]
    return float(median(conf)) if conf else src / 2.0


def _lock_plan(
    subjects, confident, src_w, src_h, out_w, out_h, crop_w, crop_h,
    y_fixed, duration, lock, pan_x, pan_y,
    grid_fps, smoothing_seconds, max_pan_frac_per_s, simplify_tol_px,
) -> CropPlan:
    """Composition-Lock dynamic plan (INI-091 Phase 5).

    The set box (``pan_x``/``pan_y``) fixes the subject's *relative placement* in the
    crop. A locked axis moves the crop each frame so the (smoothed, velocity-capped,
    clamped) subject keeps that relative placement; an unlocked axis stays parked at the
    box's pan on that axis. Both axes reuse the identical stabilisation pipeline.

    Relative anchor: ``anchor = subject_ref - box_centre`` captured at box-set time, so
    the locked crop centre is ``box_centre + (subject(t) - subject_ref)`` — i.e. the
    subject's offset from the crop centre is held constant.
    """
    lock_x = lock in ("x", "both")
    lock_y = lock in ("y", "both")

    box_cx = _box_centre(pan_x, src_w, crop_w, subjects, "x")
    box_cy = _box_centre(pan_y, src_h, crop_h, subjects, "y")

    # An unlocked axis is fixed at the box; the corresponding top-left edge:
    x_fixed = window_x(box_cx, crop_w, src_w)
    y_box_fixed = window_y(box_cy, crop_h, src_h)
    # Y unlocked keeps the existing _crop_y anchor when no explicit pan_y was given, so
    # lock="x" matches the legacy fixed-Y dynamic; an explicit pan_y parks Y at the box.
    y_unlocked = y_box_fixed if pan_y is not None else y_fixed

    t0 = confident[0].t
    t_end = duration if duration is not None else (subjects[-1].t if subjects else t0)
    if t_end <= t0 or len(confident) < 2:
        # No motion to follow -> the box itself, as a single static window.
        x = x_fixed if lock_x else x_fixed
        y = y_box_fixed if lock_y else y_unlocked
        return CropPlan(src_w, src_h, out_w, out_h, "dynamic",
                        [CropWindow(t0, max(t_end, t0), x, y, crop_w, crop_h)])

    n_steps = max(2, int(math.ceil((t_end - t0) * grid_fps)) + 1)
    grid = [t0 + i * (t_end - t0) / (n_steps - 1) for i in range(n_steps)]
    dt = (t_end - t0) / (n_steps - 1)
    conf_t = [s.t for s in confident]

    def _follow(axis, box_centre, crop, src):
        """Locked-axis dense crop-centre track: box_centre + (subject - subject_ref)."""
        ref = _subject_ref(confident, axis, src)
        raw = [box_centre + ((s.cx if axis == "x" else s.cy) - ref) for s in confident]
        raw = [clamp_center(c, crop, src) for c in raw]
        max_step = max_pan_frac_per_s * src * dt
        return _smooth_centre_track(conf_t, raw, grid, dt, smoothing_seconds, max_step)

    dense_x = _follow("x", box_cx, crop_w, src_w) if lock_x else None
    dense_y = _follow("y", box_cy, crop_h, src_h) if lock_y else None

    # Simplify on whichever axes move; merge the kept indices so a keyframe captures
    # both axes' motion (a join either axis needs becomes a shared keyframe).
    keep_idx = {0, n_steps - 1}
    if dense_x is not None:
        keep_idx.update(_douglas_peucker(grid, dense_x, simplify_tol_px))
    if dense_y is not None:
        keep_idx.update(_douglas_peucker(grid, dense_y, simplify_tol_px))
    keep = sorted(keep_idx)

    windows: List[CropWindow] = []
    for k, gi in enumerate(keep):
        t_start = grid[gi]
        t_next = grid[keep[k + 1]] if k + 1 < len(keep) else t_end
        x = window_x(dense_x[gi], crop_w, src_w) if lock_x else x_fixed
        y = window_y(dense_y[gi], crop_h, src_h) if lock_y else y_unlocked
        windows.append(CropWindow(t_start, max(t_next, t_start), x, y, crop_w, crop_h))

    # collapse neighbours identical on BOTH axes (a constant stretch needs no keyframe).
    collapsed: List[CropWindow] = []
    for w in windows:
        if collapsed and collapsed[-1].x == w.x and collapsed[-1].y == w.y:
            prev = collapsed[-1]
            collapsed[-1] = CropWindow(prev.t_start, w.t_end, prev.x, prev.y, crop_w, crop_h)
        else:
            collapsed.append(w)
    return CropPlan(src_w, src_h, out_w, out_h, "dynamic", collapsed)


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
    lock: str = "none",
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
        lock:               INI-091 Phase 5 *Composition Lock* (dynamic only):
                            "none" (default) | "x" | "y" | "both".
                              - "none" keeps the legacy semantics exactly: an explicit
                                pan_x pins the clip to a single static window, and the
                                dynamic follow (when no pan_x) smooths X onto the
                                subject with a fixed Y.
                              - "x"/"y"/"both" engage the composition lock. The box's
                                pan_x/pan_y establish the subject's RELATIVE placement
                                in the crop (captured against the subject reference at
                                box-set time); each *locked* axis then moves the crop to
                                hold that relative placement as the subject moves
                                (smoothed + velocity-capped + clamped), while an
                                *unlocked* axis stays fixed at the box's pan on that
                                axis. Unlocking both is equivalent to Static. The lock
                                path requires pan_x/pan_y (the set box); missing pans
                                default to the centre (0.5).
    """
    if lock not in ("none", "x", "y", "both"):
        raise ValueError(f"unknown lock: {lock!r}")

    crop_w, crop_h = crop_dims(src_w, src_h, out_w, out_h)
    if scale > 1.0:
        # Punch in: a smaller crop of the same aspect, scaled up to the output.
        crop_w = min(src_w, max(2, int(round(crop_w / scale / 2)) * 2))
        crop_h = min(src_h, max(2, int(round(crop_h / scale / 2)) * 2))
    # scale <= 1.0 keeps the native (widest no-fill) crop.
    y = _crop_y(subjects, src_h, crop_h, subject_y_frac, pan_y=pan_y)

    confident = [s for s in subjects if s.confidence > 0]

    # ── Phase 5: Composition Lock ────────────────────────────────────────────────
    # A lock engages the dynamic follow on the locked axis/axes, holding the subject's
    # relative placement in the set box. It supersedes the legacy "pan pins to static"
    # rule (which is only the lock == "none" behaviour) — under a lock the pan is the
    # relative anchor reference, not a fixed framing.
    if mode == "dynamic" and lock in ("x", "y", "both"):
        return _lock_plan(
            subjects, confident, src_w, src_h, out_w, out_h, crop_w, crop_h,
            y, duration, lock, pan_x, pan_y,
            grid_fps, smoothing_seconds, max_pan_frac_per_s, simplify_tol_px,
        )

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
    dt = (t_end - t0) / (n_steps - 1)
    max_step = max_pan_frac_per_s * src_w * dt

    # 3-4. dense interpolation across gaps + zero-phase smoothing + velocity cap
    dense = _smooth_centre_track(conf_t, conf_c, grid, dt, smoothing_seconds, max_step)

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
