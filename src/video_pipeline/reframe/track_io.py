"""Subject-track persistence — the tracker output as a referenced artifact (INI-091 P3).

Propose runs the (native, Mac-side) subject tracker once and persists its per-frame
``FrameSubject`` list to a file. The ``reframe.def`` decision file references that file,
so **Render consumes the track without re-tracking** — the expensive native step runs
once, the pure geometry replays from disk.

The on-disk form is plain JSON (stdlib only, human-inspectable). A serialize/parse
round-trip is the testable contract here; the actual tracking is native and lives behind
:class:`~video_pipeline.reframe.tracker.SubjectTracker`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Sequence

from .tracker import FrameSubject

TRACK_VERSION = "1.0"
TRACK_KIND = "subject-track"


def _bbox_to_list(bbox) -> Optional[list]:
    if bbox is None:
        return None
    return [float(v) for v in bbox]


def _bbox_from_list(v) -> Optional[tuple]:
    if v is None:
        return None
    return tuple(float(x) for x in v)


def track_to_dict(
    subjects: Sequence[FrameSubject],
    src_w: Optional[int] = None,
    src_h: Optional[int] = None,
    tracker_name: Optional[str] = None,
) -> dict:
    """A JSON-serializable mapping of a subject track.

    ``src_w``/``src_h`` record the source dims the centres are in (so a consumer can
    sanity-check the geometry), and ``tracker_name`` notes which detector produced it.
    """
    return {
        "kind": TRACK_KIND,
        "version": TRACK_VERSION,
        "src_w": src_w,
        "src_h": src_h,
        "tracker": tracker_name,
        "count": len(subjects),
        "subjects": [
            {
                "t": round(float(s.t), 6),
                "cx": round(float(s.cx), 4),
                "cy": round(float(s.cy), 4),
                "bbox": _bbox_to_list(s.bbox),
                "confidence": round(float(s.confidence), 4),
            }
            for s in subjects
        ],
    }


def track_from_dict(d: dict) -> List[FrameSubject]:
    """Parse a subject track back to a ``FrameSubject`` list (the Render input)."""
    return [
        FrameSubject(
            t=float(s["t"]),
            cx=float(s["cx"]),
            cy=float(s["cy"]),
            bbox=_bbox_from_list(s.get("bbox")),
            confidence=float(s.get("confidence", 1.0)),
        )
        for s in (d.get("subjects") or [])
    ]


def write_track(
    path,
    subjects: Sequence[FrameSubject],
    src_w: Optional[int] = None,
    src_h: Optional[int] = None,
    tracker_name: Optional[str] = None,
) -> None:
    """Persist a subject track to ``path`` as JSON (Propose side)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = track_to_dict(subjects, src_w=src_w, src_h=src_h, tracker_name=tracker_name)
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_track(path) -> List[FrameSubject]:
    """Load a subject track from ``path`` (Render side)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return track_from_dict(data)
