"""Safe-zone spec derivation.

An Instagram safe zone is *not* a rectangle. It is an irregular polygon — a
safe-area rectangle with a smaller rectangle notched out of the lower-right
corner for the action-button cluster. This package derives that polygon
*from a reference template PNG* (adkit.so), so that when Instagram changes the
safe zone the spec regenerates by dropping in a new PNG — no code change.

Public API:
    generate_spec(png_path, profile=...) -> SafeZoneSpec
    SafeZoneSpec  (dataclass; .to_dict() / .from_dict() / .contains())

INI-091 Phase 2 adds a resolution-independent layer (``normalized``): safe zones
stored as normalized 0–1 vector geometry with three modes (none/generic/custom)
and two purposes (subject/text), resolved to a pixel ``SafeZoneSpec`` only once
the target width/height are known. The legacy pixel API above stays intact.
"""

from .spec import SafeZoneSpec, Band
from .generator import generate_spec, GENERATOR_VERSION
from .normalized import (
    NormalizedSafeZone,
    NormalizedZone,
    SAFE_ZONE_MODES,
    DEFAULT_MODE,
    MODE_NONE,
    MODE_GENERIC,
    MODE_CUSTOM,
    PURPOSES,
    PURPOSE_SUBJECT,
    PURPOSE_TEXT,
    generic_insets,
    generic_safe_zone,
    none_safe_zone,
    custom_from_png,
    custom_from_spec,
    build_safe_zone,
    NORMALIZED_VERSION,
)

__all__ = [
    "SafeZoneSpec",
    "Band",
    "generate_spec",
    "GENERATOR_VERSION",
    # normalized (INI-091 Phase 2)
    "NormalizedSafeZone",
    "NormalizedZone",
    "SAFE_ZONE_MODES",
    "DEFAULT_MODE",
    "MODE_NONE",
    "MODE_GENERIC",
    "MODE_CUSTOM",
    "PURPOSES",
    "PURPOSE_SUBJECT",
    "PURPOSE_TEXT",
    "generic_insets",
    "generic_safe_zone",
    "none_safe_zone",
    "custom_from_png",
    "custom_from_spec",
    "build_safe_zone",
    "NORMALIZED_VERSION",
]
