"""CapCut export — arranged media, no project file.

CapCut imports no EDL/XML (a dead end for timeline automation — shaping brief
§3.2), so its "package" is just the rendered layers gathered into a folder for the
operator to import and stack by hand. Each independent layer plus the flattened
composite is copied into ``<bundle>/media/`` alongside a README listing them in
z-order with import notes (SADD §3.5: "no manifest, arranged media only").

The planning helpers (:func:`gather_layers`, :func:`plan_copy`, :func:`readme_text`)
are pure and unit-tested; :func:`export_capcut` is the thin copy/write glue.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Layer:
    """One file in the CapCut bundle, with its stacking order + a human note."""

    label: str
    path: str
    z_order: int
    note: str = ""


def gather_layers(
    base: str,
    captions: Optional[str] = None,
    composite: Optional[str] = None,
) -> List[Layer]:
    """The bundle's layers, low->high z-order: base, caption overlay, composite.

    Only the provided files are included. The composite is carried as a top-order
    reference (the operator compares against it; it is not stacked into the edit).
    """
    layers: List[Layer] = []
    if base:
        layers.append(Layer(
            "Base cut", base, 0,
            "The edited/reframed base video — the bottom layer."))
    if captions:
        layers.append(Layer(
            "Captions", captions, 30,
            "Transparent caption overlay — stack above the base."))
    if composite:
        layers.append(Layer(
            "Composite (reference)", composite, 100,
            "Flattened preview of the whole stack — import only to compare; "
            "do not layer it in."))
    return layers


def plan_copy(out_dir: str, layers: List[Layer]) -> List[Tuple[str, str]]:
    """``[(src, dest)]`` copy plan into ``<out_dir>/media/`` (names preserved)."""
    media = Path(out_dir) / "media"
    return [(L.path, str(media / Path(L.path).name)) for L in layers]


def readme_text(layers: List[Layer]) -> str:
    """The bundle README: the stack order + the no-project-import explanation."""
    lines = [
        "CapCut import package",
        "=====================",
        "",
        "CapCut cannot import a project/timeline file, so this folder holds only the",
        "rendered layers. Import the files from media/ and stack them bottom-to-top:",
        "",
    ]
    for L in sorted(layers, key=lambda x: x.z_order):
        lines.append(f"  - {Path(L.path).name}  —  {L.label} (z={L.z_order})")
        if L.note:
            lines.append(f"      {L.note}")
    lines += [
        "",
        "The Composite (reference) is the pipeline's flattened preview of the whole",
        "stack — use it to check your arrangement; it is not meant to be layered in.",
        "",
    ]
    return "\n".join(lines)


def export_capcut(
    out_dir: str,
    *,
    base: str,
    captions: Optional[str] = None,
    composite: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """Copy the rendered layers + composite into ``out_dir`` and write the README.

    Returns a summary dict (bundle dir, copied media paths, readme path, count).
    With ``dry_run`` nothing is written — the plan is computed and returned.
    """
    layers = gather_layers(base, captions, composite)
    if not layers:
        raise ValueError("capcut export needs at least a base layer")
    plan = plan_copy(out_dir, layers)
    readme_path = str(Path(out_dir) / "README.txt")
    if not dry_run:
        (Path(out_dir) / "media").mkdir(parents=True, exist_ok=True)
        for src, dest in plan:
            shutil.copy2(src, dest)
        Path(readme_path).write_text(readme_text(layers), encoding="utf-8")
    return {
        "bundle": str(out_dir),
        "media": [dest for _, dest in plan],
        "readme": readme_path,
        "layers": len(layers),
    }
