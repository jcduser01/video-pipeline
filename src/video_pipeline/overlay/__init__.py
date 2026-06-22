"""Overlay subsystem (INI-089).

One primitive — a timed/placed overlay layer — feeding thin producers (still
image, video asset, generated card, self-commentary composite). The editable
``overlay.def`` decision file is the product; the composited overlay layer is a
regenerable render of it (mirroring ``roughcut.def`` / ``caption.def``).

Modules:
  - :mod:`decision`   — ``OverlayItem`` / ``OverlayList`` + the ``overlay.def``
                        YAML round-trip (the product).
  - :mod:`occupancy`  — resolve a placement to a pixel rect and emit the
                        ``overlay.occupancy`` descriptor caption placement and QC
                        consume so captions dodge overlays and QC flags intrusions.

The render primitive (positioned/timed/scaled/faded FFmpeg overlay argv) lives in
:mod:`video_pipeline.composite.render` alongside the full-frame caption compositor.
"""

from __future__ import annotations
