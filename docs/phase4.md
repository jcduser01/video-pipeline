# Phase 4 — Safe-zone QC

The safe zone is a **derived polygon, not a box** (shaping brief §3.9): a safe
rectangle with a smaller rectangle notched out of the lower-right corner for the
action-button cluster. Phase 4 checks a finished frame layout against that polygon
and against the speaker's face, then produces three outputs:

- a **QC report** — machine-readable JSON + a printable summary, listing every
  caption / logo / CTA / text that intrudes on the danger region (notch
  included), every caption sitting on a face, and every face that drifted into
  the danger region;
- a **danger-zone preview** — the source with the danger region and the flagged
  boxes burned in, for an eyeball check;
- a **clean render** — the deliverable, stream-copied untouched.

As with the rest of the pipeline, the geometry/validation core is pure and
unit-tested; only face detection and the FFmpeg burn-in need the native toolchain.

## Flow

```
caption boxes (Remotion props) ─┐
static brand marks (project.yml) ├─▶ validate ─▶ QC report (JSON + text)
faces (reframe tracker)         ─┘            └▶ danger-zone preview (FFmpeg overlay)
source video ───────────────────────────────────▶ clean render (FFmpeg stream-copy)
```

## The three checks

1. **danger-intrusion** (error) — a protected element (caption / logo / CTA /
   text / graphic) pokes out of the safe polygon into the danger region. Exact
   against the spec's row-convex bands, so the **notch is honoured natively** — no
   rectangle approximation. The report records the danger fraction and whether the
   intrusion hits the notch. This is the headline DoD check.
2. **caption-over-face** (warning) — a caption box overlaps a detected face by
   more than `occlusion_frac` of the caption's area. This is the subject-aware
   check; faces come from the same reframe tracker seam as Phase 1 (OpenCV Haar by
   default, MediaPipe opt-in). Time windows are respected — a caption is only
   tested against faces present while it is on screen.
3. **face-in-danger** (warning) — the speaker's face has drifted into the danger
   region by more than `face_danger_frac` (e.g. a reframe crop pushed them under
   the action buttons).

Only danger-intrusion fails the pass by default (`report.passed`); the
subject-aware checks are warnings to review. `report.clean` is true only when
there are no findings of any severity.

## Why geometry, not pixels

The validator reasons about **element rectangles**, not rendered pixels: caption
boxes come from the Remotion props (the same `safeBox` + cue timings the style
layer renders), brand marks are declared once in `project.yml`, and faces are
detector boxes. That keeps the whole core pure and exhaustively testable, and it
catches the failure *before* a wasteful render. The danger-zone preview is the
pixel artifact — it exists for the human eyeball pass, not for the machine check.

## Configuration

Thresholds and any persistent overlay elements (a fixed logo, a CTA) live in the
project's `project.yml` `qc:` block; the full key list is in
`schema/project.schema.json`.

```yaml
# project.yml
qc:
  occlusion_frac: 0.10        # caption-over-face overlap threshold
  face_danger_frac: 0.20      # face-in-danger threshold
  intrusion_frac: 0.0         # danger tolerance for protected elements (0 = any)
  check_caption_over_face: true
  check_face_in_danger: true
  elements:                   # static marks checked on every render
    - {kind: logo, x: 40, y: 60, width: 120, height: 120, label: brand}
```

CLI flags (`--occlusion-frac`, `--face-danger-frac`, `--intrusion-frac`) override
the project values for one run.

## CLI

```bash
# Full QC: report + danger-zone preview + clean render (daily driver)
video-pipeline qc out/clip-9x16.mp4 \
    --safezone config/safezone/reels-9x16.safezone.json \
    --props work/caption-props.json \
    --project "2026-06-03 Reel Project - Working Title" \
    --report out/qc-report.json \
    --preview out/qc-preview.mp4 \
    --clean out/clip-clean.mp4

# Geometry-only, no render (fast; CI-friendly). --strict exits non-zero on FAIL.
video-pipeline qc out/clip-9x16.mp4 \
    --safezone config/safezone/reels-9x16.safezone.json \
    --props work/caption-props.json \
    --no-face-check --dry-run --report out/qc-report.json --strict
```

`--no-face-check` skips detection (geometry-only danger checks). `--tracker
mediapipe` swaps the face detector. `--strict` makes the command exit non-zero
when the report does not pass, so it can gate an automation step.

## What's tested vs. daily-driver

Fully unit-tested in the sandbox (no native deps): the exact safe/danger area
integration over the spec bands (fractional edges + notch), all three checks and
their thresholds, the time-window pairing for caption-over-face, the report
JSON/text serialization, the danger-preview PNG geometry (danger opaque / safe
transparent / notch opaque), the FFmpeg preview + clean argv, the `qc:` manifest
block, and caption-element gathering from Remotion props. A deliberately-violating
caption box (its right end in the notch) is asserted to be flagged — the Phase-4
DoD demonstration.

Daily-driver only (the seams, by design): live face detection (OpenCV/MediaPipe)
and the actual FFmpeg burn-in of the preview / clean render.

## Remaining Phase-4 DoD (CEO / daily driver)

- Run `qc` on a real reel (the reframed + captioned clip) and confirm the QC
  report + danger-zone preview agree with what the eye sees.
- Demonstrate the flag on a **deliberately-violating frame** — e.g. nudge a
  caption (or a `qc:` logo) into the notch — and confirm QC reports the
  danger-intrusion with the notch flagged. Accept, then merge the fork→canonical
  PR.
