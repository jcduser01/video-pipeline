# INI-089 — Overlay Subsystem (timed/placed overlays + producers)

Status: **Phase A primitive built + merged; Phase B (source card) core built.**
This doc is the engineering plan and the build-state record for the overlay
subsystem. It is architecture-first: one primitive, thin producers on top — not
three features.

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
  propose.py     # transcript→window proposer (phrase + keyword)        [BUILT]
  runner.py      # resolve overlay.def + occupancy → PlacedOverlay,     [BUILT]
                 # run the composite, emit occupancy json               (audio-mix follow-on)
  card/          # Phase B source-card producer
    content.py   # CardContent + JSON round-trip (the product)          [BUILT]
    capture.py   # PageFetcher seam + card_from_page structuring        [BUILT]
    props.py     # CardStyle + props + kind=card overlay.def item       [BUILT]
                 # + card render argv
composite/render.py
  PlacedOverlay + timed_overlay_filtergraph
  + ffmpeg_timed_composite_command                                       [BUILT]
remotion/src/Card.tsx (+ CardProps, Root registration)                   [BUILT, Mac-render]
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

1. ~~`overlay/propose.py`~~ — **BUILT** (phrase + keyword-cluster window proposer).
2. ~~`overlay/runner.py`~~ — **BUILT** (`resolve_placed_overlays` placement→rect +
   `loop` from kind; `write_occupancy`; `render_overlays` composite seam mirroring
   `composite/runner.py`). Audio mixing is the one carve-out (item 3).
3. **Audio policy** — `duck`/`mute` for video overlays (amix/sidechain or volume on
   the overlay's audio; `keep` passes base audio through as today). The runner
   currently drops a video overlay's own audio (safe `mute` default).
4. ~~Caption-dodge~~ — **BUILT.** `captions/placement.py:caption_box_avoiding`
   relocates a caption to the nearest clear anchor (lower→upper→center) when its box
   hits an overlay; `overlay/occupancy.py:rects_active_in_window` / `avoid_windows`
   feed it. `captions/export.build_props_from_safezone(avoid_windows=…)` emits a
   **per-cue** `box` only for cues whose window overlaps an overlay (props
   schemaVersion 3; `Captions.tsx` uses `cue.box ?? safeBox`). Full-bleed overlay =
   best-effort (advisory QC warns). Per-cue, so captions move only where an overlay
   actually sits. Render acceptance Mac-side.
5. ~~QC consumption~~ — **BUILT.** `qc/validate.py` gains an `overlays=` channel
   (kind `overlay`, from `overlay.occupancy` via `overlay_elements`) and a
   **caption-over-overlay** warning — the residual a full-bleed overlay leaves that
   caption-dodge could not relocate (mirrors caption-over-face, time-window aware).
   Overlays are deliberately **not** danger-intrusion-checked (a full-bleed overlay
   covers the danger zone by design). `QCReport` gains `overlays_checked`; the runner
   loads `occupancy_path` and `caption_elements_from_props` now honours each cue's
   dodged `box`. Render acceptance Mac-side.
6. ~~Cut-time remap at handoff~~ — **BUILT** (data path). `fcpxml/timeline.py`
   `remap_overlay` / `remap_overlays` ride the same `kept_spans` mapping as caption
   cues: overlays in dropped regions drop, straddlers clip, fades shrink (frame-
   aligned) to fit, whole-clip KEEP is identity. `assemble_project(overlays_def_path=…)`
   writes the cut-time overlay file (`<project>.overlays.cut.yml`), the analogue of
   the cut-time caption file. **Remaining:** the FCPXML/XMEML per-overlay *track*
   clip (each overlay on its own labeled lane) — needs per-overlay rendered assets
   (the runner's per-layer render), so it follows the schema/CLI work.
7. **Schema + CLI** — add the `overlay` step + `overlay.define` / `overlay.render`
   tasks + `overlay.def` / `overlay.occupancy` artifacts to `schema/definition.py`,
   and the matching `overlay` / `overlay-render` CLI subcommands. The SADD already
   reserved the slot; this is the GUI-zero-recompile surfacing.

## Phase B — generated source card

The highest-value producer: an article/news card timed to the spoken span. It is a
**producer on the Phase-A primitive**, not a parallel feature — the card renders to
an alpha layer that becomes a `kind=card` entry in `overlay.def`, placed and
windowed by the primitive. Built to the same content-vs-look split as captions.

**Content (the reviewable product) — `overlay/card/content.py`.** `CardContent`
(`heading`, `body`, `footer`, `image`, `citation`, `source_url`) is a small JSON
the CEO edits before render — fix a clumsy summary, trim the body, swap the image.
Lossless round-trip, heading required.

**Capture → content — `overlay/card/capture.py`.** Split like the transcriber seam:
the **fetch is a seam** (`PageFetcher` Protocol — a Chrome-MCP fetcher and a
Jina-reader fetcher run where the network is, `FixedFetcher` for tests), the
**structuring is pure** (`card_from_page`: heading from title, body from the lead
paragraphs up to a char budget, footer from byline→site-name, citation from the
domain, lead image). No LLM in the path — deterministic, reviewable.

**Look — `overlay/card/props.py` + `remotion/src/Card.tsx`.** `CardStyle` (neutral
defaults; identity/brand overrides layer on) drives a deterministic Remotion `Card`
composition. The card is a **static tile** rendered at its placement-rect size; the
on-screen window and fade are applied by the ffmpeg overlay primitive, so the
component has no per-frame animation (cheap, predictable). `card_to_remotion_props`
is the JSON contract; `card_render_command` is the `npx remotion render` argv
(ProRes 4444 — alpha preserved), mirroring the caption seam.

**Wiring — `build_card_overlay_item`** produces the `kind=card` `overlay.def` entry
(default bottom-half, fade, muted) from a rendered card layer + a proposed window.

**Transcript→window proposer — `overlay/propose.py`** (shared with Phase A's
image/video producers). `propose_window(transcript, query)` returns the source-time
span where a thing is discussed: an exact phrase match first, a keyword-cluster
fallback second (stopword-aware, sentence-gap-bounded), padded and clamped, or
`None` to leave it to manual placement. This is the AI-leverage spine for every
producer's timing — deterministic, model out of the render path.

### Remaining for Phase B

1. **`overlay/card/render` integration in the runner** — capture (Chrome MCP/Jina,
   Mac-side) → `CardContent` JSON → (CEO edit) → `card_to_remotion_props` → Remotion
   render → alpha tile → `kind=card` `overlay.def` item with the proposed window.
2. **CLI + schema** — an `overlay-card` subcommand (URL → content JSON) and the
   `Card` look surfaced through `schema/definition.py` (shared with Phase A's schema
   work).
3. **Brand pass on `Card.tsx`** — the v1 look is neutral; identity-driven styling
   (per-creator colors/type, like caption identities) and layout polish iterate
   Mac-side on real renders.
4. **Render acceptance on real footage (Mac)** — a captured article renders as a
   branded card at the discussed span, content editable via the JSON, captions clear
   of it (Phase-A occupancy/caption-dodge).

## Verification model

Pure logic (overlay geometry, decision-file round-trip, occupancy, proposer, card
content/structuring/props, argv assembly, cut-time remap) is unit-tested in-sandbox
under the standing TDD
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
