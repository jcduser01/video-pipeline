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
from typing import Dict, FrozenSet, Optional, Tuple


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


# ── project-level Target value object (INI-091) ─────────────────────────────────

# Valid resolution selections: "auto" or a tier key.
RESOLUTION_SELECTIONS: Tuple[str, ...] = ("auto", *TIERS)

# Tolerant map: legacy per-step ``profile`` slugs -> a project Target. Lets existing
# project.yml files (and INI-090 fixtures) keep declaring ``profile`` while the new
# code reads a Target. Aspect is derived from the slug; resolution defaults to Auto
# (the legacy profiles encoded a 1080-class fixed size, which Auto reproduces for the
# common 1080-class crop and otherwise picks the best non-upscaling tier).
PROFILE_TO_ASPECT: Dict[str, str] = {
    "reels-9x16": "full-portrait",
    "story-9x16": "full-portrait",
    "feed-portrait-4x5": "wide-portrait",
    "feed-square-1x1": "square",
    "feed-landscape-16x9": "widescreen",
}


@dataclass(frozen=True)
class Target:
    """The project-level Target — one aspect shape + one resolution selection.

    Chosen once per project (INI-091), upstream of safezone and reframe, replacing
    the legacy per-step ``--profile``/aspect selector. ``aspect`` is an aspect-preset
    key; ``resolution`` is ``"auto"`` or a tier key. Immutable and self-validating so
    an invalid Target can never reach the resolver/crop math.
    """

    aspect: str = DEFAULT_ASPECT
    resolution: str = "auto"

    def __post_init__(self) -> None:
        # Validate eagerly: raises ValueError on an unknown aspect/selection.
        aspect_preset(self.aspect)
        if self.resolution not in RESOLUTION_SELECTIONS:
            raise ValueError(
                f"unknown resolution selection {self.resolution!r}; "
                f"valid: {RESOLUTION_SELECTIONS}"
            )

    @property
    def preset(self) -> AspectPreset:
        return aspect_preset(self.aspect)

    def resolve(self, crop_w: int, crop_h: int,
                tolerance: float = UPSCALE_TOLERANCE) -> ResolutionTarget:
        """Resolve this Target's resolution against a native crop size."""
        return resolve(self.aspect, self.resolution, crop_w, crop_h, tolerance=tolerance)

    @classmethod
    def from_profile(cls, profile: Optional[str]) -> "Target":
        """Tolerant back-compat: map a legacy ``profile`` slug to a Target.

        Unknown/absent profiles fall back to the default Target (full-portrait/auto)
        rather than raising — the legacy path was always best-effort, and INI-091
        keeps any existing project loadable.
        """
        aspect = PROFILE_TO_ASPECT.get(profile or "", DEFAULT_ASPECT)
        return cls(aspect=aspect, resolution="auto")


# ── downstream reset cascade (INI-091, the testable heart) ──────────────────────

# Downstream artifacts a Target change can invalidate, in pipeline order.
#   framing  — the reframe's composition proposal (subject_scale/subject_y).
#   reframe  — the crop geometry / reframed clip (depends on aspect shape).
#   safezone — the safe-zone spec (its pixel resolution tracks the target size).
#   captions — caption layout/render (sized to the frame).
#   qc       — safe-zone QC (re-checks against the new frame).
DOWNSTREAM_ARTIFACTS: Tuple[str, ...] = (
    "framing", "reframe", "safezone", "captions", "qc",
)

# Artifacts whose validity depends on the *aspect shape* (the crop geometry / the
# subject's framing). Changing the aspect invalidates these; a resolution-only
# change does NOT (the shape is unchanged, only the pixel size).
_ASPECT_DEPENDENT: FrozenSet[str] = frozenset(
    {"framing", "reframe", "safezone", "captions", "qc"}
)

# Artifacts whose validity depends on the *pixel resolution* of the frame. A
# resolution-only change invalidates these (their pixel geometry must regenerate)
# but NOT framing/reframe (the crop shape and subject composition are unchanged —
# the reframe re-scales to the new size without re-proposing the crop).
_RESOLUTION_DEPENDENT: FrozenSet[str] = frozenset(
    {"safezone", "captions", "qc"}
)


@dataclass(frozen=True)
class ResetResult:
    """Which downstream artifacts a Target change invalidates, and why.

    ``invalidated`` is the ordered tuple of artifact keys that must regenerate.
    ``aspect_changed`` / ``resolution_changed`` expose the two axes independently so
    a caller can explain the cascade (and so the resolution-reset rule — change the
    aspect and resolution snaps back to Auto — is observable).
    """

    invalidated: Tuple[str, ...]
    aspect_changed: bool
    resolution_changed: bool
    resolution_reset_to_auto: bool

    def __contains__(self, artifact: str) -> bool:
        return artifact in self.invalidated

    def __bool__(self) -> bool:
        return bool(self.invalidated)


def reset_downstream(old: Target, new: Target) -> ResetResult:
    """Pure cascade: which downstream artifacts a Target change invalidates.

    Rules (INI-091 locked spec):
      - **Aspect change** resets *everything* downstream — framing re-proposes, the
        reframe crop geometry changes, the safe zone regenerates, captions/QC are
        invalidated — AND resolution snaps back to **Auto** (the new aspect's tiers
        are different, so a tier carried over from the old aspect is meaningless).
      - **Resolution-only change** invalidates the pixel-dependent downstream
        (safezone pixel resolution, captions, QC) but NOT framing/reframe (the crop
        shape and subject composition are unchanged — the render re-scales).
      - **No change** invalidates nothing.

    Returns a :class:`ResetResult`. Note: the caller decides what ``new`` is; this
    function only *reports* whether, given the cascade, the resolution should have
    been reset to Auto on an aspect change (``resolution_reset_to_auto``). Pair it
    with :func:`apply_reset` to get the corrected new Target.
    """
    aspect_changed = old.aspect != new.aspect
    resolution_changed = old.resolution != new.resolution

    if aspect_changed:
        # Aspect drives the widest reset; resolution is forced back to Auto.
        invalidated = tuple(a for a in DOWNSTREAM_ARTIFACTS if a in _ASPECT_DEPENDENT)
        reset_to_auto = new.resolution != "auto"
        return ResetResult(
            invalidated=invalidated,
            aspect_changed=True,
            resolution_changed=resolution_changed,
            resolution_reset_to_auto=reset_to_auto,
        )

    if resolution_changed:
        invalidated = tuple(
            a for a in DOWNSTREAM_ARTIFACTS if a in _RESOLUTION_DEPENDENT
        )
        return ResetResult(
            invalidated=invalidated,
            aspect_changed=False,
            resolution_changed=True,
            resolution_reset_to_auto=False,
        )

    return ResetResult(
        invalidated=(),
        aspect_changed=False,
        resolution_changed=False,
        resolution_reset_to_auto=False,
    )


def apply_reset(old: Target, new: Target) -> Tuple[Target, ResetResult]:
    """Apply the cascade's resolution-reset rule, returning (effective_new, result).

    When the aspect changed, the effective new Target has its resolution forced to
    ``"auto"`` (the downstream-reset rule); otherwise ``new`` passes through. This is
    the single place the "changing aspect resets resolution -> Auto" rule is enacted,
    so the manifest layer and the GUI cannot drift on it.
    """
    result = reset_downstream(old, new)
    if result.resolution_reset_to_auto:
        new = Target(aspect=new.aspect, resolution="auto")
    return new, result
