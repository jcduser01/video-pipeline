"""Preview proxy — bake a transparent layer over a checkerboard into plain h264.

The control-tower previewer plays layers in a WKWebView ``<video>``, which can't
be relied on to decode alpha (ProRes 4444 / HEVC-alpha) in isolation. Instead of
gambling on that (SADD §9's residual risk), we render a **preview proxy**: the
transparent layer composited over a neutral checkerboard into a universal h264
.mp4 the webview always plays. The checkerboard reads unambiguously as "this part
is transparent."

These proxies are GUI-only — they are never bundled into an editor export. Pure
ffmpeg-argv assembly (:func:`ffmpeg_proxy_command`) + a thin runner, like the
other renderers. The actual render is a daily-driver seam (real ffmpeg).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Sequence


def checkerboard_filtergraph(
    width: int,
    height: int,
    fps: int,
    *,
    square: int = 16,
    light: int = 165,
    dark: int = 110,
) -> str:
    """The ``filter_complex`` that lays input 0 (the alpha layer) over a generated
    checkerboard of ``square``-px cells alternating two neutral grays.

    The background is a generated ``color`` source; ``geq`` paints the checker on
    its luma (chroma pinned neutral so the cells stay gray). The layer is overlaid
    on top and the result is flattened to ``yuv420p`` for h264. Commas inside the
    ``geq`` expression are escaped so ffmpeg passes them to the expression
    evaluator rather than reading them as filter separators.
    """
    checker = (
        f"if(eq(mod(floor(X/{square})+floor(Y/{square})\\,2)\\,0)"
        f"\\,{light}\\,{dark})"
    )
    return (
        f"color=c=gray:s={width}x{height}:r={fps},"
        f"geq=lum='{checker}':cb=128:cr=128[bg];"
        f"[bg][0:v]overlay=shortest=1,format=yuv420p[outv]"
    )


def ffmpeg_proxy_command(
    layer_path: str,
    output_path: str,
    *,
    width: int,
    height: int,
    fps: int = 30,
    square: int = 16,
    crf: int = 20,
    preset: str = "veryfast",
) -> List[str]:
    """Assemble the ffmpeg argv that bakes ``layer_path`` over a checkerboard.

    A throwaway preview (a faster preset, no audio), not a deliverable. The
    background is generated, so the layer is the only input.
    """
    if not layer_path:
        raise ValueError("proxy needs a layer to render")
    return [
        "ffmpeg",
        "-y",
        "-i", layer_path,
        "-filter_complex",
        checkerboard_filtergraph(width, height, fps, square=square),
        "-map", "[outv]",
        "-an",
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        output_path,
    ]


def render_proxy(
    layer_path: str,
    output_path: str,
    *,
    width: int,
    height: int,
    fps: int = 30,
    square: int = 16,
    dry_run: bool = False,
) -> List[str]:
    """Build, and unless ``dry_run`` run, the preview-proxy render. Returns argv."""
    cmd = ffmpeg_proxy_command(
        layer_path, output_path, width=width, height=height, fps=fps, square=square
    )
    if dry_run:
        return cmd
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return cmd
