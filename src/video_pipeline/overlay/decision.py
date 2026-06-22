"""The overlay file — the product of the overlay phase (INI-089 Phase A).

The overlay-decision file *is the deliverable*; the composited overlay layer is a
regenerable render of it. It is a human-editable YAML document: a flat, ordered
list of overlays, each with a source-time window ``[start, end)``, a placement, a
trivial transition (cut or fade), and per-kind options. This mirrors the rough-cut
and caption decision files exactly: edit text, re-render.

To change an overlay the CEO nudges ``start`` / ``end`` (the window the overlay is
on screen), flips ``placement``, swaps ``src``, or switches ``transition`` to
``fade`` — then re-renders. The edit round-trips: :meth:`OverlayList.from_yaml`
parses exactly what :meth:`OverlayList.to_yaml` writes, so a hand-edited file loads
back losslessly. The pipeline never modifies ``source/``; the overlay file lives in
``work/`` and the composite in ``review/``.

Coordinate / time convention
----------------------------
``start`` / ``end`` are **source-time** seconds (the same timebase as the caption
cues and the rough-cut segments), so the transcript→window proposer and the
cut-time remap at editor handoff both apply unchanged. ``rect`` (when present) is
in the profile's native pixel frame (top-left origin, x right, y down), half-open
like the safe-zone spec.

Example (abridged)::

    source: 2026-06-03-reel.mp4
    profile: reels-9x16
    segments:
      - {i: 0, kind: image, src: assets/chart.png, start: 3.20, end: 7.80, placement: bottom-half, transition: fade, fade: 0.30, audio: keep, scale: fit, matte: none, rect: null, text: "the Q3 chart"}
      - {i: 1, kind: video, src: assets/clip.mov,  start: 12.0, end: 18.5, placement: pip-rect,    transition: cut,  fade: 0.0,  audio: duck, scale: fill, matte: none, rect: {x: 60, y: 1180, w: 420, h: 560}, text: "the demo clip"}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import yaml

# Vocabulary (kept here so the schema/CLI and the GUI dropdowns read one source).
KINDS = ("image", "video", "card")
PLACEMENTS = ("full-bleed", "bottom-half", "pip-rect")
TRANSITIONS = ("cut", "fade")
AUDIO_MODES = ("keep", "duck", "mute")
SCALE_MODES = ("fit", "fill")
MATTE_MODES = ("none", "selfieseg")  # Phase C; "none" is the Phase-A default.


def _round(t: float) -> float:
    return round(float(t), 3)


@dataclass
class OverlayItem:
    """One timed/placed overlay over the base clip.

    ``kind`` selects the producer (``image`` / ``video`` now; ``card`` in Phase B).
    ``src`` is the asset path (or, for a card, its content-JSON path). ``start`` /
    ``end`` bound the source-time window the overlay is on screen. ``placement`` is
    ``full-bleed`` / ``bottom-half`` / ``pip-rect``; ``rect`` carries the explicit
    PiP rectangle (``x, y, w, h``) when ``placement == 'pip-rect'`` (``None``
    otherwise — the geometry is derived from the placement keyword). ``transition``
    is ``cut`` or ``fade`` (with ``fade`` seconds); richer in/out animation stays in
    the NLE by design. ``audio`` (video only) is ``keep`` / ``duck`` / ``mute``.
    ``matte`` is the Phase-C self-composite toggle (``none`` default).
    """

    index: int
    kind: str
    src: str
    start: float
    end: float
    placement: str = "full-bleed"
    rect: Optional[Tuple[int, int, int, int]] = None
    transition: str = "cut"
    fade: float = 0.0
    audio: str = "keep"
    scale: str = "fit"
    matte: str = "none"
    text: str = ""

    def __post_init__(self):
        self.start = _round(self.start)
        self.end = _round(self.end)
        self.fade = _round(self.fade)
        if self.kind not in KINDS:
            raise ValueError(f"kind {self.kind!r} not in {KINDS}")
        if self.placement not in PLACEMENTS:
            raise ValueError(f"placement {self.placement!r} not in {PLACEMENTS}")
        if self.transition not in TRANSITIONS:
            raise ValueError(f"transition {self.transition!r} not in {TRANSITIONS}")
        if self.audio not in AUDIO_MODES:
            raise ValueError(f"audio {self.audio!r} not in {AUDIO_MODES}")
        if self.scale not in SCALE_MODES:
            raise ValueError(f"scale {self.scale!r} not in {SCALE_MODES}")
        if self.matte not in MATTE_MODES:
            raise ValueError(f"matte {self.matte!r} not in {MATTE_MODES}")
        if self.end <= self.start:
            raise ValueError(
                f"overlay window must be positive: start={self.start} end={self.end}"
            )
        if self.transition == "cut":
            # A cut has no fade; normalize so the file and the renderer agree.
            self.fade = 0.0
        elif self.fade <= 0.0:
            raise ValueError("transition 'fade' needs fade > 0 (seconds)")
        elif self.fade * 2 > self.duration:
            raise ValueError(
                f"fade {self.fade}s too long for a {self.duration}s window "
                "(in+out fades would overlap)"
            )
        if self.placement == "pip-rect":
            if self.rect is None:
                raise ValueError("placement 'pip-rect' requires a rect {x, y, w, h}")
            self.rect = tuple(int(v) for v in self.rect)  # type: ignore[assignment]
            if len(self.rect) != 4:
                raise ValueError("rect must be (x, y, w, h)")
            if self.rect[2] <= 0 or self.rect[3] <= 0:
                raise ValueError("rect width/height must be positive")
        elif self.rect is not None:
            # A keyword placement derives its own geometry; carrying a rect would be
            # ambiguous (which wins?). Reject rather than silently ignore it.
            raise ValueError(
                f"rect is only valid with placement 'pip-rect' (got {self.placement!r})"
            )

    @property
    def duration(self) -> float:
        return _round(max(0.0, self.end - self.start))

    def to_dict(self) -> dict:
        # compact, key-ordered mapping (renders inline as a flow-style row)
        return {
            "i": self.index,
            "kind": self.kind,
            "src": self.src,
            "start": self.start,
            "end": self.end,
            "placement": self.placement,
            "transition": self.transition,
            "fade": self.fade,
            "audio": self.audio,
            "scale": self.scale,
            "matte": self.matte,
            "rect": (
                {"x": self.rect[0], "y": self.rect[1], "w": self.rect[2], "h": self.rect[3]}
                if self.rect is not None
                else None
            ),
            "text": self.text,
        }

    @staticmethod
    def _parse_rect(v) -> Optional[Tuple[int, int, int, int]]:
        if v is None:
            return None
        if isinstance(v, dict):
            return (int(v["x"]), int(v["y"]), int(v["w"]), int(v["h"]))
        return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))

    @classmethod
    def from_dict(cls, d: dict) -> "OverlayItem":
        return cls(
            index=int(d.get("i", d.get("index", 0))),
            kind=str(d["kind"]),
            src=str(d.get("src", "") or ""),
            start=float(d["start"]),
            end=float(d["end"]),
            placement=str(d.get("placement", "full-bleed")),
            rect=cls._parse_rect(d.get("rect")),
            transition=str(d.get("transition", "cut")),
            fade=float(d.get("fade", 0.0) or 0.0),
            audio=str(d.get("audio", "keep")),
            scale=str(d.get("scale", "fit")),
            matte=str(d.get("matte", "none")),
            text=str(d.get("text", "") or ""),
        )


_HEADER = (
    "# overlay file — generated by video_pipeline.overlay (INI-089 Phase A).\n"
    "# THIS FILE IS THE PRODUCT. The composited overlay layer is a regenerable\n"
    "# render of it. Edit an overlay by nudging start/end (the on-screen window),\n"
    "# flipping placement, swapping src, or switching transition to fade — then\n"
    "# re-render the composite. start/end are SOURCE-time seconds (same timebase as\n"
    "# the captions); the cut-time remap is applied at editor handoff.\n"
    "# placement: full-bleed | bottom-half | pip-rect (rect required, profile px).\n"
    "# transition: cut | fade (fade seconds).  audio (video only): keep | duck | mute.\n"
    "# source/ is never modified.\n"
)


@dataclass
class OverlayList:
    """An ordered overlay-decision document over a single base clip."""

    source: str
    segments: List[OverlayItem] = None  # type: ignore[assignment]
    profile: Optional[str] = None
    duration: Optional[float] = None

    def __post_init__(self):
        if self.segments is None:
            self.segments = []

    # ── views ────────────────────────────────────────────────────────────────

    def active_at(self, t: float) -> List[OverlayItem]:
        """Overlays whose source-time window contains ``t`` (half-open)."""
        return [s for s in self.segments if s.start <= t < s.end]

    def source_duration(self) -> float:
        if self.duration is not None:
            return _round(self.duration)
        return _round(max((s.end for s in self.segments), default=0.0))

    def reindex(self) -> "OverlayList":
        """Renumber overlays 0..n-1 in window order (after a hand edit)."""
        self.segments.sort(key=lambda s: (s.start, s.end))
        for i, s in enumerate(self.segments):
            s.index = i
        return self

    # ── serialization ──────────────────────────────────────────────────────────

    def to_yaml(self) -> str:
        head = {
            "source": self.source,
            "profile": self.profile,
            "duration": _round(self.source_duration()),
            "count": len(self.segments),
        }
        head_yaml = yaml.safe_dump(
            head, sort_keys=False, allow_unicode=True, default_flow_style=False
        )
        # One overlay per line in flow style so the file reads like a script and a
        # window nudge or placement flip is a single-line edit.
        lines = ["segments:"]
        for s in self.segments:
            row = yaml.safe_dump(
                s.to_dict(), sort_keys=False, allow_unicode=True,
                default_flow_style=True, width=10_000,
            ).strip()
            lines.append(f"  - {row}")
        return _HEADER + head_yaml + "\n".join(lines) + "\n"

    @classmethod
    def from_yaml(cls, text: str) -> "OverlayList":
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise ValueError("overlay file did not parse to a mapping")
        segments = [OverlayItem.from_dict(d) for d in (data.get("segments") or [])]
        return cls(
            source=str(data.get("source", "")),
            segments=segments,
            profile=data.get("profile"),
            duration=(float(data["duration"]) if data.get("duration") is not None else None),
        )

    def write(self, path) -> None:
        from pathlib import Path

        Path(path).write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def read(cls, path) -> "OverlayList":
        from pathlib import Path

        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))
