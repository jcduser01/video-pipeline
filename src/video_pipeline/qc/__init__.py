"""Safe-zone QC — the Phase-4 quality gate.

Checks a finished frame layout against the derived safe-zone polygon (notch
included) and the speaker's face, then produces three outputs:

  - a **QC report** (machine-readable JSON + a printable summary) flagging any
    caption/logo/CTA/text that intrudes on the danger region, any caption sitting
    on a face, and any face that drifted into the danger zone;
  - a **danger-zone preview** (the source with the danger region + flagged boxes
    burned in) for an eyeball check;
  - a **clean render** (the deliverable, untouched).

The geometry/validation core (:mod:`report`, :mod:`validate`) is pure and
unit-tested. The pixel/preview rendering (:mod:`overlay`) builds a PIL danger
mask plus FFmpeg argv; the actual burn-in runs on the daily driver via
:mod:`runner`.
"""

from .report import (
    QCElement,
    QCReport,
    Rect,
    Violation,
    PROTECTED_KINDS,
    FACE_KIND,
)
from .validate import validate, danger_overlap, safe_area_of_rect

__all__ = [
    "QCElement",
    "QCReport",
    "Rect",
    "Violation",
    "PROTECTED_KINDS",
    "FACE_KIND",
    "validate",
    "danger_overlap",
    "safe_area_of_rect",
]
