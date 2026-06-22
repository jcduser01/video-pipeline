# INI-089 — Overlay Subsystem (timed/placed overlays + producers)

Status: **Phase A in progress.** This doc is the engineering plan and the
build-state record for the overlay subsystem. It is architecture-first: one
primitive, thin producers on top — not three features.

## The shape (why a primitive, not three features)

The compositor today stacks **full-frame, full-duration** transparent layers
(`composite/render.py`, `overlay=0:0`); captions are the only real layer feeding
it. Every content overlay is therefore hand-placed in the NLE. The expensive part
is not drawing the box — it is deciding **when** each overlay is on screen,
frame-matched to the narration and clear of the caption layer and safe zone.

So the subsystem is one missing primitive — a **timed/placed overlay layer** —
and thin **producers** that emit overlays into it:

- **Primitive** — a layer with a source-time window `[start, end)`, a placement
  (`full-bleed` / `bottom-half` / `pip-rect`), a trivial transition (cut or fade),
  and an emitted **`overlay.occupancy`** descriptor so caption placement and
  safe-zone QC stay aware of it.
- **Product** — **`overlay.def`**, an editable decision file (one scannable line
  per overlay), mirroring `roughcut.def` / `caption.def`. Edit text, re-render.
- **AI leverage** — a transcript→window proposer fills each `[start, end)` from the
  span where the overlay is discussed (the word-level transcript already exists).
  The LLM stays out of the render path.

## Reuse, not reinvention

| Need | Existing machinery |
|---|---|
| Decision-file product (round-trip, header, flow-row YAML) | `roughcut/decision.py` — mirrored exactly by `overlay/decision.py` |
| Source-time → cut-time remap at editor handoff | `fcpxml/timeline.py` `kept_spans` / `source_to_cut` / `remap_cue` — overlay cues ride the same map |
| Word-level transcript for the window proposer | `roughcut/transcript.py` (`Transcript` / `Word`) |
| Safe-zone geometry for caption-dodge + QC | `safezone/spec.py` (`SafeZoneSpec`, bands, `rect_clear`) |
| GUI surfacing with zero recompile | `schema/definition.py` — a new step/task/artifact appears on next emit |
| Composite flatten | `composite/render.py` — extended with a timed/placed filtergraph |

## Module layout

```
overlay/
  decision.py    # OverlayItem + OverlayList + overlay.def round-trip   [BUILT]
  occupancy.py   # placement→rect + overlay.occupancy descriptor        [BUILT]
  propose.py     # transcript→window proposer (writes overlay.def)      [TODO]
  runner.py      # resolve overlay.def + occupancy → PlacedOverlay,     [TODO]
                 # run the composite, emit occupancy json
composite/render.py
  PlacedOverlay + timed_overlay_filtergraph
  + ffmpeg_timed_composite_command                                       [BUILT]
```

## Data contracts

### `overlay.def` (the product) — `overlay/decision.py`

Source-time YAML, one overlay per flow-style line so a window nudge is a one-line
edit. `start`/`end` are **source-time seconds** (same timebase as caption cues and
rough-cut segments) so the proposer and the cut-time remap both apply unchanged.

Per-overlay fields: `kind` (image | video | card), `src`, `start`, `end`,
`placement` (full-bleed | bottom-half | pip-rect), `rect` (x,y,w,h — pip only),
`transition` (cut | fade) + `fade` seconds, `audio` (keep | duck | mute — video),
`scale` (fit | fill), `matte` (none | selfieseg — Phase C), `text` (human label).
Validation lives in `OverlayItem.__post_init__`: enum membership, positive window,
cut⇒fade 0, fade ≤ half the window, pip⇒rect (and rect only with pip).

### `overlay.occupancy` (the cross-layer descriptor) — `overlay/occupancy.py`

Each overlay resolves to a **geometric** rect (not safe-zone-clipped) over its
window. Emitted so caption placement dodges a busy region and QC flags a danger-zone
intrusion — no branch reads another branch's pixels (SADD §3.3). A matted PiP
(Phase C) keeps the geometric PiP rect either way. `active_at(t)` and
`intersects_rect(...)` are the consumer helpers.

### Render primitive — `composite/render.py`

`PlacedOverlay(path, x, y, w, h, start, end, fade, loop)` → `timed_overlay_filtergraph`
builds `scale` + optional alpha `fade` in/out + `overlay=x:y:enable='between(t,…)'`,
stacked low→high z-order to `[outv]`. `ffmpeg_timed_composite_command` adds `-loop 1`
for stills (a single-frame input must loop to persist across its window) and carries
base audio through. Per-overlay audio duck/mute is layered by the runner.

## Built so far (this increment)

- `overlay/decision.py`, `overlay/occupancy.py`, and the `composite/render.py`
  timed-overlay primitive — all pure, all unit-tested (`tests/test_overlay.py`,
  33 tests). Full suite green (296). No render path touched that captions depend on
  (the caption `composite_filtergraph` is untouched; the timed path is additive).

## Remaining for Phase A

1. **`overlay/propose.py`** — match an overlay (by `text`/keyword or an explicit
   span) to its discussed window in the `Transcript`; write `overlay.def`. Pure,
   unit-tested.
2. **`overlay/runner.py`** — read (possibly hand-edited) `overlay.def`, build the
   occupancy descriptor, resolve each item to a `PlacedOverlay` (placement→rect,
   `loop` from `kind`), run the composite, write `overlay.occupancy` json. The
   daily-driver seam (mirrors `composite/runner.py`).
3. **Audio policy** — `duck`/`mute` for video overlays (amix/sidechain or volume on
   the overlay's audio; `keep` passes base audio through as today).
4. **Caption-dodge** — caption placement consumes `overlay.occupancy`: when a cue's
   window intersects an active overlay rect, the caption box avoids it (extends
   `captions/placement.py` to take occupancy rects; coordinate with INI-088 control
   9, the one safe-zone module both touch).
5. **QC consumption** — `qc` reads `overlay.occupancy` and flags overlays intruding
   on the danger polygon (`SafeZoneSpec.rect_clear`).
6. **Cut-time remap at handoff** — overlay cues run through `fcpxml/timeline.py`
   like caption cues; the editor handoff opens with each overlay on its own labeled
   track at the correct cut-time offset.
7. **Schema + CLI** — add the `overlay` step + `overlay.define` / `overlay.render`
   tasks + `overlay.def` / `overlay.occupancy` artifacts to `schema/definition.py`,
   and the matching `overlay` / `overlay-render` CLI subcommands. The SADD already
   reserved the slot; this is the GUI-zero-recompile surfacing.

## Verification model

Pure logic (overlay geometry, decision-file round-trip, occupancy, proposer, argv
assembly, cut-time remap) is unit-tested in-sandbox under the standing TDD
discipline (`python3 -m unittest`, no native deps). Render, fade, and matte seams
are **Mac-side acceptance on real footage** — the sandbox cannot run ffmpeg/Remotion
or the matte. Each phase closes only on observed real-footage DoD (an image overlay
and a video overlay placed full-bleed and bottom-half, captions visibly dodging
them, QC reporting occupancy, the editor handoff opening with the overlay on its own
track at the right cut-time offset), not on shipped artifacts.

## Coordination with INI-088 (now closed)

INI-088 shipped the `--preview-frames` + dispatcher render-and-grab loop (the soft
prerequisite for this phase's render acceptance) and the `captions/placement.py`
safe-zone module. Phase A's caption-dodge work edits that same module — one
consistent pass that reads both the notch rule (INI-088 control 9) and the overlay
occupancy.

## Build order (resolved with CEO 2026-06-21)

A → B → C by delivery risk. **Phase A** (this doc): primitive + image/video
producers. **Phase B**: generated source-card overlay (Chrome/Jina → content JSON →
Remotion card → Phase-A primitive). **Phase C**: self-commentary composite
(MediaPipe SelfieSegmentation default, green-screen-assisted; `matte=none` rerender
→ Premiere chroma key) — highest complexity, lowest frequency, last.
