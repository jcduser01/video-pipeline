"""Landscape -> portrait auto-reframe.

This is the de-risking probe (brief §3.4): it deletes the After Effects + Media
Encoder round-trip and is the cheapest way to test whether the CEO trusts a
machine-produced layer before the full build.

Three layers, kept separate so the taste-bearing parts stay inspectable:
  - ``tracker``  — subject detection (MediaPipe behind a Protocol seam; a pure
                   synthetic tracker drives the tests with no native deps).
  - ``plan``     — subject centres -> a stabilised crop window (pure, tested).
  - ``crop``     — a crop plan -> an FFmpeg command (pure string build, tested).

``probe`` glues real MediaPipe + FFmpeg and runs on the daily driver (Ono-Sendai)
against real footage — that run is the trust-model acceptance, not a sandbox test.
"""

from .tracker import (
    FrameSubject,
    SubjectTracker,
    FixedTracker,
    OpenCVFaceTracker,
    MediaPipeTracker,
)
from .plan import CropPlan, CropWindow, build_crop_plan
from .crop import ffmpeg_crop_command
from .model import (
    FramingModel,
    CropGeometry,
    ZoomClamp,
    ResolutionReadout,
    model_to_window,
    window_to_model,
    max_zoom,
    clamp_scale,
    resolution_readout,
    propose_framing,
)
from .decision import ReframeDef
from .track_io import write_track, read_track
from .pipeline import (
    propose,
    propose_from_subjects,
    crop_plan_from_def,
    geometry_from_def,
    render_inputs_from_def,
)

__all__ = [
    "FrameSubject",
    "SubjectTracker",
    "FixedTracker",
    "OpenCVFaceTracker",
    "MediaPipeTracker",
    "CropPlan",
    "CropWindow",
    "build_crop_plan",
    "ffmpeg_crop_command",
    # INI-091 Phase 3 — framing model + reframe.def + propose/render split
    "FramingModel",
    "CropGeometry",
    "ZoomClamp",
    "ResolutionReadout",
    "model_to_window",
    "window_to_model",
    "max_zoom",
    "clamp_scale",
    "resolution_readout",
    "propose_framing",
    "ReframeDef",
    "write_track",
    "read_track",
    "propose",
    "propose_from_subjects",
    "crop_plan_from_def",
    "geometry_from_def",
    "render_inputs_from_def",
]
