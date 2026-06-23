"""The framing model — one source of truth for the reframe crop (INI-091 Phase 3).

A reframe crop is described by exactly three numbers::

    {scale, pan_x, pan_y}

  - ``scale`` — the punch-in multiplier. ``1.0`` is the *native widest crop* of the
    target aspect (the largest target-aspect rectangle that fits the source — the most
    you can show without letterbox fill); ``> 1.0`` punches in (a smaller crop of the
    same aspect, scaled up to the output, so the subject is larger). ``< 1.0`` is
    clamped to ``1.0`` — there is deliberately no pull-back past the source bounds (the
    no-fill rule), so native is the widest framing.
  - ``pan_x`` / ``pan_y`` — the crop **centre**, in **normalized source coordinates**
    (``0.0``–``1.0``; ``x`` = fraction of source width, ``y`` = fraction of source
    height, top-left origin). ``(0.5, 0.5)`` centres the crop. The centre is clamped so
    the crop never leaves the footage (the same no-fill rule the legacy pixel path
    enforces).

This is the single value object the draggable box (Phase 4) and the numeric knobs both
bind to; the ML proposal (:func:`propose_framing`) is just its *initial* value. The
canonical-unit transforms below convert the model to/from a pixel crop window and back,
exactly (within integer-pixel rounding) — so **Render reproduces the model's crop with
no re-derivation**: the geometry the model yields is the geometry rendered.

Pure, no native deps, fully unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from ..target_format import UPSCALE_TOLERANCE, AspectPreset, aspect_preset
from .plan import crop_dims


# ── the framing model ────────────────────────────────────────────────────────────

# Pan defaults — a centred crop.
DEFAULT_PAN_X = 0.5
DEFAULT_PAN_Y = 0.5
DEFAULT_SCALE = 1.0


@dataclass(frozen=True)
class FramingModel:
    """``{scale, pan_x, pan_y}`` — the canonical reframe crop description.

    ``scale`` is the punch-in (>= 1.0 enforced; native is the widest framing).
    ``pan_x`` / ``pan_y`` are the crop centre in normalized source coords (0–1).
    The model carries no pixels: it resolves to a pixel crop only against concrete
    source dims + a target aspect, via :func:`model_to_window`.
    """

    scale: float = DEFAULT_SCALE
    pan_x: float = DEFAULT_PAN_X
    pan_y: float = DEFAULT_PAN_Y

    def __post_init__(self) -> None:
        # Clamp eagerly so an out-of-range model can never reach the geometry math.
        # scale < 1.0 -> native (no fill); pan outside [0, 1] -> clamped to frame.
        object.__setattr__(self, "scale", max(1.0, float(self.scale)))
        object.__setattr__(self, "pan_x", min(1.0, max(0.0, float(self.pan_x))))
        object.__setattr__(self, "pan_y", min(1.0, max(0.0, float(self.pan_y))))

    def to_dict(self) -> dict:
        return {"scale": round(self.scale, 6), "pan_x": round(self.pan_x, 6),
                "pan_y": round(self.pan_y, 6)}

    @classmethod
    def from_dict(cls, d: dict) -> "FramingModel":
        return cls(
            scale=float(d.get("scale", DEFAULT_SCALE)),
            pan_x=float(d.get("pan_x", DEFAULT_PAN_X)),
            pan_y=float(d.get("pan_y", DEFAULT_PAN_Y)),
        )


# ── canonical-unit transforms: model <-> pixel crop window ────────────────────────

def native_crop_dims(src_w: int, src_h: int, aspect: AspectPreset) -> Tuple[int, int]:
    """The native (scale=1.0) crop size for ``aspect`` inside the source — even px.

    Thin wrapper over :func:`plan.crop_dims` (the single crop-geometry primitive) so
    the model and the legacy plan path compute identical native dimensions.
    """
    return crop_dims(src_w, src_h, aspect.w, aspect.h)


def scaled_crop_dims(src_w: int, src_h: int, aspect: AspectPreset, scale: float) -> Tuple[int, int]:
    """Crop size at a given punch-in ``scale`` — matches ``build_crop_plan`` exactly.

    ``scale <= 1.0`` returns the native widest crop; ``> 1.0`` shrinks it by ``scale``
    (even dimensions, never larger than the source). This mirrors the arithmetic in
    :func:`plan.build_crop_plan` byte-for-byte, so a model-derived crop and a
    plan-derived crop never disagree on size.
    """
    crop_w, crop_h = native_crop_dims(src_w, src_h, aspect)
    if scale > 1.0:
        crop_w = min(src_w, max(2, int(round(crop_w / scale / 2)) * 2))
        crop_h = min(src_h, max(2, int(round(crop_h / scale / 2)) * 2))
    return crop_w, crop_h


def _clamp_top_left(centre_px: float, crop: int, src: int) -> int:
    """Integer top/left edge for a normalized-centre-derived pixel centre, clamped."""
    edge = int(round(centre_px - crop / 2))
    return min(max(edge, 0), max(0, src - crop))


@dataclass(frozen=True)
class CropGeometry:
    """A concrete pixel crop window ``(x, y, w, h)`` derived from a model.

    ``x``/``y`` are the clamped integer top-left; ``w``/``h`` the even crop size.
    The renderer scales this to the target output size; nothing is re-derived.
    """

    x: int
    y: int
    w: int
    h: int

    @property
    def aspect(self) -> float:
        return self.w / self.h


def model_to_window(
    model: FramingModel, src_w: int, src_h: int, aspect: AspectPreset
) -> CropGeometry:
    """Resolve a :class:`FramingModel` to a clamped pixel crop window.

    ``scale`` sets the crop size; ``pan_x``/``pan_y`` (the normalized crop *centre*)
    set its position. The window is clamped inside the source frame (the no-fill rule),
    so an extreme pan parks the crop against the nearest edge rather than over-cropping.
    """
    crop_w, crop_h = scaled_crop_dims(src_w, src_h, aspect, model.scale)
    cx_px = model.pan_x * src_w
    cy_px = model.pan_y * src_h
    x = _clamp_top_left(cx_px, crop_w, src_w)
    y = _clamp_top_left(cy_px, crop_h, src_h)
    return CropGeometry(x=x, y=y, w=crop_w, h=crop_h)


def window_to_model(
    geom: CropGeometry, src_w: int, src_h: int, aspect: AspectPreset
) -> FramingModel:
    """Inverse transform: a pixel crop window back to a :class:`FramingModel`.

    ``scale`` is recovered from the crop width vs the native crop width; ``pan_x`` /
    ``pan_y`` from the window's pixel centre over the source dims. Round-trips with
    :func:`model_to_window` within integer-pixel rounding (the headline DoD): for a
    model resolved to a window, re-deriving the model and re-resolving yields the same
    window.
    """
    native_w, native_h = native_crop_dims(src_w, src_h, aspect)
    # Recover the punch-in from the crop size. w and h are rounded independently from
    # native/scale in the forward transform, so a single axis ratio can re-resolve to a
    # 1-px-off size. Pick the axis-derived scale that *re-resolves to this exact window*
    # (the forward transform is authoritative); this makes model<->window idempotent.
    candidates = []
    if geom.w:
        candidates.append(native_w / geom.w)
    if geom.h:
        candidates.append(native_h / geom.h)
    candidates.append(1.0)
    scale = candidates[0]
    for cand in candidates:
        cand = max(1.0, cand)
        if scaled_crop_dims(src_w, src_h, aspect, cand) == (geom.w, geom.h):
            scale = cand
            break
    pan_x = (geom.x + geom.w / 2) / src_w if src_w else DEFAULT_PAN_X
    pan_y = (geom.y + geom.h / 2) / src_h if src_h else DEFAULT_PAN_Y
    return FramingModel(scale=scale, pan_x=pan_x, pan_y=pan_y)


# ── resolution-driven max-zoom + advanced upscale ────────────────────────────────

def max_zoom(
    src_w: int,
    src_h: int,
    aspect: AspectPreset,
    out_w: int,
    out_h: int,
    tolerance: float = UPSCALE_TOLERANCE,
) -> float:
    """The largest punch-in ``scale`` before the crop's NATIVE pixels fall below the
    target output resolution (within ``tolerance`` upscale).

    At ``scale=1.0`` the native crop is the widest; punching in shrinks the crop's
    native pixel count. ``max_zoom`` is the scale at which the *shorter* of the crop's
    native dimensions, blown up by ``tolerance``, still covers the target output. Past
    it the render would upscale beyond tolerance — the hard-stop default refuses that.

    Returns a float ``>= 1.0`` (a target larger than the native crop yields ``1.0`` —
    you cannot even afford native, let alone punch in). Independent of pan (pan moves
    the crop, it does not change its size).
    """
    native_w, native_h = native_crop_dims(src_w, src_h, aspect)
    # crop native dim at scale s is native_dim / s; require native_dim/s * (1+tol) >= out
    # => s <= native_dim * (1+tol) / out, for both axes; the binding (smaller) one wins.
    cap_w = native_w * (1.0 + tolerance) / out_w if out_w else float("inf")
    cap_h = native_h * (1.0 + tolerance) / out_h if out_h else float("inf")
    cap = min(cap_w, cap_h)
    return max(1.0, cap)


@dataclass(frozen=True)
class ZoomClamp:
    """The result of clamping a requested scale to the resolution-driven max-zoom."""

    scale: float          # the effective scale after clamping
    requested: float      # what the caller asked for
    max_zoom: float       # the computed hard-stop
    clamped: bool         # did the clamp bite?


def clamp_scale(
    requested: float,
    src_w: int,
    src_h: int,
    aspect: AspectPreset,
    out_w: int,
    out_h: int,
    *,
    allow_upscale: bool = False,
    tolerance: float = UPSCALE_TOLERANCE,
) -> ZoomClamp:
    """Clamp a requested punch-in ``scale`` to the resolution-driven max-zoom.

    Hard-stop by default: ``scale`` is capped at :func:`max_zoom` and ``clamped`` reports
    whether the cap bit. ``allow_upscale=True`` is the advanced opt-in — the requested
    scale passes through (``clamped`` is then always ``False``), letting the render
    upscale past the resolution budget. ``scale`` is never below ``1.0`` (no fill).
    """
    requested = max(1.0, float(requested))
    mz = max_zoom(src_w, src_h, aspect, out_w, out_h, tolerance=tolerance)
    if allow_upscale:
        return ZoomClamp(scale=requested, requested=requested, max_zoom=mz, clamped=False)
    effective = min(requested, mz)
    return ZoomClamp(scale=effective, requested=requested, max_zoom=mz,
                     clamped=effective < requested)


@dataclass(frozen=True)
class ResolutionReadout:
    """Live resolution readout: a crop's native pixels vs the target output pixels.

    ``upscale_factor`` > 1.0 means the render is enlarging the crop to hit the target
    (the margin the GUI shows next to the zoom slider). ``within_tolerance`` is the
    quick yes/no the readout colours on.
    """

    crop_native_w: int
    crop_native_h: int
    out_w: int
    out_h: int
    upscale_factor: float        # max axis enlargement (>1 = upscaling)
    within_tolerance: bool


def resolution_readout(
    scale: float,
    src_w: int,
    src_h: int,
    aspect: AspectPreset,
    out_w: int,
    out_h: int,
    tolerance: float = UPSCALE_TOLERANCE,
) -> ResolutionReadout:
    """Crop native px vs target output px at a given ``scale`` (the upscale margin).

    The upscale factor is the larger of the two per-axis enlargements (out / native);
    below 1.0 the render downscales (plenty of pixels), above 1.0 it upscales. The
    tolerance flag mirrors the max-zoom budget.
    """
    cw, ch = scaled_crop_dims(src_w, src_h, aspect, max(1.0, float(scale)))
    fx = out_w / cw if cw else float("inf")
    fy = out_h / ch if ch else float("inf")
    factor = max(fx, fy)
    return ResolutionReadout(
        crop_native_w=cw, crop_native_h=ch, out_w=out_w, out_h=out_h,
        upscale_factor=factor, within_tolerance=factor <= 1.0 + tolerance + 1e-9,
    )


# ── ML proposal seeding ──────────────────────────────────────────────────────────

def propose_framing(
    src_w: int,
    src_h: int,
    aspect: AspectPreset,
    *,
    subject_cx: float | None = None,
    subject_cy: float | None = None,
    scale: float = DEFAULT_SCALE,
    subject_y_frac: float | None = None,
) -> FramingModel:
    """Convert today's subject-derived centring into an initial :class:`FramingModel`.

    The tracker/robust-centre + framing-intent (scale / subject_y_frac) become the
    model's *initial* ``{scale, pan_x, pan_y}`` — the ML proposal the box and knobs then
    edit. The conversion reproduces the legacy crop placement exactly:

      - ``pan_x`` = the subject's horizontal centre as a source fraction (``0.5`` when no
        subject — the centred fallback).
      - ``pan_y`` = the source-y at which, after the model clamps the crop, the subject's
        vertical centre lands at ``subject_y_frac`` of the crop height — i.e. the same
        anchor :func:`plan._crop_y` computes. ``None`` centres the crop (legacy).
      - ``scale`` passes through (clamped to ``>= 1.0``).

    Pure: the caller supplies the already-tracked subject centre (median of confident
    detections); this function does no tracking.
    """
    pan_x = (subject_cx / src_w) if (subject_cx is not None and src_w) else DEFAULT_PAN_X
    if subject_y_frac is None or subject_cy is None:
        pan_y = DEFAULT_PAN_Y
    else:
        # legacy _crop_y: y_top = subject_cy - subject_y_frac*crop_h ; crop centre is
        # y_top + crop_h/2, so the source-fraction centre is:
        crop_w, crop_h = scaled_crop_dims(src_w, src_h, aspect, scale)
        centre_px = subject_cy - subject_y_frac * crop_h + crop_h / 2
        pan_y = (centre_px / src_h) if src_h else DEFAULT_PAN_Y
    return FramingModel(scale=scale, pan_x=pan_x, pan_y=pan_y)
