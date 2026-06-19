"""QC rendering — the danger-zone preview (PIL) + FFmpeg argv. Mostly pure.

Two seams, mirroring the rest of the pipeline:

  - :func:`render_overlay_png` paints a transparent PNG the size of the profile
    frame: the danger region as a translucent wash, the safe polygon outlined,
    and (optionally) each checked element/face boxed — green if clear, red if it
    violates. Pure pixels (PIL + numpy), unit-tested.
  - :func:`build_preview_command` / :func:`build_clean_command` build the FFmpeg
    argv that burns the overlay onto the source (the **danger-zone preview**) and
    stream-copies the source untouched (the **clean render**). Pure argv; the
    actual FFmpeg invocation runs on the daily driver (see :mod:`runner`).
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from ..safezone.spec import SafeZoneSpec
from .report import QCElement, QCReport, Rect, Violation


# ── danger-zone preview PNG ────────────────────────────────────────────────────

def _danger_alpha_array(spec: SafeZoneSpec, color, alpha):
    """RGBA ndarray: danger region = (color, alpha), safe region = transparent."""
    import numpy as np

    h, w = spec.image_height, spec.image_width
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, 0] = color[0]
    arr[:, :, 1] = color[1]
    arr[:, :, 2] = color[2]
    arr[:, :, 3] = alpha
    # Clear the safe bands to fully transparent.
    for b in spec.bands:
        y0 = max(0, b.y0)
        y1 = min(h, b.y1)
        x0 = max(0, b.x0)
        x1 = min(w, b.x1)
        if y1 > y0 and x1 > x0:
            arr[y0:y1, x0:x1, 3] = 0
    return arr


def _violating_rects(report: Optional[QCReport]):
    if report is None:
        return set()
    return {
        (round(v.rect.x0, 1), round(v.rect.y0, 1), round(v.rect.x1, 1), round(v.rect.y1, 1))
        for v in report.violations
    }


def render_overlay_png(
    spec: SafeZoneSpec,
    path: str,
    *,
    report: Optional[QCReport] = None,
    elements: Sequence[QCElement] = (),
    faces: Sequence[QCElement] = (),
    color=(239, 68, 68),
    alpha: int = 110,
    outline: bool = True,
    outline_color=(255, 255, 255),
    outline_width: int = 4,
    clear_color=(34, 197, 94),     # green for clear element boxes
    violate_color=(239, 68, 68),   # red for violating element boxes
    face_color=(59, 130, 246),     # blue for face boxes
    box_width: int = 4,
) -> str:
    """Write the danger-zone preview PNG (profile-frame size) to ``path``.

    The danger region is a translucent wash; the safe polygon is outlined; each
    element box is drawn green (clear) or red (it appears in ``report``'s
    violations); faces are drawn blue. Returns ``path``.
    """
    from PIL import Image, ImageDraw

    arr = _danger_alpha_array(spec, color, alpha)
    img = Image.fromarray(arr, "RGBA")
    draw = ImageDraw.Draw(img)

    if outline and spec.polygon:
        pts = [(int(x), int(y)) for x, y in spec.polygon]
        draw.line(pts + [pts[0]], fill=outline_color + (255,), width=outline_width)

    violating = _violating_rects(report)

    def _box(rect: Rect, col):
        draw.rectangle(
            [int(rect.x0), int(rect.y0), int(rect.x1) - 1, int(rect.y1) - 1],
            outline=col + (255,),
            width=box_width,
        )

    for el in elements:
        key = (round(el.rect.x0, 1), round(el.rect.y0, 1),
               round(el.rect.x1, 1), round(el.rect.y1, 1))
        _box(el.rect, violate_color if key in violating else clear_color)

    for face in faces:
        _box(face.rect, face_color)

    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path


# ── FFmpeg argv (the daily-driver seam) ────────────────────────────────────────

def build_preview_command(
    input_video: str,
    overlay_png: str,
    output: str,
    *,
    overwrite: bool = True,
) -> List[str]:
    """FFmpeg argv: burn ``overlay_png`` onto ``input_video`` -> the preview.

    The overlay PNG is the profile-frame size, so it composites at (0,0). Audio
    is stream-copied (the preview is for an eyeball check, not delivery).
    """
    cmd = ["ffmpeg"]
    if overwrite:
        cmd.append("-y")
    cmd += [
        "-i", input_video,
        "-i", overlay_png,
        "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto",
        "-c:a", "copy",
        output,
    ]
    return cmd


def build_clean_command(
    input_video: str,
    output: str,
    *,
    overwrite: bool = True,
) -> List[str]:
    """FFmpeg argv: the clean render — the deliverable, stream-copied untouched."""
    cmd = ["ffmpeg"]
    if overwrite:
        cmd.append("-y")
    cmd += ["-i", input_video, "-c", "copy", output]
    return cmd
