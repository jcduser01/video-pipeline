"""Caption style — the style layer, loaded from layered repo-resident config.

Captions are two layers kept separate (brief §3.3). This module owns the **style
layer**: font / size / colour / stroke / position / emphasis / casing, plus the
chunk-sizing knobs that shape the timing layer. Both live in one config file so
"how captions read and look" is a single decision.

Layers compose exactly like the glossary: ``global`` + the project's ``identity``
layer (identity wins), then optional project-level overrides from ``project.yml``
(project wins over identity wins over global). Unset keys fall through to the
built-in :class:`CaptionStyle` defaults.

The chunker (:mod:`video_pipeline.captions.chunk`) consumes ``max_words`` /
``min_words`` / ``max_chars`` / ``max_gap_s`` / ``emphasize_glossary_terms``; the
Remotion props exporter consumes the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

# Vertical anchors for the caption box inside the safe zone.
POSITIONS = ("upper-third", "center", "lower-third")


@dataclass(frozen=True)
class CaptionStyle:
    """Resolved caption style + chunk knobs for one project.

    Defaults are deliberately legible-over-video (heavy weight, thick stroke,
    high-contrast fill) and Reels-sized (px at the profile's native height).
    """

    # ── style layer (Remotion) ──
    font_family: str = "Helvetica"
    font_size: int = 96
    font_weight: int = 800
    fill_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: int = 8
    emphasis_color: str = "#FFE14D"
    uppercase: bool = True
    position: str = "lower-third"

    # ── timing layer (chunker) ──
    # min_words / max_words are the words-per-cue RANGE — the primary control.
    # 1/1 = single-word (word-by-word) captions; 2/4 = phrase-aware groups; any
    # other range (e.g. 1/2) works too. There is no separate "mode".
    max_words: int = 4
    min_words: int = 2
    max_chars: int = 24
    max_gap_s: float = 0.6
    emphasize_glossary_terms: bool = True
    # 0 = auto (midpoint of the range); the chunker aims cue lengths at this.
    target_words: int = 0
    # empty = built-in English function-word set (chunk.DEFAULT_BREAK_WORDS).
    break_words: tuple = ()
    # Karaoke active-word highlight: each word lights up as it is spoken. Works at
    # any range; most striking at 2-4 words per cue.
    karaoke: bool = False

    def __post_init__(self):
        if self.position not in POSITIONS:
            raise ValueError(
                f"position {self.position!r} not in {POSITIONS}"
            )
        if self.min_words < 1 or self.max_words < self.min_words:
            raise ValueError(
                f"need 1 <= min_words <= max_words "
                f"(got min={self.min_words}, max={self.max_words})"
            )
        if self.target_words and not (self.min_words <= self.target_words <= self.max_words):
            raise ValueError(
                f"target_words {self.target_words} must be within "
                f"[{self.min_words}, {self.max_words}] (or 0 for auto)"
            )
        # normalise break_words to a tuple (config may hand us a list)
        object.__setattr__(self, "break_words", tuple(self.break_words or ()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "font_family": self.font_family,
            "font_size": self.font_size,
            "font_weight": self.font_weight,
            "fill_color": self.fill_color,
            "stroke_color": self.stroke_color,
            "stroke_width": self.stroke_width,
            "emphasis_color": self.emphasis_color,
            "uppercase": self.uppercase,
            "position": self.position,
            "max_words": self.max_words,
            "min_words": self.min_words,
            "max_chars": self.max_chars,
            "max_gap_s": self.max_gap_s,
            "emphasize_glossary_terms": self.emphasize_glossary_terms,
            "target_words": self.target_words,
            "karaoke": self.karaoke,
        }


# Keys that are allowed to come from config / project overrides, mapped to the
# coercion applied. Unknown keys are ignored (forward-compatible config).
_COERCE = {
    "font_family": str,
    "font_size": int,
    "font_weight": int,
    "fill_color": str,
    "stroke_color": str,
    "stroke_width": int,
    "emphasis_color": str,
    "uppercase": bool,
    "position": str,
    "max_words": int,
    "min_words": int,
    "max_chars": int,
    "max_gap_s": float,
    "emphasize_glossary_terms": bool,
    "target_words": int,
    "break_words": lambda v: [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])],
    "karaoke": bool,
}


def _clean(data: Optional[dict]) -> Dict[str, Any]:
    """Keep only recognised keys, coerced to their declared types."""
    if not data:
        return {}
    out: Dict[str, Any] = {}
    for key, coerce in _COERCE.items():
        if key in data and data[key] is not None:
            out[key] = coerce(data[key])
    return out


def _load_layer(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not parse to a mapping")
    return _clean(data)


def load_caption_style(
    config_root: str | Path,
    identity: str,
    overrides: Optional[dict] = None,
) -> CaptionStyle:
    """Merge global + identity + project overrides into a :class:`CaptionStyle`.

    ``config_root`` is the repo ``config/`` directory (it holds ``caption-styles/``).
    Precedence (low → high): built-in defaults, ``global.yml``, the identity
    layer, then ``overrides`` (a project.yml ``captions:`` block). Unknown keys
    in any layer are ignored.
    """
    root = Path(config_root) / "caption-styles"
    merged: Dict[str, Any] = {}
    merged.update(_load_layer(root / "global.yml"))
    merged.update(_load_layer(root / "identities" / f"{identity}.yml"))
    merged.update(_clean(overrides))
    return replace(CaptionStyle(), **merged)
