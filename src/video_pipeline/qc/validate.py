"""Safe-zone QC validation — the pure, unit-tested core.

Given a :class:`~video_pipeline.safezone.spec.SafeZoneSpec` and the frame's
elements (captions, logos, CTAs, detected faces), produce a
:class:`~video_pipeline.qc.report.QCReport`. Three checks:

  - **danger-intrusion** (error) — a protected element (caption/logo/CTA/text)
    pokes out of the safe polygon into the danger region, *including the
    lower-right action-button notch*. This is the headline DoD check.
  - **caption-over-face** (warning) — a caption box overlaps a detected face by
    more than ``occlusion_frac`` of the caption's area (the subject-aware check;
    faces come from the reframe tracker seam — OpenCV/MediaPipe).
  - **face-in-danger** (warning) — the speaker's face has drifted into the danger
    region (e.g. the reframe crop pushed them under the action buttons).

All geometry is exact against the spec's row-convex bands, so the notch is
honoured natively — no rectangle approximation.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from ..safezone.spec import SafeZoneSpec
from .report import (
    FACE_KIND,
    PROTECTED_KINDS,
    QCElement,
    QCReport,
    Rect,
    Violation,
)

_EPS = 1e-9


def _safe_intervals_at_row(spec: SafeZoneSpec, y: int) -> List[Tuple[int, int]]:
    """Safe x-intervals [x0, x1) at integer row ``y`` (union over covering bands)."""
    raw = sorted(
        (b.x0, b.x1) for b in spec.bands if b.y0 <= y < b.y1
    )
    if not raw:
        return []
    merged: List[Tuple[int, int]] = [raw[0]]
    for x0, x1 in raw[1:]:
        lx0, lx1 = merged[-1]
        if x0 <= lx1:
            merged[-1] = (lx0, max(lx1, x1))
        else:
            merged.append((x0, x1))
    return merged


def _overlap_len(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def safe_area_of_rect(spec: SafeZoneSpec, rect: Rect) -> float:
    """Area of ``rect`` that lies inside the safe region (fractional, exact).

    Integrates per integer row: each row contributes its vertical overlap with
    the rect times the horizontal overlap of the rect's x-span with the row's
    safe intervals. Handles fractional rect edges and the notch.
    """
    if rect.area <= 0:
        return 0.0
    import math

    iy0, iy1 = int(math.floor(rect.y0)), int(math.ceil(rect.y1))
    total = 0.0
    for y in range(iy0, iy1):
        v = _overlap_len(rect.y0, rect.y1, y, y + 1)  # vertical weight of this row
        if v <= 0:
            continue
        row_cov = 0.0
        for sx0, sx1 in _safe_intervals_at_row(spec, y):
            row_cov += _overlap_len(rect.x0, rect.x1, sx0, sx1)
        total += v * row_cov
    return total


def danger_overlap(spec: SafeZoneSpec, rect: Rect) -> Tuple[float, bool]:
    """Return ``(danger_fraction, hits_notch)`` for ``rect``.

    ``danger_fraction`` is the fraction of the rect's area outside the safe
    region (0.0 = fully clear). ``hits_notch`` is True if the rect intersects any
    of the spec's carved notch rectangles.
    """
    if rect.area <= 0:
        return 0.0, False
    safe = safe_area_of_rect(spec, rect)
    danger_frac = max(0.0, 1.0 - safe / rect.area)
    hits_notch = any(
        rect.intersection(Rect.from_tuple(n)) is not None for n in spec.notch_rects
    )
    return danger_frac, hits_notch


def _time_overlaps(
    a_t: Optional[float], a_te: Optional[float],
    b_t: Optional[float], b_te: Optional[float],
) -> bool:
    """True if two (possibly open-ended / always-on) time windows overlap."""
    if a_t is None or b_t is None:
        return True  # an always-on element overlaps everything
    a_end = a_te if a_te is not None else a_t
    b_end = b_te if b_te is not None else b_t
    return a_t <= b_end and b_t <= a_end


def validate(
    spec: SafeZoneSpec,
    elements: Sequence[QCElement],
    faces: Sequence[QCElement] = (),
    *,
    occlusion_frac: float = 0.10,
    face_danger_frac: float = 0.20,
    intrusion_frac: float = 0.0,
    check_caption_over_face: bool = True,
    check_face_in_danger: bool = True,
    profile: Optional[str] = None,
    spec_name: str = "",
) -> QCReport:
    """Validate a frame layout against the safe-zone spec.

    ``elements`` are protected items (must stay inside the safe zone). ``faces``
    are detected subject boxes (kind ``"face"``). ``intrusion_frac`` is the
    danger fraction above which a protected element is flagged (default 0 = any
    intrusion). ``occlusion_frac`` is the caption-over-face overlap threshold;
    ``face_danger_frac`` the face-in-danger threshold.
    """
    violations: List[Violation] = []

    for el in elements:
        if el.kind == FACE_KIND:
            raise ValueError("faces must be passed via the `faces` argument, not `elements`")
        if el.kind not in PROTECTED_KINDS:
            raise ValueError(
                f"unknown protected element kind {el.kind!r}; expected one of {PROTECTED_KINDS}"
            )
        danger_frac, hits_notch = danger_overlap(spec, el.rect)
        if danger_frac > intrusion_frac + _EPS:
            violations.append(
                Violation(
                    kind="danger-intrusion",
                    element_kind=el.kind,
                    rect=el.rect,
                    severity="error",
                    label=el.label,
                    t=el.t,
                    t_end=el.t_end,
                    detail={
                        "danger_frac": round(danger_frac, 4),
                        "hits_notch": hits_notch,
                    },
                )
            )

    if check_caption_over_face and faces:
        captions = [e for e in elements if e.kind == "caption"]
        for cap in captions:
            best_frac = 0.0
            best_face: Optional[QCElement] = None
            for face in faces:
                if not _time_overlaps(cap.t, cap.t_end, face.t, face.t_end):
                    continue
                inter = cap.rect.intersection_area(face.rect)
                if cap.rect.area <= 0:
                    continue
                frac = inter / cap.rect.area
                if frac > best_frac:
                    best_frac, best_face = frac, face
            if best_face is not None and best_frac >= occlusion_frac:
                violations.append(
                    Violation(
                        kind="caption-over-face",
                        element_kind="caption",
                        rect=cap.rect,
                        severity="warning",
                        label=cap.label,
                        t=cap.t,
                        t_end=cap.t_end,
                        detail={
                            "overlap_frac": round(best_frac, 4),
                            "face_rect": best_face.rect.to_dict(),
                            "face_t": best_face.t,
                        },
                    )
                )

    if check_face_in_danger:
        for face in faces:
            danger_frac, hits_notch = danger_overlap(spec, face.rect)
            if danger_frac >= face_danger_frac:
                violations.append(
                    Violation(
                        kind="face-in-danger",
                        element_kind="face",
                        rect=face.rect,
                        severity="warning",
                        label=face.label,
                        t=face.t,
                        t_end=face.t_end,
                        detail={
                            "danger_frac": round(danger_frac, 4),
                            "hits_notch": hits_notch,
                        },
                    )
                )

    return QCReport(
        profile=profile or spec.profile,
        width=spec.image_width,
        height=spec.image_height,
        safezone_spec=spec_name,
        violations=violations,
        elements_checked=len(elements),
        faces_checked=len(faces),
    )
