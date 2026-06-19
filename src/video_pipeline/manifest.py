"""project.yml — the per-project contract.

``project.yml`` declares ``identity`` (-> glossary layer), ``profile`` (-> output
dimensions + safe-zone spec), and ``rough_cut.trim_filler`` (default true), plus
optional metadata. This module validates it against ``schema/project.schema.json``,
applies defaults, and parses the project folder-name convention:

    {project-id} = "YYYY-MM-DD <Token> Project - <Hook>"
        e.g.  "2026-06-03 Reel Project - I used to make fun of ravers"

    final render = "YYYY-MM-DD-<token>-<kebab-hook>.mp4"
        e.g.  "2026-06-03-reel-i-used-to-make-fun-of-ravers.mp4"

The ``<Token>`` ("Reel") is profile-supplied; a future YouTube profile
substitutes its own token in both the folder label and the render filename.
``identity`` and ``profile`` come from ``project.yml``, not the folder name.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

_FOLDER_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<token>\S+)\s+Project\s*-\s*(?P<hook>.+?)\s*$"
)

_SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schema" / "project.schema.json"


# ── folder-name parsing ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class FolderName:
    date: str
    token: str
    hook: str

    def render_filename(self) -> str:
        return f"{self.date}-{self.token.lower()}-{kebab(self.hook)}.mp4"


def kebab(text: str) -> str:
    """Lowercase, ASCII, hyphen-separated slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def parse_folder_name(name: str) -> FolderName:
    """Parse a project folder name. Raises ValueError if it doesn't match."""
    name = os.path.basename(name.rstrip("/"))
    m = _FOLDER_RE.match(name)
    if not m:
        raise ValueError(
            f"folder name {name!r} does not match "
            f"'YYYY-MM-DD <Token> Project - <Hook>'"
        )
    return FolderName(date=m.group("date"), token=m.group("token"), hook=m.group("hook"))


# ── manifest model ────────────────────────────────────────────────────────────

@dataclass
class Manifest:
    identity: str
    profile: str
    trim_filler: bool = True
    project_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def safezone_spec_filename(self) -> str:
        return f"{self.profile}.safezone.json"

    @property
    def identity_glossary_filename(self) -> str:
        return f"{self.identity}.yml"

    def propose_config(self):
        """Build a roughcut ProposeConfig from this manifest's rough_cut block.

        Lazily imported so the manifest layer carries no dependency on the
        rough-cut phase. Unspecified knobs fall back to ProposeConfig defaults.
        """
        from .roughcut.propose import ProposeConfig

        rough = (self.raw.get("rough_cut") or {}) if self.raw else {}
        defaults = ProposeConfig()
        extra = frozenset(rough.get("extra_filler_words") or [])
        return ProposeConfig(
            trim_filler=bool(rough.get("trim_filler", self.trim_filler)),
            extra_filler_words=extra,
            silence_gap_s=float(rough.get("silence_gap_s", defaults.silence_gap_s)),
            keep_pad_lead_s=float(rough.get("keep_pad_lead_s", defaults.keep_pad_lead_s)),
            keep_pad_tail_s=float(rough.get("keep_pad_tail_s", defaults.keep_pad_tail_s)),
            detect_false_starts=bool(
                rough.get("detect_false_starts", defaults.detect_false_starts)
            ),
        )

    @property
    def caption_overrides(self) -> Dict[str, Any]:
        """The project's ``captions:`` block (style/chunk overrides), or ``{}``.

        Passed to ``load_caption_style`` as the highest-precedence layer over the
        repo's ``config/caption-styles/`` global + identity files.
        """
        return dict((self.raw.get("captions") or {})) if self.raw else {}

    def caption_style(self, config_root):
        """Resolve this project's :class:`CaptionStyle` (lazily imported)."""
        from .captions.style import load_caption_style

        return load_caption_style(config_root, self.identity, overrides=self.caption_overrides)

    def qc_config(self) -> Dict[str, Any]:
        """Resolve the safe-zone QC settings from this manifest's ``qc:`` block.

        Returns a dict of validator knobs (``occlusion_frac``,
        ``face_danger_frac``, ``intrusion_frac``, ``check_caption_over_face``,
        ``check_face_in_danger``) plus ``elements`` — a list of static
        :class:`~video_pipeline.qc.report.QCElement` overlay boxes (logo/CTA)
        checked on every render. Unset keys fall back to the validator defaults.
        """
        from .qc.report import QCElement, Rect

        qc = (self.raw.get("qc") or {}) if self.raw else {}
        elements = []
        for e in qc.get("elements") or []:
            elements.append(
                QCElement(
                    kind=e["kind"],
                    rect=Rect.from_xywh(e["x"], e["y"], e["width"], e["height"]),
                    label=e.get("label", ""),
                )
            )
        return {
            "occlusion_frac": float(qc.get("occlusion_frac", 0.1)),
            "face_danger_frac": float(qc.get("face_danger_frac", 0.2)),
            "intrusion_frac": float(qc.get("intrusion_frac", 0.0)),
            "check_caption_over_face": bool(qc.get("check_caption_over_face", True)),
            "check_face_in_danger": bool(qc.get("check_face_in_danger", True)),
            "elements": elements,
        }


def _load_schema() -> dict:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def validate_manifest_dict(data: dict) -> None:
    """Validate a manifest dict against the JSON schema. Raises on failure."""
    import jsonschema

    jsonschema.validate(instance=data, schema=_load_schema())


def manifest_from_dict(data: dict) -> Manifest:
    validate_manifest_dict(data)
    rough = data.get("rough_cut") or {}
    return Manifest(
        identity=data["identity"],
        profile=data["profile"],
        trim_filler=bool(rough.get("trim_filler", True)),
        project_id=data.get("project_id"),
        metadata=data.get("metadata") or {},
        raw=data,
    )


def load_manifest(path: str | os.PathLike) -> Manifest:
    """Load and validate ``project.yml`` (file or containing directory)."""
    import yaml

    p = Path(path)
    if p.is_dir():
        p = p / "project.yml"
    with open(p, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{p} did not parse to a mapping")
    return manifest_from_dict(data)
