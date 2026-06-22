"""Target-format model — aspect presets, resolution tiers, and the Auto resolver.

The first-class "target format" the reframe (and every downstream render) recomposes
toward. Two orthogonal axes, deliberately kept separate (INI-090):

  - **Aspect preset** — the *shape*. Drives the crop/reframe geometry. Stored as a
    reduced integer fraction so all crop arithmetic stays integer-exact (e.g. cinematic
    is true 21:9 reduced to 7:3 — never a 2.39 float).
  - **Resolution tier** — the *final pixel size*. A finite ladder (4K / 1440p / 1080p /
    720p) of canonical, platform-correct targets per aspect, plus **Auto**.

Auto chooses the highest tier whose canonical target fits inside the reframed crop's
native pixels, allowing a small (5%) upscale tolerance; it steps down a tier when the
crop is too small, and falls back to the largest exact-ratio box inside the crop when
even the 720p target won't fit. The engine never upscales beyond the tolerance by
default — going further is an explicit, opt-in advanced choice the caller makes.

Pure and fully unit-tested; no native deps. The CLI/schema layer maps user selections
onto :func:`resolve`; this module owns the data and the math.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, Tuple


# ── data types ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AspectPreset:
    """A named target shape. ``w:h`` is the reduced integer aspect ratio."""

    key: str
    label: str
    w: int
    h: int
    use: str

    @property
    def ratio(self) -> Fraction:
        return Fraction(self.w, self.h)


@dataclass(frozen=True)
class ResolutionTarget:
    """A concrete render size. ``tier`` is a ladder key or ``"exact-fit"``."""

    tier: str
    width: int
    height: int

    @property
    def aspect(self) -> Fraction:
        return Fraction(self.width, self.height)


# ── canonical tables (CEO-locked spec, 2026-06-22, INI-090) ────────────────────

# Resolution ladder, ordered HIGH → LOW so the first tier that fits is the highest.
TIERS: Tuple[str, ...] = ("4k", "1440p", "1080p", "720p")

DEFAULT_ASPECT = "full-portrait"   # primary tool default — short-form social
DEFAULT_TIER = "1080p"             # each aspect's labeled default / Auto reference
UPSCALE_TOLERANCE = 0.05           # Auto may upscale a crop by at most this fraction

ASPECT_PRESETS: Dict[str, AspectPreset] = {
    "cinematic": AspectPreset(
        "cinematic", "Cinematic widescreen", 7, 3,
        "Cinematic/ultrawide/music-video/trailer; an intentional stylistic option, "
        "not a social feed format."),
    "widescreen": AspectPreset(
        "widescreen", "Widescreen", 16, 9,
        "Standard landscape: YouTube, Facebook landscape, embedded/desktop, "
        "general-purpose exports."),
    "full-portrait": AspectPreset(
        "full-portrait", "Full portrait", 9, 16,
        "Full-screen vertical: Reels, Stories, TikTok, Shorts, Snapchat and similar "
        "mobile-first placements. The primary short-form default."),
    "portrait": AspectPreset(
        "portrait", "Portrait", 2, 3,
        "Tall vertical feed creative; Pinterest-style and tall feed placements short "
        "of full-screen."),
    "wide-portrait": AspectPreset(
        "wide-portrait", "Wide portrait", 4, 5,
        "Vertical feed video filling more than square without going full-screen: "
        "Instagram/Facebook/LinkedIn/Reddit feeds."),
    "square": AspectPreset(
        "square", "Square", 1, 1,
        "Square feed video; broadly supported across Instagram/Facebook/TikTok/"
        "LinkedIn/Pinterest/Reddit/X."),
    "classic-tv": AspectPreset(
        "classic-tv", "Classic television", 4, 3,
        "Vintage/archival/retro styling; a creative/completeness preset, not a modern "
        "social-first default."),
}

# Per aspect: tier key -> (width, height). Exact-ratio, even dimensions.
RESOLUTION_MATRIX: Dict[str, Dict[str, Tuple[int, int]]] = {
    "cinematic":     {"4k": (5040, 2160), "1440p": (3360, 1440), "1080p": (2520, 1080), "720p": (1680, 720)},
    "widescreen":    {"4k": (3840, 2160), "1440p": (2560, 1440), "1080p": (1920, 1080), "720p": (1280, 720)},
    "full-portrait": {"4k": (2160, 3840), "1440p": (1440, 2560), "1080p": (1080, 1920), "720p": (720, 1280)},
    "portrait":      {"4k": (1440, 2160), "1440p": (1200, 1800), "1080p": (1000, 1500), "720p": (720, 1080)},
    "wide-portrait": {"4k": (1728, 2160), "1440p": (1152, 1440), "1080p": (1080, 1350), "720p": (576, 720)},
    "square":        {"4k": (2160, 2160), "1440p": (1440, 1440), "1080p": (1080, 1080), "720p": (720, 720)},
    "classic-tv":    {"4k": (2880, 2160), "1440p": (1920, 1440), "1080p": (1440, 1080), "720p": (960, 720)},
}


# ── accessors ──────────────────────────────────────────────────────────────────

def aspect_preset(aspect_key: str) -> AspectPreset:
    try:
        return ASPECT_PRESETS[aspect_key]
    except KeyError:
        raise ValueError(
            f"unknown aspect preset {aspect_key!r}; valid: {sorted(ASPECT_PRESETS)}"
        ) from None


def resolution_target(aspect_key: str, tier: str) -> ResolutionTarget:
    tiers = RESOLUTION_MATRIX.get(aspect_key)
    if tiers is None:
        raise ValueError(
            f"unknown aspect preset {aspect_key!r}; valid: {sorted(RESOLUTION_MATRIX)}"
        )
    if tier not in tiers:
        raise ValueError(
            f"unknown resolution tier {tier!r} for {aspect_key!r}; valid: {TIERS}"
        )
    w, h = tiers[tier]
    return ResolutionTarget(tier=tier, width=w, height=h)


def default_target(aspect_key: str) -> ResolutionTarget:
    """The aspect's labeled default (1080p-class) — Auto's reference point."""
    return resolution_target(aspect_key, DEFAULT_TIER)


# ── geometry ───────────────────────────────────────────────────────────────────

def largest_exact_fit(aspect_key: str, crop_w: int, crop_h: int) -> Tuple[int, int]:
    """Largest exact-aspect, even-dimensioned box that fits inside ``crop_w × crop_h``.

    Used when even the 720p-class target exceeds the crop and upscaling is disabled:
    we export the biggest box of the target shape the crop can hold without upscaling.
    """
    p = aspect_preset(aspect_key)
    m = min(crop_w // p.w, crop_h // p.h)
    # w = p.w * m, h = p.h * m is exact ratio for any m; shrink m until both even.
    while m > 0 and ((p.w * m) % 2 or (p.h * m) % 2):
        m -= 1
    return (p.w * m, p.h * m)


# ── resolver ───────────────────────────────────────────────────────────────────

def resolve_auto(
    aspect_key: str,
    crop_w: int,
    crop_h: int,
    tolerance: float = UPSCALE_TOLERANCE,
) -> ResolutionTarget:
    """Auto: highest tier whose target fits the crop within ``tolerance`` upscale.

    Walks the ladder high → low and returns the first (highest) tier whose canonical
    target fits inside ``crop_w × crop_h`` scaled up by ``tolerance``. If none fit,
    returns the largest exact-ratio box inside the crop, tagged ``"exact-fit"``.
    """
    max_w = crop_w * (1.0 + tolerance)
    max_h = crop_h * (1.0 + tolerance)
    for tier in TIERS:
        t = resolution_target(aspect_key, tier)
        if t.width <= max_w and t.height <= max_h:
            return t
    w, h = largest_exact_fit(aspect_key, crop_w, crop_h)
    return ResolutionTarget(tier="exact-fit", width=w, height=h)


def resolve(
    aspect_key: str,
    selection: str,
    crop_w: int,
    crop_h: int,
    tolerance: float = UPSCALE_TOLERANCE,
) -> ResolutionTarget:
    """Resolve a user selection to a concrete :class:`ResolutionTarget`.

    ``selection`` is ``"auto"`` or a tier key in :data:`TIERS`. ``"auto"`` defers to
    :func:`resolve_auto` (never upscales beyond ``tolerance``). An explicit tier is
    honored verbatim — the canonical target for that tier — even if it exceeds the
    crop; refusing/allowing that upscale is a separate advanced-option policy the
    caller owns, not the resolver's.
    """
    if selection == "auto":
        return resolve_auto(aspect_key, crop_w, crop_h, tolerance=tolerance)
    return resolution_target(aspect_key, selection)
