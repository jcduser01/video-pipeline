"""Normalized (resolution-independent) safe-zone model — INI-091 Phase 2.

Phase 1 (``target_format.py``) made the project's *Target* (aspect + resolution)
a first-class, resolution-independent choice. This module makes the **safe zone**
match: instead of baking pixels per profile (the legacy :class:`SafeZoneSpec`),
a zone is stored as **vector geometry in normalized 0–1 coordinates** and resolved
to pixels only once the target width/height are known. So 1080×1920, 1440×2560 and
2160×3840 of one aspect all share a single proportional zone.

Three **modes** (resolution-independent):

  - ``none``    — the full frame is safe; nothing is avoided.
  - ``generic`` — a built-in asymmetric inset rectangle, per-aspect, the DEFAULT.
  - ``custom``  — the user's PNG → polygon (notch-aware), normalized to its canvas
                  and re-resolvable at any target size. Aspect-bound (resets with
                  the aspect, just like the reframe crop geometry).

Two **purposes** are carried, mirroring how the rest of the pipeline reads the
zone:

  - ``subject`` — faces / bodies / products. Tolerates the edge more; the reframe
                  uses this to keep the subject composed.
  - ``text``    — captions / logos / CTAs. Stricter; captions + QC resolve against
                  this so on-screen text never lands in a danger band.

The resolution step (:meth:`NormalizedSafeZone.resolve`) returns an ordinary
:class:`~video_pipeline.safezone.spec.SafeZoneSpec` for the requested purpose, so
every existing pixel consumer (caption placement, QC) keeps working unchanged —
it just receives a spec resolved *now* for the live target instead of one baked
into a file. Pure; numpy/Pillow are only needed for the PNG ingest path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .spec import Band, SafeZoneSpec

NORMALIZED_VERSION = "1.0"

# ── modes ───────────────────────────────────────────────────────────────────────

MODE_NONE = "none"
MODE_GENERIC = "generic"
MODE_CUSTOM = "custom"
SAFE_ZONE_MODES: Tuple[str, ...] = (MODE_NONE, MODE_GENERIC, MODE_CUSTOM)
DEFAULT_MODE = MODE_GENERIC  # INI-091 locked default

# ── purposes ────────────────────────────────────────────────────────────────────

PURPOSE_SUBJECT = "subject"
PURPOSE_TEXT = "text"
PURPOSES: Tuple[str, ...] = (PURPOSE_SUBJECT, PURPOSE_TEXT)


# ── generic per-aspect inset tables (CEO-locked, INI-091, 2026-06-22) ────────────
#
# Insets are fractions of the frame dimension, asymmetric (Top, Right, Bottom,
# Left). Bottom is the largest on portrait formats (UI chrome / action buttons
# cluster low); text-safe is uniformly stricter than subject-safe.

# Generic subject-safe insets: aspect_key -> (top, right, bottom, left).
GENERIC_SUBJECT_INSETS: Dict[str, Tuple[float, float, float, float]] = {
    "full-portrait": (0.14, 0.14, 0.22, 0.08),
    "portrait":      (0.10, 0.07, 0.14, 0.07),
    "wide-portrait": (0.10, 0.07, 0.14, 0.07),
    "square":        (0.08, 0.08, 0.12, 0.08),
    "widescreen":    (0.06, 0.06, 0.10, 0.06),
    "cinematic":     (0.06, 0.06, 0.10, 0.06),
    "classic-tv":    (0.08, 0.08, 0.08, 0.08),
}

# Generic text/overlay-safe insets: aspect_key -> (top, right, bottom, left).
GENERIC_TEXT_INSETS: Dict[str, Tuple[float, float, float, float]] = {
    "full-portrait": (0.18, 0.16, 0.28, 0.10),
    "portrait":      (0.13, 0.09, 0.18, 0.09),
    "wide-portrait": (0.13, 0.09, 0.18, 0.09),
    "square":        (0.10, 0.10, 0.16, 0.10),
    "widescreen":    (0.08, 0.08, 0.14, 0.08),
    "cinematic":     (0.08, 0.08, 0.14, 0.08),
    "classic-tv":    (0.10, 0.10, 0.10, 0.10),
}


def generic_insets(aspect_key: str, purpose: str) -> Tuple[float, float, float, float]:
    """Per-aspect generic insets ``(top, right, bottom, left)`` for ``purpose``.

    ``purpose`` is ``"subject"`` or ``"text"``. Raises ``ValueError`` on an unknown
    aspect or purpose so a typo can never silently pick the wrong (or empty) zone.
    """
    if purpose == PURPOSE_SUBJECT:
        table = GENERIC_SUBJECT_INSETS
    elif purpose == PURPOSE_TEXT:
        table = GENERIC_TEXT_INSETS
    else:
        raise ValueError(f"unknown purpose {purpose!r}; valid: {PURPOSES}")
    try:
        return table[aspect_key]
    except KeyError:
        raise ValueError(
            f"unknown aspect preset {aspect_key!r}; valid: {sorted(table)}"
        ) from None


# ── normalized geometry ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedZone:
    """One purpose's safe region as a normalized (0–1) orthogonal polygon.

    ``polygon`` is the closed ring of (x, y) vertices in 0–1 space (x/width,
    y/height); ``notch_rects`` are danger rectangles carved from it (also 0–1).
    A ``None`` mode / full-frame zone is the unit square with no notch. The
    geometry is row-convex when resolved (the generator guarantees this), so the
    pixel :class:`SafeZoneSpec` it produces is a drop-in for the legacy path.
    """

    polygon: List[Tuple[float, float]]
    notch_rects: List[Tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def bounding_box(self) -> Tuple[float, float, float, float]:
        xs = [p[0] for p in self.polygon]
        ys = [p[1] for p in self.polygon]
        return (min(xs), min(ys), max(xs), max(ys))

    def to_dict(self) -> dict:
        return {
            "polygon": [[x, y] for x, y in self.polygon],
            "notch_rects": [list(r) for r in self.notch_rects],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NormalizedZone":
        return cls(
            polygon=[(float(x), float(y)) for x, y in d["polygon"]],
            notch_rects=[tuple(float(v) for v in r) for r in d.get("notch_rects", [])],
        )


def _inset_polygon(insets: Tuple[float, float, float, float]) -> List[Tuple[float, float]]:
    """Normalized rectangle polygon from ``(top, right, bottom, left)`` insets."""
    t, r, b, l = insets
    x0, y0, x1, y1 = l, t, 1.0 - r, 1.0 - b
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"degenerate generic insets {insets!r} (no safe area left)")
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ── pixel resolution helpers ─────────────────────────────────────────────────────


def _round_in(v: float, hi: int) -> int:
    """Round a normalized coord to a pixel edge, clamped to ``[0, hi]``."""
    return max(0, min(hi, int(round(v))))


def _bands_from_rect_and_notches(
    bbox: Tuple[int, int, int, int],
    notch_rects: List[Tuple[int, int, int, int]],
) -> List[Band]:
    """Row-convex bands for a rectangle with right/left/edge notches carved out.

    The generic/none cases produce a single rectangle (no notch); custom can carry
    notches. Notches are assumed (per the PNG generator's contract) to be convex
    carve-outs that keep each row a single contiguous safe run — which holds for the
    lower-right action-button cluster the templates encode. We build bands row-run
    by row-run; a row whose safe span is split by a notch would raise, mirroring the
    generator's row-convex invariant.
    """
    bx0, by0, bx1, by1 = bbox
    if by1 <= by0 or bx1 <= bx0:
        return []
    # Per-row [x0, x1) after carving notches; group equal consecutive rows.
    bands: List[Band] = []
    run_x0 = run_x1 = None
    run_start = by0
    for y in range(by0, by1):
        x0, x1 = bx0, bx1
        for nx0, ny0, nx1, ny1 in notch_rects:
            if ny0 <= y < ny1:
                # Carve: notch must touch a side (else the row would split).
                if nx1 >= bx1 and nx0 > x0:        # right-side notch
                    x1 = min(x1, nx0)
                elif nx0 <= bx0 and nx1 < bx1:     # left-side notch
                    x0 = max(x0, nx1)
                elif nx0 <= bx0 and nx1 >= bx1:    # full-width notch (top/bottom bar)
                    x0 = x1 = 0
                else:
                    raise ValueError(
                        "interior notch would split a row (not row-convex); "
                        f"row {y}, notch {(nx0, ny0, nx1, ny1)}"
                    )
        if x1 <= x0:
            x0 = x1 = 0  # this row is fully danger
        if (x0, x1) != (run_x0, run_x1):
            if run_x0 is not None and run_x1 > run_x0:
                bands.append(Band(run_x0, run_start, run_x1, y))
            run_x0, run_x1, run_start = x0, x1, y
    if run_x0 is not None and run_x1 > run_x0:
        bands.append(Band(run_x0, run_start, run_x1, by1))
    return bands


# ── the normalized safe zone ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class NormalizedSafeZone:
    """A resolution-independent safe zone: a mode + the two purpose geometries.

    Resolve to pixels with :meth:`resolve` (defaulting to the text purpose, the
    strictest — what captions/QC consume). The same instance resolves to a
    proportional :class:`SafeZoneSpec` at any width/height, which is the whole
    point: one zone, every resolution tier of an aspect.
    """

    mode: str
    aspect: Optional[str]                  # the bound aspect (custom resets with it)
    subject: NormalizedZone
    text: NormalizedZone
    source_template: str = ""
    version: str = NORMALIZED_VERSION

    def __post_init__(self) -> None:
        if self.mode not in SAFE_ZONE_MODES:
            raise ValueError(f"unknown safe-zone mode {self.mode!r}; valid: {SAFE_ZONE_MODES}")

    def zone(self, purpose: str = PURPOSE_TEXT) -> NormalizedZone:
        if purpose == PURPOSE_SUBJECT:
            return self.subject
        if purpose == PURPOSE_TEXT:
            return self.text
        raise ValueError(f"unknown purpose {purpose!r}; valid: {PURPOSES}")

    def resolve(
        self,
        width: int,
        height: int,
        purpose: str = PURPOSE_TEXT,
        profile: Optional[str] = None,
    ) -> SafeZoneSpec:
        """Resolve to a pixel :class:`SafeZoneSpec` for ``purpose`` at this size.

        Normalized x/y are scaled by width/height and rounded to pixel edges; the
        zone's notches are carved into row-convex bands. The returned spec exposes
        exactly the legacy pixel API (``contains`` / ``rect_clear`` / ``bands`` /
        ``polygon`` / ``notch_rects``), so caption placement and QC consume it
        unchanged. ``none`` resolves to the full frame (one band, no notch).
        """
        if width <= 0 or height <= 0:
            raise ValueError(f"resolve needs positive dims, got {width}x{height}")
        z = self.zone(purpose)
        nx0, ny0, nx1, ny1 = z.bounding_box
        bbox = (
            _round_in(nx0 * width, width),
            _round_in(ny0 * height, height),
            _round_in(nx1 * width, width),
            _round_in(ny1 * height, height),
        )
        px_notches: List[Tuple[int, int, int, int]] = []
        for rx0, ry0, rx1, ry1 in z.notch_rects:
            px_notches.append(
                (
                    _round_in(rx0 * width, width),
                    _round_in(ry0 * height, height),
                    _round_in(rx1 * width, width),
                    _round_in(ry1 * height, height),
                )
            )
        bands = _bands_from_rect_and_notches(bbox, px_notches)
        polygon = _polygon_from_bands(bands)
        # Re-derive the effective bbox from bands (a fully-empty zone -> empty).
        if bands:
            cx0 = min(b.x0 for b in bands)
            cx1 = max(b.x1 for b in bands)
            cy0 = min(b.y0 for b in bands)
            cy1 = max(b.y1 for b in bands)
            eff_bbox = (cx0, cy0, cx1, cy1)
        else:
            eff_bbox = bbox
        safe_area = sum(b.area for b in bands)
        # Notches actually realized in band space (carved ones only).
        realized_notches = _notch_rects_from_bands(bands, eff_bbox)
        return SafeZoneSpec(
            profile=profile or self.source_template or self.mode,
            source_template=self.source_template,
            image_width=width,
            image_height=height,
            key_mode="normalized",
            key_threshold=0,
            bounding_box=eff_bbox,
            polygon=polygon,
            bands=bands,
            notch_rects=realized_notches,
            safe_area_px=safe_area,
            total_px=width * height,
            generator_version=f"normalized-{self.version}",
        )

    # ── serialization ────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "aspect": self.aspect,
            "source_template": self.source_template,
            "version": self.version,
            "subject": self.subject.to_dict(),
            "text": self.text.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "NormalizedSafeZone":
        return cls(
            mode=d["mode"],
            aspect=d.get("aspect"),
            subject=NormalizedZone.from_dict(d["subject"]),
            text=NormalizedZone.from_dict(d["text"]),
            source_template=d.get("source_template", ""),
            version=d.get("version", NORMALIZED_VERSION),
        )


# polygon/notch helpers reused from the band shape (kept local to avoid importing
# the generator's numpy path just for orthogonal tracing).

def _polygon_from_bands(bands: List[Band]) -> List[Tuple[int, int]]:
    """Orthogonal boundary ring of vertically-stacked, row-convex bands."""
    if not bands:
        return []
    pts: List[Tuple[int, int]] = []
    for b in bands:
        pts.append((b.x1, b.y0))
        pts.append((b.x1, b.y1))
    for b in reversed(bands):
        pts.append((b.x0, b.y1))
        pts.append((b.x0, b.y0))
    return _simplify(pts)


def _simplify(points: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    pts = [p for i, p in enumerate(points) if p != points[i - 1]]
    if len(pts) < 3:
        return pts
    out: List[Tuple[int, int]] = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if cross != 0:
            out.append(b)
    return out


def _notch_rects_from_bands(
    bands: List[Band], bbox: Tuple[int, int, int, int]
) -> List[Tuple[int, int, int, int]]:
    """Danger rectangles carved from the bbox (mirrors the generator's logic)."""
    if not bands:
        return []
    bx0, _, bx1, _ = bbox
    raw: List[Tuple[int, int, int, int]] = []
    for b in bands:
        if b.x1 < bx1:
            raw.append((b.x1, b.y0, bx1, b.y1))
        if b.x0 > bx0:
            raw.append((bx0, b.y0, b.x0, b.y1))
    raw.sort(key=lambda r: (r[0], r[2], r[1]))
    merged: List[Tuple[int, int, int, int]] = []
    for r in raw:
        if merged:
            m = merged[-1]
            if m[0] == r[0] and m[2] == r[2] and m[3] == r[1]:
                merged[-1] = (m[0], m[1], m[2], r[3])
                continue
        merged.append(r)
    return merged


# ── builders ──────────────────────────────────────────────────────────────────────


def none_safe_zone(aspect: Optional[str] = None) -> NormalizedSafeZone:
    """The ``none`` mode: full frame is safe, nothing avoided.

    Both purposes are the unit square. Resolving gives a single full-frame band, so
    ``contains`` is always True inside the frame and ``rect_clear`` always holds —
    caption placement falls back to the full-frame inset, QC flags nothing.
    """
    full = NormalizedZone(polygon=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    return NormalizedSafeZone(
        mode=MODE_NONE, aspect=aspect, subject=full, text=full,
    )


def generic_safe_zone(aspect_key: str) -> NormalizedSafeZone:
    """The ``generic`` mode (DEFAULT): per-aspect asymmetric inset rectangles.

    Both purposes are populated from the locked inset tables (text stricter than
    subject). Resolution-independent: the same instance resolves to proportional
    pixels at 1080p / 1440p / 4k of ``aspect_key``.
    """
    subject = NormalizedZone(polygon=_inset_polygon(generic_insets(aspect_key, PURPOSE_SUBJECT)))
    text = NormalizedZone(polygon=_inset_polygon(generic_insets(aspect_key, PURPOSE_TEXT)))
    return NormalizedSafeZone(mode=MODE_GENERIC, aspect=aspect_key, subject=subject, text=text)


def custom_from_spec(
    spec: SafeZoneSpec,
    aspect: Optional[str] = None,
    subject_spec: Optional[SafeZoneSpec] = None,
) -> NormalizedSafeZone:
    """Normalize a pixel :class:`SafeZoneSpec` (from the PNG generator) to 0–1.

    The existing generator emits a pixel spec for a template's canvas; this divides
    its polygon/notches by that canvas so the zone re-resolves at any target size.
    ``custom`` is **aspect-bound** (the caller passes the aspect it was traced for;
    it resets when the aspect changes). If ``subject_spec`` is given it populates the
    subject purpose separately (e.g. a looser subject template); otherwise both
    purposes share the one traced zone (first-implementation: text-safe primary).
    """
    text_zone = _normalize_spec(spec)
    subject_zone = _normalize_spec(subject_spec) if subject_spec is not None else text_zone
    return NormalizedSafeZone(
        mode=MODE_CUSTOM,
        aspect=aspect,
        subject=subject_zone,
        text=text_zone,
        source_template=spec.source_template,
    )


def _normalize_spec(spec: SafeZoneSpec) -> NormalizedZone:
    w, h = spec.image_width, spec.image_height
    poly = [(x / w, y / h) for x, y in spec.polygon]
    notches = [(x0 / w, y0 / h, x1 / w, y1 / h) for (x0, y0, x1, y1) in spec.notch_rects]
    return NormalizedZone(polygon=poly, notch_rects=notches)


def custom_from_png(
    png_path: str,
    aspect: Optional[str] = None,
    *,
    key: str = "auto",
    threshold: Optional[int] = None,
) -> NormalizedSafeZone:
    """Trace a template PNG (via the existing generator) and normalize it.

    Thin convenience over :func:`~video_pipeline.safezone.generator.generate_spec`
    + :func:`custom_from_spec`. Needs numpy/Pillow (the generator's deps).
    """
    from .generator import generate_spec

    spec = generate_spec(png_path, key=key, threshold=threshold)
    return custom_from_spec(spec, aspect=aspect)


# ── top-level factory ──────────────────────────────────────────────────────────────


def build_safe_zone(
    mode: str,
    aspect_key: str,
    *,
    png_path: Optional[str] = None,
) -> NormalizedSafeZone:
    """Build the normalized zone for ``mode`` against ``aspect_key``.

    ``none``/``generic`` need only the aspect; ``custom`` requires ``png_path``.
    The single place mode → builder mapping lives, so the manifest/CLI/GUI cannot
    drift on which builder a mode uses.
    """
    if mode == MODE_NONE:
        return none_safe_zone(aspect=aspect_key)
    if mode == MODE_GENERIC:
        return generic_safe_zone(aspect_key)
    if mode == MODE_CUSTOM:
        if not png_path:
            raise ValueError("custom safe-zone mode requires a png_path")
        return custom_from_png(png_path, aspect=aspect_key)
    raise ValueError(f"unknown safe-zone mode {mode!r}; valid: {SAFE_ZONE_MODES}")
