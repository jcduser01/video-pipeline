"""Remotion render seam — the style-layer renderer (daily-driver / Node only).

Remotion is a Node/React renderer; it owns caption *look*. The pipeline's job is
to hand it a correct props JSON (built by :mod:`video_pipeline.captions.export`)
and shell out to ``npx remotion render`` against the bundled ``remotion/`` project.
This is the exact analogue of the mlx-whisper seam: native toolchain, exercised on
Ono-Sendai, not in the JasonOS sandbox. The argv builder (:func:`remotion_render_
command`) is pure and unit-tested; the subprocess call is daily-driver-bound.

The bundled composition id is ``Captions``; it reads ``--props`` and renders a
transparent caption overlay (``.mov``, ProRes 4444 — alpha preserved) sized to the
props ``dimensions``. Composite it over the reframed video in the editor (or via a
later FCPXML track), so captions stay an independent, restyleable layer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional

# Repo root -> the bundled Remotion project. parents: captions -> video_pipeline
# -> src -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
REMOTION_DIR = _REPO_ROOT / "remotion"
DEFAULT_COMPOSITION = "Captions"


def remotion_render_command(
    props_path: str,
    output_path: str,
    composition: str = DEFAULT_COMPOSITION,
    remotion_dir: Optional[str] = None,
    codec: str = "prores",
    prores_profile: str = "4444",
) -> List[str]:
    """Build the ``npx remotion render`` argv. Pure — no process is spawned.

    ProRes 4444 keeps the alpha channel so the overlay composites cleanly. Paths
    are passed absolute; ``--props`` points at the JSON written by
    :func:`video_pipeline.captions.export.write_remotion_props`.
    """
    rdir = Path(remotion_dir) if remotion_dir else REMOTION_DIR
    return [
        "npx",
        "remotion",
        "render",
        str((rdir / "src" / "index.ts")),
        composition,
        str(Path(output_path).resolve()),
        f"--props={Path(props_path).resolve()}",
        f"--codec={codec}",
        f"--prores-profile={prores_profile}",
    ]


def render_overlay(  # pragma: no cover - daily-driver: needs Node + Remotion
    props_path: str,
    output_path: str,
    composition: str = DEFAULT_COMPOSITION,
    remotion_dir: Optional[str] = None,
    dry_run: bool = False,
) -> List[str]:
    """Render the styled caption overlay via Remotion. Returns the argv.

    Requires Node + an installed ``remotion/`` project (``npm install`` run once
    on the daily driver). Raises a clear error if the project is missing.
    """
    rdir = Path(remotion_dir) if remotion_dir else REMOTION_DIR
    if not (rdir / "package.json").exists():
        raise RuntimeError(
            f"Remotion project not found at {rdir}. On the daily driver run "
            f"`npm install` in that directory once before rendering captions."
        )
    cmd = remotion_render_command(
        props_path, output_path, composition=composition, remotion_dir=str(rdir)
    )
    if not dry_run:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(cmd, check=True, cwd=str(rdir))
    return cmd
