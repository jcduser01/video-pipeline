"""Subject occupancy — project the tracked subject into the reframed frame (INI-090 C).

The reframe already knows two things the caption layer needs but never received: the
subject's bounding box (per sampled frame, from the tracker) and the crop transform
that maps source pixels into the target frame. This module composes them into
``avoid_windows`` — the exact ``(x, y, w, h, start, end)`` shape the caption exporter
already consumes for overlay occupancy (INI-089) — so captions dodge the *subject* the
same way they dodge overlays. No new caption machinery; one more producer on the
existing seam.

For a static plan this is one window over the whole clip (the subject sits still in the
reframed frame). For a dynamic plan the crop pans, so occupancy is emitted per keyframe
segment using that segment's crop transform.
"""

from __future__ import annotations

import json
from statistics import median
from typing import List, Optional, Sequence, Tuple

from .plan import CropPlan
from .tracker import FrameSubject

# (x, y, w, h, start, end) in target/profile pixel space + seconds.
AvoidWindow = Tuple[int, int, int, int, float, float]


def _project_rect(bx0, by0, bx1, by1, cw, out_w, out_h):
    """Map a source-pixel rect through one crop window into profile pixels.

    Returns ``(x, y, w, h)`` clamped to the frame, or ``None`` if the rect lands
    fully outside the crop (zero/negative area after clamping).
    """
    sx = out_w / cw.w
    sy = out_h / cw.h
    px0 = (bx0 - cw.x) * sx
    px1 = (bx1 - cw.x) * sx
    py0 = (by0 - cw.y) * sy
    py1 = (by1 - cw.y) * sy
    px0 = max(0.0, min(px0, out_w))
    px1 = max(0.0, min(px1, out_w))
    py0 = max(0.0, min(py0, out_h))
    py1 = max(0.0, min(py1, out_h))
    w = int(round(px1 - px0))
    h = int(round(py1 - py0))
    if w <= 0 or h <= 0:
        return None
    return int(round(px0)), int(round(py0)), w, h


def subject_occupancy_windows(
    plan: CropPlan,
    subjects: Sequence[FrameSubject],
    pad_frac: float = 0.0,
) -> List[AvoidWindow]:
    """Subject footprint(s) in the reframed frame, as caption ``avoid_windows``.

    Uses the median confident bbox per crop segment (robust to per-frame jitter and
    detection gaps). ``pad_frac`` optionally inflates the box (e.g. 0.1 = +10% each
    side) to keep captions a margin clear of the subject. Subjects without a bbox are
    ignored (e.g. test/fallback trackers) — occupancy is then simply empty.
    """
    multi = len(plan.windows) > 1
    out: List[AvoidWindow] = []
    for cw in plan.windows:
        if multi:
            seg = [s for s in subjects
                   if s.bbox and s.confidence > 0 and cw.t_start <= s.t <= cw.t_end]
        else:
            seg = [s for s in subjects if s.bbox and s.confidence > 0]
        if not seg:
            continue
        bx0 = median([s.bbox[0] for s in seg])
        by0 = median([s.bbox[1] for s in seg])
        bx1 = median([s.bbox[2] for s in seg])
        by1 = median([s.bbox[3] for s in seg])
        if pad_frac:
            dw = (bx1 - bx0) * pad_frac
            dh = (by1 - by0) * pad_frac
            bx0, bx1, by0, by1 = bx0 - dw, bx1 + dw, by0 - dh, by1 + dh
        rect = _project_rect(bx0, by0, bx1, by1, cw, plan.out_w, plan.out_h)
        if rect is None:
            continue
        out.append((rect[0], rect[1], rect[2], rect[3], cw.t_start, cw.t_end))
    return out


def write_occupancy(
    path: str,
    windows: Sequence[AvoidWindow],
    frame_w: Optional[int] = None,
    frame_h: Optional[int] = None,
    caption_position: Optional[str] = None,
) -> None:
    """Persist subject occupancy as JSON (mirrors the overlay.occupancy artifact).

    ``frame_w/frame_h`` record the coordinate space the windows live in (the reframe
    out dims) so the caption layer can rescale them onto its safe-zone spec, which may
    be a different resolution tier of the same aspect. ``caption_position`` carries the
    framing intent's paired anchor as a hint.
    """
    payload = {
        "kind": "subject",
        "frame_w": frame_w,
        "frame_h": frame_h,
        "caption_position": caption_position,
        "avoid_windows": [
            {"x": x, "y": y, "w": w, "h": h, "start": s, "end": e}
            for (x, y, w, h, s, e) in windows
        ],
    }
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_occupancy(path: str, to_w: Optional[int] = None, to_h: Optional[int] = None) -> List[AvoidWindow]:
    """Load avoid_windows from a subject/overlay occupancy JSON file.

    If ``to_w/to_h`` are given and the file records its own ``frame_w/frame_h``, the
    windows are uniformly rescaled into the ``to_w × to_h`` space (same aspect, so a
    single scale factor) — letting captions consume occupancy emitted at a different
    resolution tier.
    """
    from pathlib import Path
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    wins = [
        (w["x"], w["y"], w["w"], w["h"], w["start"], w["end"])
        for w in data.get("avoid_windows", [])
    ]
    fw, fh = data.get("frame_w"), data.get("frame_h")
    if to_w and to_h and fw and fh and (fw, fh) != (to_w, to_h):
        wins = rescale_windows(wins, fw, fh, to_w, to_h)
    return wins


def rescale_windows(windows, from_w, from_h, to_w, to_h) -> List[AvoidWindow]:
    """Uniformly rescale avoid_windows from one frame size to another (same aspect)."""
    sx = to_w / from_w
    sy = to_h / from_h
    return [
        (int(round(x * sx)), int(round(y * sy)), int(round(w * sx)), int(round(h * sy)), s, e)
        for (x, y, w, h, s, e) in windows
    ]
