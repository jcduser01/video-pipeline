"""Propose -> Render split for the reframe (INI-091 Phase 3).

The reframe is two phases around the ``reframe.def`` artifact:

  - **Propose** — run the subject tracker once, persist the track, convert the
    subject-derived centre + framing intent into the canonical framing model, clamp the
    punch-in to the resolution-driven max-zoom, and write ``reframe.def``. (Today's
    dry-run, formalized.)
  - **Render** — read ``reframe.def`` + the persisted track, resolve the framing model
    to the exact pixel crop, and build the crop plan. **No re-tracking, no
    re-derivation** — the geometry Propose wrote is the geometry Render produces.

The *pure* pieces live here and are fully tested: the def write/read, the model→geometry
transform, and the crop-plan construction from the def. The two native seams — the real
subject tracker (MediaPipe/OpenCV, Mac-side) and the ffmpeg render — are injected /
deferred (see :func:`propose` ``tracker`` and the module docstring of
:mod:`video_pipeline.reframe.probe`).
"""

from __future__ import annotations

from statistics import median
from typing import List, Optional, Sequence, Tuple

from ..target_format import Target, aspect_preset
from .decision import (
    DEFAULT_LOCK,
    DEFAULT_REFRAME_MODE,
    ReframeDef,
)
from .framing import FramingIntent
from .model import FramingModel, clamp_scale, model_to_window, propose_framing
from .plan import CropPlan, build_crop_plan
from .tracker import FrameSubject, SubjectTracker
from .track_io import read_track, write_track


# ── helpers ──────────────────────────────────────────────────────────────────────

def _robust_subject_centre(subjects: Sequence[FrameSubject]) -> Tuple[Optional[float], Optional[float]]:
    """Median confident (cx, cy) — the ML proposal's seed (mirrors plan._robust_center).

    Returns ``(None, None)`` when there is no confident detection, so the proposer falls
    back to a centred crop.
    """
    conf = [s for s in subjects if s.confidence > 0]
    if not conf:
        return None, None
    return float(median([s.cx for s in conf])), float(median([s.cy for s in conf]))


# ── Propose ──────────────────────────────────────────────────────────────────────

def propose_from_subjects(
    source: str,
    subjects: Sequence[FrameSubject],
    src_w: int,
    src_h: int,
    target: Target,
    *,
    out_w: int,
    out_h: int,
    framing: Optional[FramingIntent] = None,
    mode: str = DEFAULT_REFRAME_MODE,
    lock: str = DEFAULT_LOCK,
    safe_zone_mode: Optional[str] = None,
    subject_track: Optional[str] = None,
    allow_upscale: bool = False,
    duration: Optional[float] = None,
) -> ReframeDef:
    """Build a ``reframe.def`` from an already-tracked subject list (pure).

    The subject-derived centre + the ``framing`` intent (scale / subject_y_frac) become
    the canonical framing model via :func:`propose_framing`; the punch-in is clamped to
    the resolution-driven max-zoom (hard-stop unless ``allow_upscale``). ``out_w/out_h``
    are the resolved target output dims (the caller resolves them from ``target`` against
    the source). This is the dry-run, formalized — no tracking, no render here.
    """
    preset = aspect_preset(target.aspect)
    scale = framing.subject_scale if framing else 1.0
    subject_y_frac = framing.subject_y_frac if framing else None
    cx, cy = _robust_subject_centre(subjects)

    # Clamp the requested punch-in to what the target resolution can afford.
    clamp = clamp_scale(
        scale, src_w, src_h, preset, out_w, out_h, allow_upscale=allow_upscale
    )
    model = propose_framing(
        src_w, src_h, preset,
        subject_cx=cx, subject_cy=cy,
        scale=clamp.scale, subject_y_frac=subject_y_frac,
    )
    kwargs = {}
    if safe_zone_mode is not None:
        kwargs["safe_zone_mode"] = safe_zone_mode
    return ReframeDef(
        source=source,
        target=target,
        framing=model,
        mode=mode,
        lock=lock,
        framing_intent=(framing.key if framing else None),
        custom=False,
        subject_track=subject_track,
        proposal=model,
        duration=duration,
        **kwargs,
    )


def propose(
    source: str,
    subjects: Sequence[FrameSubject],
    src_w: int,
    src_h: int,
    target: Target,
    *,
    out_w: int,
    out_h: int,
    def_path: str,
    track_path: str,
    framing: Optional[FramingIntent] = None,
    mode: str = DEFAULT_REFRAME_MODE,
    lock: str = DEFAULT_LOCK,
    safe_zone_mode: Optional[str] = None,
    allow_upscale: bool = False,
    duration: Optional[float] = None,
    tracker_name: Optional[str] = None,
) -> ReframeDef:
    """Propose: persist the subject track + write ``reframe.def`` to disk.

    Writes the track to ``track_path`` and the decision file to ``def_path`` (with the
    def referencing the track). Returns the written :class:`ReframeDef`. The native
    tracker run that produced ``subjects`` happens upstream (Mac-side); pass its output
    in. Render later reads exactly these two files.
    """
    write_track(track_path, subjects, src_w=src_w, src_h=src_h, tracker_name=tracker_name)
    rdef = propose_from_subjects(
        source, subjects, src_w, src_h, target,
        out_w=out_w, out_h=out_h, framing=framing, mode=mode, lock=lock,
        safe_zone_mode=safe_zone_mode, subject_track=track_path,
        allow_upscale=allow_upscale, duration=duration,
    )
    rdef.write(def_path)
    return rdef


# ── Render (pure pieces) ─────────────────────────────────────────────────────────

def geometry_from_def(rdef: ReframeDef, src_w: int, src_h: int):
    """Resolve a def's framing model to the exact pixel crop window (no re-derivation).

    The headline DoD: this reproduces the model's crop exactly — the geometry the def
    carries is the geometry rendered.
    """
    preset = aspect_preset(rdef.target.aspect)
    return model_to_window(rdef.framing, src_w, src_h, preset)


def crop_plan_from_def(
    rdef: ReframeDef,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    *,
    subjects: Optional[Sequence[FrameSubject]] = None,
) -> CropPlan:
    """Build the crop plan from a ``reframe.def`` — Render's pure core.

    The def's framing model pins the crop centre (``pan_x``/``pan_y``) and punch-in
    (``scale``). For a **static** def (or a dynamic def with no lock) the framing is the
    whole geometry: :func:`build_crop_plan` consumes ``pan_x``/``pan_y`` as the manual-pan
    override so the plan reproduces :func:`geometry_from_def` exactly, and ``subjects``
    (the persisted track) is passed through for occupancy/diagnostics only — it does NOT
    move the crop.

    For a **dynamic def with a composition lock** (INI-091 Phase 5) the framing model's
    ``pan_x``/``pan_y`` are the *set box* (the relative-placement anchor) and the
    persisted ``subjects`` track drives the follow on the locked axis/axes. The def's
    geometry is still authoritative — the box and the track are exactly what Propose
    captured, so Render is deterministic.
    """
    subs = list(subjects) if subjects is not None else []
    if rdef.mode == "dynamic" and rdef.lock in ("x", "y", "both"):
        return build_crop_plan(
            subs, src_w, src_h, out_w=out_w, out_h=out_h,
            mode="dynamic", lock=rdef.lock, duration=rdef.duration,
            scale=rdef.framing.scale,
            pan_x=rdef.framing.pan_x,
            pan_y=rdef.framing.pan_y,
        )
    return build_crop_plan(
        subs, src_w, src_h, out_w=out_w, out_h=out_h,
        mode="static", duration=rdef.duration,
        scale=rdef.framing.scale,
        pan_x=rdef.framing.pan_x,
        pan_y=rdef.framing.pan_y,
    )


def render_inputs_from_def(
    def_path: str,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
) -> Tuple[ReframeDef, List[FrameSubject], CropPlan]:
    """Read ``reframe.def`` + its subject track and build the crop plan (Render core).

    Returns ``(reframe_def, subjects, crop_plan)``. Reading the def and replaying its
    geometry needs no tracker and no ffmpeg — those native steps (tracking on Propose,
    encoding on Render) sit at the seams documented in the module docstring. The single
    crop window this returns is what the ffmpeg command (``crop.ffmpeg_crop_command``)
    then renders.
    """
    rdef = ReframeDef.read(def_path)
    subjects = read_track(rdef.subject_track) if rdef.subject_track else []
    plan = crop_plan_from_def(rdef, src_w, src_h, out_w, out_h, subjects=subjects)
    return rdef, subjects, plan
