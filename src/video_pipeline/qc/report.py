"""QC data model — rectangles, elements, violations, and the report. Pure.

Phase 4's job is to answer one question for a finished frame layout: *does
anything that must stay inside the safe zone stick out into the danger region,
and does any caption sit on top of the speaker's face?* The validator
(:mod:`video_pipeline.qc.validate`) produces a :class:`QCReport` made of
:class:`Violation` records; the renderer (:mod:`video_pipeline.qc.overlay`)
turns the same geometry into a danger-zone preview.

Coordinate convention matches the safe-zone spec: integer **pixel-edge**
coordinates, origin top-left, x right, y down; a rect ``(x0, y0, x1, y1)`` is
half-open (covers ``x0 <= x < x1`` and ``y0 <= y < y1``). Rects live in the
profile's native frame (e.g. 1080x1920), the same space as the safe-zone spec.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ── geometry ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Rect:
    """A half-open rectangle ``x0 <= x < x1``, ``y0 <= y < y1`` (pixel-edge)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"degenerate rect: {self!r}")

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def intersection(self, other: "Rect") -> Optional["Rect"]:
        """The overlapping rect, or ``None`` if they do not overlap."""
        x0 = max(self.x0, other.x0)
        y0 = max(self.y0, other.y0)
        x1 = min(self.x1, other.x1)
        y1 = min(self.y1, other.y1)
        if x1 <= x0 or y1 <= y0:
            return None
        return Rect(x0, y0, x1, y1)

    def intersection_area(self, other: "Rect") -> float:
        inter = self.intersection(other)
        return inter.area if inter else 0.0

    @classmethod
    def from_xywh(cls, x: float, y: float, width: float, height: float) -> "Rect":
        return cls(x, y, x + width, y + height)

    @classmethod
    def from_tuple(cls, t) -> "Rect":
        return cls(float(t[0]), float(t[1]), float(t[2]), float(t[3]))

    def to_dict(self) -> dict:
        return {
            "x0": round(self.x0, 3),
            "y0": round(self.y0, 3),
            "x1": round(self.x1, 3),
            "y1": round(self.y1, 3),
        }


# Elements that must stay *inside* the safe zone (flagged if they intrude on the
# danger region). FACE is the subject, used for caption-over-face / face-framing
# checks rather than danger-intrusion.
PROTECTED_KINDS = ("caption", "logo", "cta", "text", "graphic")
FACE_KIND = "face"


@dataclass(frozen=True)
class QCElement:
    """One thing on the frame to check.

    ``kind`` is one of :data:`PROTECTED_KINDS` (must stay inside the safe zone)
    or ``"face"`` (a detected subject). ``t`` / ``t_end`` mark the time window for
    time-varying elements (a caption cue's on-screen span, or a sampled face
    detection); static brand marks leave them ``None``.
    """

    kind: str
    rect: Rect
    label: str = ""
    t: Optional[float] = None
    t_end: Optional[float] = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "rect": self.rect.to_dict()}
        if self.label:
            d["label"] = self.label
        if self.t is not None:
            d["t"] = round(self.t, 3)
        if self.t_end is not None:
            d["t_end"] = round(self.t_end, 3)
        return d


# Severities, ordered.
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}


@dataclass(frozen=True)
class Violation:
    """A single QC finding."""

    kind: str            # "danger-intrusion" | "caption-over-face" | "face-in-danger"
    element_kind: str
    rect: Rect
    severity: str = "error"
    label: str = ""
    t: Optional[float] = None
    t_end: Optional[float] = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "kind": self.kind,
            "element_kind": self.element_kind,
            "severity": self.severity,
            "rect": self.rect.to_dict(),
        }
        if self.label:
            d["label"] = self.label
        if self.t is not None:
            d["t"] = round(self.t, 3)
        if self.t_end is not None:
            d["t_end"] = round(self.t_end, 3)
        if self.detail:
            d["detail"] = self.detail
        return d

    def describe(self) -> str:
        where = ""
        if self.t is not None:
            where = f" @ {self.t:.2f}s" if self.t_end is None else f" @ {self.t:.2f}-{self.t_end:.2f}s"
        label = f" '{self.label}'" if self.label else ""
        extra = ""
        if self.kind == "danger-intrusion":
            frac = self.detail.get("danger_frac")
            notch = " (NOTCH)" if self.detail.get("hits_notch") else ""
            if frac is not None:
                extra = f" — {frac:.0%} in danger{notch}"
        elif self.kind == "caption-over-face":
            frac = self.detail.get("overlap_frac")
            if frac is not None:
                extra = f" — {frac:.0%} of caption over a face"
        elif self.kind == "face-in-danger":
            frac = self.detail.get("danger_frac")
            notch = " (NOTCH)" if self.detail.get("hits_notch") else ""
            if frac is not None:
                extra = f" — {frac:.0%} of face in danger{notch}"
        return f"[{self.severity.upper()}] {self.kind} {self.element_kind}{label}{where}{extra}"


@dataclass
class QCReport:
    """Result of a safe-zone QC pass."""

    profile: str
    width: int
    height: int
    safezone_spec: str
    violations: List[Violation] = field(default_factory=list)
    elements_checked: int = 0
    faces_checked: int = 0
    generator_version: str = "qc-1"

    @property
    def passed(self) -> bool:
        """True if no error-level violations were found (warnings/info allowed)."""
        return not any(v.severity == "error" for v in self.violations)

    @property
    def clean(self) -> bool:
        """True if there are no violations of any severity."""
        return len(self.violations) == 0

    def counts_by_kind(self) -> dict:
        out: dict = {}
        for v in self.violations:
            out[v.kind] = out.get(v.kind, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "dimensions": {"width": self.width, "height": self.height},
            "safezone_spec": self.safezone_spec,
            "passed": self.passed,
            "clean": self.clean,
            "elements_checked": self.elements_checked,
            "faces_checked": self.faces_checked,
            "counts_by_kind": self.counts_by_kind(),
            "violations": [v.to_dict() for v in self.violations],
            "generator_version": self.generator_version,
            "coordinate_convention": (
                "integer pixel-edge; origin top-left; x right, y down; "
                "rects are half-open x0<=x<x1, y0<=y<y1"
            ),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    def to_text(self) -> str:
        """Human-readable summary (the printable QC report)."""
        status = "PASS" if self.passed else "FAIL"
        head = (
            f"Safe-zone QC: {status}  profile={self.profile}  "
            f"{self.width}x{self.height}  spec={self.safezone_spec}\n"
            f"  checked: {self.elements_checked} element(s), "
            f"{self.faces_checked} face sample(s)\n"
            f"  violations: {len(self.violations)} "
            f"({self.counts_by_kind() or 'none'})"
        )
        if not self.violations:
            return head + "\n  ✓ everything inside the safe zone; no caption-over-face.\n"
        lines = [head, ""]
        for v in self.violations:
            lines.append("  " + v.describe())
        return "\n".join(lines) + "\n"
