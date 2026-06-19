"""Safe-zone QC runner — daily-driver glue.

Wires the pure QC pieces into the Phase-4 flow:

    rendered frame layout
        captions (caption file / Remotion props)  ─┐
        static brand marks (project.yml qc:)        ├─> validate ─> QC report (JSON + text)
        faces (reframe tracker: OpenCV/MediaPipe)  ─┘
                                                     └─> danger-zone preview (FFmpeg overlay)
    source video ─────────────────────────────────────> clean render (FFmpeg stream-copy)

The validation/geometry core (:mod:`validate`, :mod:`report`) and the overlay
PNG / argv builders (:mod:`overlay`) are pure and unit-tested. Face detection
and the FFmpeg burn-in need native deps + real footage, so the orchestration
here runs on the daily driver (hence ``pragma: no cover``).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List, Optional, Sequence

from ..safezone.spec import SafeZoneSpec
from .overlay import build_clean_command, build_preview_command, render_overlay_png
from .report import QCElement, QCReport, Rect
from .validate import validate


# ── element gathering (pure-ish; no native deps) ───────────────────────────────

def caption_elements_from_props(props: dict) -> List[QCElement]:
    """Caption boxes from a Remotion props object.

    The props' ``safeBox`` is the shared caption box; each kept cue's on-screen
    span gives the time window. One QC element per cue so the report timestamps
    line up with the cues the CEO sees.
    """
    box = props["safeBox"]
    rect = Rect.from_xywh(box["x"], box["y"], box["width"], box["height"])
    fps = props.get("fps", 30)
    out: List[QCElement] = []
    for cue in props.get("cues", []):
        start = cue.get("startSeconds")
        if start is None:
            start = cue["from"] / fps
        end = cue.get("endSeconds")
        if end is None:
            end = (cue["from"] + cue["durationInFrames"]) / fps
        out.append(
            QCElement(
                kind="caption",
                rect=rect,
                label=cue.get("text", "")[:40],
                t=start,
                t_end=end,
            )
        )
    return out


def faces_from_subjects(subjects, frame_w: int, frame_h: int,
                        src_w: int, src_h: int) -> List[QCElement]:  # pragma: no cover
    """Map reframe-tracker ``FrameSubject`` boxes into profile-frame QC elements.

    The tracker runs on source-pixel coordinates; if the source and profile
    frames differ, the caller passes the scale. Subjects without a bbox are
    skipped (no box to check).
    """
    sx = frame_w / src_w if src_w else 1.0
    sy = frame_h / src_h if src_h else 1.0
    out: List[QCElement] = []
    for s in subjects:
        if not s.bbox:
            continue
        x0, y0, x1, y1 = s.bbox
        out.append(
            QCElement(
                kind="face",
                rect=Rect(x0 * sx, y0 * sy, x1 * sx, y1 * sy),
                t=getattr(s, "t", None),
                confidence=getattr(s, "confidence", 1.0),
            )
        )
    return out


# ── daily-driver orchestration ─────────────────────────────────────────────────

def run_qc(  # pragma: no cover - daily-driver orchestration (native deps + footage)
    input_video: str,
    safezone_spec_path: str,
    *,
    props_path: Optional[str] = None,
    extra_elements: Sequence[QCElement] = (),
    report_out: Optional[str] = None,
    preview_out: Optional[str] = None,
    clean_out: Optional[str] = None,
    overlay_png_out: Optional[str] = None,
    detect_faces: bool = True,
    tracker_name: str = "opencv",
    occlusion_frac: float = 0.1,
    face_danger_frac: float = 0.2,
    intrusion_frac: float = 0.0,
    check_caption_over_face: bool = True,
    check_face_in_danger: bool = True,
    dry_run: bool = False,
) -> QCReport:
    """Run the full QC pass; write report + preview + clean render.

    Returns the :class:`QCReport`. With ``dry_run`` the FFmpeg commands are not
    executed (the report is still computed and written).
    """
    spec = SafeZoneSpec.from_json(Path(safezone_spec_path).read_text(encoding="utf-8"))

    elements: List[QCElement] = list(extra_elements)
    if props_path:
        props = json.loads(Path(props_path).read_text(encoding="utf-8"))
        elements += caption_elements_from_props(props)

    faces: List[QCElement] = []
    if detect_faces and (check_caption_over_face or check_face_in_danger):
        faces = _detect_faces(input_video, spec, tracker_name)

    report = validate(
        spec,
        [e for e in elements if e.kind != "face"],
        faces=faces,
        occlusion_frac=occlusion_frac,
        face_danger_frac=face_danger_frac,
        intrusion_frac=intrusion_frac,
        check_caption_over_face=check_caption_over_face,
        check_face_in_danger=check_face_in_danger,
        spec_name=Path(safezone_spec_path).name,
    )

    if report_out:
        Path(report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(report_out).write_text(report.to_json(), encoding="utf-8")

    if preview_out:
        png = overlay_png_out or str(Path(preview_out).with_suffix(".overlay.png"))
        render_overlay_png(
            spec, png, report=report,
            elements=[e for e in elements if e.kind != "face"], faces=faces,
        )
        cmd = build_preview_command(input_video, png, preview_out)
        if not dry_run:
            subprocess.run(cmd, check=True)

    if clean_out:
        cmd = build_clean_command(input_video, clean_out)
        if not dry_run:
            subprocess.run(cmd, check=True)

    return report


def _detect_faces(input_video: str, spec: SafeZoneSpec, tracker_name: str):  # pragma: no cover
    """Run the reframe tracker and map detections to profile-frame face boxes.

    Assumes the input video is already in the profile frame (post-reframe), so
    source and profile dims match; if not, scaling falls out of the bbox sizes.
    """
    from ..reframe.tracker import MediaPipeTracker, OpenCVFaceTracker

    tracker = MediaPipeTracker() if tracker_name == "mediapipe" else OpenCVFaceTracker()
    subjects = tracker.track(input_video)

    import cv2

    cap = cv2.VideoCapture(input_video)
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or spec.image_width
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or spec.image_height
    cap.release()
    return faces_from_subjects(
        subjects, spec.image_width, spec.image_height, src_w, src_h
    )
