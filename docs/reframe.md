# Reframe — multi-target + interactive (INI-090 / INI-091)

The reframe step recomposes a source clip toward a chosen **target format**. It is
no longer the portrait-only probe of Phase 1: INI-090 generalized it to arbitrary
aspect/resolution targets, and INI-091 split it into a propose/render pair driven by
an **editable `reframe.def`** — the same decision-file model as the rough cut,
captions, and overlays. The proposal is a starting point; you nudge the crop in the
def (or, in the GUI, drag the crop box) and re-render. The source is never modified;
the reframed clip is a regenerable view of the def.

> The `reframe.def` *is the product*. The reframed clip is a render of it.

## Two orthogonal axes: the target format (INI-090)

The target is two independent choices, kept deliberately separate so the geometry
math stays integer-exact and the pixel size is a finite, platform-correct ladder
(`src/video_pipeline/target_format.py`).

**Aspect preset — the shape.** Drives the crop geometry, stored as a reduced integer
fraction (cinematic is true 21:9 reduced to 7:3, never a `2.39` float):

| Key | Ratio | Use |
|---|---|---|
| `full-portrait` | 9:16 | Reels / Stories / TikTok / Shorts — the short-form **default** |
| `portrait` | 2:3 | Tall portrait |
| `wide-portrait` | 4:5 | Feed portrait (Instagram) |
| `square` | 1:1 | Square feed |
| `widescreen` | 16:9 | YouTube / desktop / general landscape |
| `cinematic` | 7:3 | Ultrawide / music-video / trailer — a stylistic option, not a feed format |
| `classic-tv` | 4:3 | Classic television |

**Resolution tier — the final pixel size.** A ladder ordered high→low —
`4k`, `1440p`, `1080p`, `720p` — plus **`auto`**. `auto` picks the highest tier whose
canonical target fits inside the reframed crop's native pixels (allowing a 5% upscale
tolerance), steps down when the crop is too small, and falls back to the largest
exact-ratio box inside the crop when even 720p won't fit. The engine never upscales
past the tolerance unless you opt in (`--allow-upscale`). Examples: 2:3 at 1440p =
1200×1800; `auto` will not upscale a crop by more than 5%.

## Framing intents (INI-090)

A named composition preset seeds the crop's punch-in and vertical anchor so the
subject and the caption band are designed together
(`src/video_pipeline/reframe/framing.py`):

| Intent | Scale | Subject anchor | For |
|---|---|---|---|
| `talking-head` | 1.30 | upper third | Pieces-to-camera; punched in on the face, captions low |
| `performer` | 1.00 | high | DJ-at-the-decks; widest native crop, lower band reserved for torso / instrument / captions (**recommended default**) |
| `wide-context` | 1.00 | centred | Show as much of the source as the aspect allows, captions low |

Framing is opt-in on the CLI (omitting `--framing` keeps the legacy auto-tracked
behaviour). The intent is recorded in the def as `framing_intent`; the actual crop
is the framing model below.

## The framing model + `reframe.def`

The canonical crop is three numbers (`src/video_pipeline/reframe/model.py`):

- **`scale`** — punch-in. `1.0` = the widest native crop that fills the target with
  no fill; `>1.0` punches in.
- **`pan_x` / `pan_y`** — the crop **centre** in normalized source coords (0–1).

A proposal writes these from the tracker + framing intent. Hand-editing any of them
is a deliberate override (`custom` flips true, so the GUI knows you took control). A
shape of the def:

```yaml
# THIS FILE IS THE PRODUCT. The reframed clip is a regenerable render of it.
source: source/clip.mp4
target: {aspect: full-portrait, resolution: auto}
mode: static            # static crop | dynamic follow
lock: none              # composition lock axis: none | x | y | both (dynamic)
safe_zone_mode: ...     # normalized 3-mode safe-zone selection (INI-091)
framing_intent: performer
framing: {scale: 1.0, pan_x: 0.42, pan_y: 0.35}
```

Render resolves `framing` to the **exact** pixel crop — the geometry in the def is
the geometry rendered, no re-derivation — and replays the persisted subject track
(`reframe.track.json`) so it needs no native tracker. Audio is stream-copied;
reframe is spatial-only.

## Composition lock (INI-091 Phase 5)

In `dynamic` mode the crop follows the subject. A **composition lock** holds the
set-box on one or both axes instead of following: `--lock x` / `y` / `both`, with the
held placement given by `--pan-x` / `--pan-y` (a relative anchor 0–1). `--lock none`
(default) is the legacy follow behaviour. This is how you pin, say, the horizontal
composition while still tracking vertically.

## Propose / render split

```bash
# 1. Propose: track the subject, write the editable def + persisted track.
#    Reads the project-level target if --project is given; explicit flags win.
video-pipeline reframe-propose source/clip.mp4 -o work/reframe.json \
    --project . --framing performer
#    -> work/reframe.json (edit it) + work/reframe.track.json

# 2. Render: replay the (possibly hand-edited) def -> the reframed clip.
#    No tracking/framing knobs here — the def is the single source of geometry.
video-pipeline reframe-render source/clip.mp4 --reframe-def work/reframe.json \
    -o out/clip-9x16.mp4 \
    --reframed-out work/reframed.mp4 \
    --occupancy-out work/reframe.occupancy.json
```

`--reframed-out` persists the reframed-but-uncut clip as a stable editor-handoff
source (so a later rough-cut re-render of `base` doesn't strand it).
`--occupancy-out` writes the subject occupancy — recomputed from the **final** edited
crop — for the caption layer to dodge.

### One-shot back-compat

The original single `reframe` command still works as a one-shot (track + render in
one call) and accepts the same target/framing/lock flags directly:

```bash
video-pipeline reframe source/clip.mp4 -o out/clip-9x16.mp4 \
    --aspect full-portrait --resolution auto --framing performer
# legacy fixed-dimension form (used when --aspect is omitted):
video-pipeline reframe source/clip.mp4 -o out/clip-9x16.mp4 --profile reels-9x16
```

Per-run override flags on `reframe` / `reframe-propose`: `--aspect`, `--resolution`,
`--framing`, `--scale`, `--subject-y` (bipolar: −1 top, 0 centre, +1 bottom),
`--pan-x` / `--pan-y`, `--lock`, `--mode`, `--tracker` (`opencv` default, bundled, no
download; or `mediapipe`, Tasks API, downloads on first use).

## Project-level target

The target is chosen **once**, upstream of safezone and reframe, in the project's
`project.yml` — it is the source of truth over the legacy `profile`:

```yaml
# project.yml
target:
  aspect: full-portrait   # required; one of the 7 presets above
  resolution: auto        # auto | 4k | 1440p | 1080p | 720p
```

Changing the **aspect** resets `resolution` to `auto` and invalidates everything
pixel- or geometry-dependent downstream (framing + safe zone + captions + QC).
Changing only the **resolution** invalidates the pixel-dependent downstream (safe
zone / captions / QC) but not framing. `reframe-propose --project .` reads this
target; explicit `--aspect` / `--resolution` flags win over it.

## Module layout

```
src/video_pipeline/
  target_format.py        aspect presets + resolution tiers + the Auto resolver (pure)
  reframe/
    tracker.py            subject detection behind a SubjectTracker Protocol (opencv default)
    framing.py            named framing intents -> seed scale + subject anchor
    model.py              the canonical crop math (scale / pan_x / pan_y -> pixel crop)
    decision.py           ReframeDef — the editable def, lossless round-trip
    pipeline.py           propose() / render_inputs_from_def() — the two-task orchestration
    plan.py               crop windows (static one-shot / dynamic per-sample, stabilised)
    crop.py               crop plan -> FFmpeg command
    occupancy.py          subject occupancy windows the caption layer dodges
    probe.py              dimension probe + resolve_output_dims (Auto)
    track_io.py           persisted subject track read/write
```

## What runs where

The target-format math, the framing model, the def round-trip, and the crop/argv
assembly are pure Python and unit-tested in the sandbox with no native deps. The
**subject tracker** (OpenCV / MediaPipe) and the **FFmpeg render** need the native
toolchain, so they run on the editing/render machine — the same local-acceptance
posture as the rest of the pipeline.
