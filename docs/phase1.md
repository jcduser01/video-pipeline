# Phase 1 — Probe

Phase 1 proves the trust model: that a machine-produced layer is good enough for
the operator to accept. It delivers three things (INI-085 brief §5.1, Phase 1 DoD).

## 1. Repo scaffold + project contract

- `schema/project.schema.json` — the `project.yml` contract. Required: `identity`
  (→ glossary layer), `profile` (→ dimensions + safe-zone spec). `rough_cut.trim_filler`
  defaults true; set false to preserve audio continuity (live-off-the-mixer DJ sets).
- `src/video_pipeline/manifest.py` — loads/validates `project.yml`, parses the
  folder-name convention `YYYY-MM-DD <Token> Project - <Hook>`, derives the render
  filename `YYYY-MM-DD-<token>-<kebab-hook>.mp4`. The `<Token>` is profile-supplied.
- `src/video_pipeline/project.py` — scaffolds `{project-id}/` with
  `source/ work/ review/ out/ render/`. `source/` is read-only by convention;
  `render/` ships **empty** so future automation can treat "file present in
  `render/`" as "deliverable ready".
- `config/safezone/` and `config/glossary/` scaffolds (see their READMEs).

Projects are **data**, created under `~/Video/Projects/` on the daily driver — never
committed to this repo.

## 2. Safe-zone spec generator

`src/video_pipeline/safezone/` derives a machine-readable spec **from a reference
template PNG** (adkit.so). The danger region is an opaque overlay; the safe region
is transparent. The generator:

1. classifies pixels danger/safe (alpha-keyed; auto-falls back to colour for
   flattened templates),
2. isolates the main safe region with a scanline flood-fill from its centroid,
3. emits a row-convex run-length encoding (`bands`) **and** an equivalent
   orthogonal `polygon` — both **notch-aware**,
4. asserts the two views describe the same area (shoelace == mask area).

The Reels safe zone is **not a rectangle**: it has a rectangle notched out of the
lower-right corner for the action-button cluster. The committed spec
(`config/safezone/reels-9x16.safezone.json`) captures it exactly:

- bounding box `(35, 250) → (1045, 1470)`
- notch (danger) `(915, 1117) → (1045, 1470)`
- 6-vertex polygon; safe fraction 57.2%

**Update-resilient:** when Instagram changes the safe zone, drop in a new template
PNG and re-run `safezone-gen` — no code change.

## 3. Reframe probe

> **Extended since.** This documents reframe as delivered in Phase 1 (a
> portrait-only probe). It was later generalized to arbitrary aspect/resolution
> targets (INI-090) and split into a propose/render pair driven by an editable
> `reframe.def`, with framing intents and a composition lock (INI-091). For the
> current reframe subsystem see [`reframe.md`](reframe.md); the three layers below
> still describe the tracker → plan → crop core that underlies it.

`src/video_pipeline/reframe/` does landscape → portrait auto-reframe, in three
separable layers:

- `tracker.py` — subject detection behind a `SubjectTracker` Protocol.
  `OpenCVFaceTracker` is the **default** (`--tracker opencv`): the Haar cascade
  ships inside the `opencv-python` wheel, so there is no model download and no
  dependence on MediaPipe's API churn. `MediaPipeTracker` (`--tracker mediapipe`)
  uses the current MediaPipe **Tasks API** — the legacy `mp.solutions` API was
  removed from recent wheels — and downloads a model bundle on first use.
  `FixedTracker` drives tests with no native deps.
- `plan.py` — subject centres → a stabilised crop window: exact output aspect,
  clamped inside the frame, EMA-smoothed with a dead-band + per-sample shift clamp
  so the reframe doesn't jitter. `static` mode (probe default) emits one robust
  window; `dynamic` mode emits one per sample.
- `crop.py` — a crop plan → an FFmpeg command (static `crop,scale` or a time-keyed
  `crop` expression). Audio is stream-copied (reframe is spatial-only).

## Verification

In-sandbox (no MediaPipe/footage):

```bash
PYTHONPATH=src:. python -m unittest discover -s tests   # 55 tests, all green
```

End-to-end mechanics were smoke-tested with the real FFmpeg binary and a
`FixedTracker`: a synthetic 1920×1080 landscape clip reframed to **1080×1920**
with the crop biased toward the subject and audio preserved — confirming the
probe's plumbing. This validates everything except the live MediaPipe detection.

## Phase 1 DoD — remaining acceptance (daily driver / CEO)

The one item that cannot be closed in the sandbox: **the reframe probe produces a
vertical crop the CEO accepts on a real clip.** On Ono-Sendai:

```bash
pip install -e '.[reframe]'
video-pipeline reframe "<real landscape clip>" -o /tmp/probe-9x16.mp4 --profile reels-9x16
```

Then the CEO reviews `/tmp/probe-9x16.mp4`. Acceptance of that output closes the
Phase 1 trust-model gate and authorises Phase 2 (rough cut + decision file).
