# video-pipeline

AI **pre-editing** pipeline for vertical short-form video (Instagram Reels first;
other targets are profiles, not forks). The pipeline does the mechanical labor —
cut to length, reframe to vertical, caption, place overlays inside the safe zone
— and hands off an **editable project** (FCPXML → Premiere Pro). Pacing, taste,
transitions, and music placement stay with the editor.

> The edit-decision artifacts are the product; renders are regenerable views of
> them. You review and adjust a plain-text file, then re-render — the source
> footage is never modified.

## Status

| Phase | What | State |
|---|---|---|
| 1 — Probe | Repo scaffold + project contract; safe-zone spec generator (template PNG → notch-aware polygon/mask); reframe probe (subject tracking → FFmpeg vertical crop) | ✅ accepted on real footage |
| 2 — Rough cut | Transcription seam (mlx-whisper) + silence fallback; pure proposer (filler / false-start / dead-air, honors `trim_filler`); **editable decision file** (the product); FFmpeg trim/concat render | ✅ accepted on real footage |
| 3 — Captions | Glossary-corrected 2–4-word chunker (timing layer); **editable caption file** (the product); safe-zone-aware placement; SRT export; **Remotion** styled-overlay renderer (style layer) driven by a props contract; layered caption-style config | ✅ accepted on real footage |
| 4 — Safe-zone QC | Validate a frame layout against the derived safe polygon (notch included): flag captions/logos/CTAs intruding on the danger region, captions over the speaker's face, and faces in the danger region; **QC report** (JSON + printable) + **danger-zone preview** + **clean render** | ✅ built (pure logic tested; face detection + FFmpeg burn-in are the local acceptance gate) |
| 5 — Editor handoff | Assemble the editor project: the decision file's KEEP segments over the reframed clip on a **Base Cut** track + the caption overlay on a **Captions** track; cues **remapped to cut time** so they line up with the compressed timeline. Two formats — **Premiere-compatible FCP7 XML (default)** and FCPXML 1.10 — so it opens natively in Premiere Pro (which does not read FCPXML), Resolve, or Final Cut | ✅ built (both serializers + remap tested; opening the project + overlay render are the local acceptance gate) |
| 6 — Overlays | Timed/placed overlay layer — a still image, a video asset, or a generated **source card** — on a transcript-proposed window, as an editable **`overlay.def`**; emits an **`overlay.occupancy`** descriptor so captions **dodge** overlays and QC flags intrusions; overlay windows **remap to cut time** at handoff. See [Overlays](#overlays). | ✅ built (pure logic tested in-sandbox; ffmpeg / Remotion render acceptance on real footage is the local gate) |

Captions are **two layers, kept separate**: the pipeline owns *timing* (transcript
→ cues, glossary-corrected) and *placement* (a safe-zone-derived box); **Remotion**
owns *look* (`remotion/`). The props JSON is the seam between them.

## How it runs

The pure logic (project contract, safe-zone geometry, rough-cut proposal, caption
chunking/placement/export) is plain Python and runs anywhere — it is what the test
suite covers, with no native toolchain required.

A few steps need a local machine with the native toolchain installed, so they run
on the editing/render machine rather than in CI:

- **Transcription** (`mlx-whisper`) — Apple-Silicon Mac; local, word-level
  timestamps, nothing leaves the machine. A precomputed Whisper-JSON transcript
  can be supplied instead, which removes this requirement.
- **Reframe / rough-cut render** — needs an `ffmpeg` binary.
- **Caption / source-card render** — needs Node + the bundled `remotion/` project.
- **Composite / overlay render** — needs `ffmpeg` (flatten the base + caption /
  overlay layers; place timed/scaled overlays).

## Install

```bash
pip install -e .            # core: numpy, Pillow, PyYAML, jsonschema
pip install -e '.[reframe]' # native extras: mediapipe, opencv-python
pip install -e '.[roughcut]'# transcription extra: mlx-whisper (Apple-Silicon)
pip install -e '.[dev]'     # pytest
```

The core install and the test suite need **no** native MediaPipe/OpenCV build; the
reframe probe and transcription do.

**Using `uv`?** `make ready` rebuilds everything outside git after a fresh pull —
the Python environment with all extras (`uv sync --all-extras`) and the Remotion
renderer's `node_modules` (`npm install`). Run it whenever you pull a new version
or reset the working tree. `make test` runs the suite; `make` lists the targets.

> Note: a bare `uv sync` / `uv run` installs only the default dependencies — it
> **skips the optional extras**, so `mlx-whisper` won't be present. Use `make
> ready` (or `uv sync --all-extras`) so transcription works.

## Usage

Replace `<identity>` with an identity defined under `config/glossary/identities/`
and `config/caption-styles/identities/` (these layer brand vocabulary and caption
styling over the shared defaults).

```bash
# 1. Regenerate the safe-zone spec from a template PNG (update-resilient)
video-pipeline safezone-gen config/safezone/instagram-safe-zone-reels-9x16.png \
    --profile reels-9x16 -o config/safezone/reels-9x16.safezone.json

# 2. Scaffold a new project (creates source/work/review/out/render + project.yml)
video-pipeline project-init "2026-06-03 Reel Project - Working Title" \
    --identity <identity> --profile reels-9x16

# 3. Reframe a landscape clip to vertical (needs ffmpeg; --dry-run prints the cmd)
video-pipeline reframe source/clip.mp4 -o out/clip-9x16.mp4 --profile reels-9x16

# 4. Rough cut -> editable decision file (mlx-whisper, or pass --transcript)
video-pipeline roughcut source/clip.mp4 -o review/decision.yml --render work/rough.mp4

# 5. Captions -> editable caption file + SRT + Remotion props (mlx-whisper or --transcript)
video-pipeline captions source/clip.mp4 -o review/captions.yml --identity <identity> \
    --srt out/captions.srt --props work/caption-props.json \
    --safezone config/safezone/reels-9x16.safezone.json

# 6. Render the styled caption overlay from the (editable) caption file (needs Remotion)
video-pipeline captions-render review/captions.yml -o out/captions.mov \
    --identity <identity> --safezone config/safezone/reels-9x16.safezone.json

# 6a. Per-run caption styling (font / size / colors / stroke). The same flags work
#     on both `captions` and `captions-render`; omitted flags fall through to the
#     identity/global caption-style config. Sizes/widths are capped and fonts are
#     allowlisted at the Python boundary (config/caption-styles/README.md).
video-pipeline captions-render review/captions.yml -o out/captions.mov \
    --identity <identity> --safezone config/safezone/reels-9x16.safezone.json \
    --font-family Helvetica --font-size 96 \
    --fill-color "#FFFFFF" --stroke-color "#000000" --stroke-width 8

# 6b. Background plate behind the captions (whole-block rounded rectangle, padded
#     to clear the stroke). --bg turns it on; --bg-color / --bg-radius shape it.
video-pipeline captions-render review/captions.yml -o out/captions.mov \
    --identity <identity> --safezone config/safezone/reels-9x16.safezone.json \
    --bg --bg-color "#000000" --bg-radius 24

# 6c. Render then grab N representative still frames (composited over a neutral
#     plate) for visual verification — written to <output>-frames/ by default.
video-pipeline captions-render review/captions.yml -o out/captions.mov \
    --identity <identity> --safezone config/safezone/reels-9x16.safezone.json \
    --preview-frames 4

# 7. Safe-zone QC: report + danger-zone preview + clean render
#    (--no-face-check --dry-run is a fast, native-free geometry check)
video-pipeline qc out/clip-9x16.mp4 \
    --safezone config/safezone/reels-9x16.safezone.json \
    --props work/caption-props.json \
    --report out/qc-report.json --preview out/qc-preview.mp4

# 8. Assemble the editor handoff (base cut over the reframed clip + captions).
#    Default format is Premiere-compatible FCP7 XML; --format fcpxml for Resolve/FCP.
#    Also writes a cut-time caption file; render that to the overlay it references.
video-pipeline handoff review/decision.yml -o out/reel.xml \
    --reframed out/clip-9x16.mp4 --captions review/captions.yml --profile reels-9x16
video-pipeline captions-render out/reel.captions.cut.yml -o out/reel.captions.mov \
    --safezone config/safezone/reels-9x16.safezone.json
```

Captions need real words — supply mlx-whisper (the `[roughcut]` extra) or a cached
Whisper-JSON transcript via `--transcript` (e.g. the one the rough-cut phase wrote
to `work/`). The Remotion overlay needs the bundled project installed once:
`cd remotion && npm install`.

## Overlays

Place timed content overlays — a still image, a video asset, or a generated
article/news **source card** — on a window proposed from the transcript, as an
editable `overlay.def` (one line per overlay, the same decision-file model as the
rough cut and captions). The pipeline emits an `overlay.occupancy` descriptor so
caption placement **dodges** overlays for the span they are on screen and QC flags
any that intrude on the danger zone; overlay windows are **remapped to cut time**
at editor handoff like caption cues. The window proposer matches an overlay to the
span where it is discussed — the LLM never touches the render path.

```bash
# 1. Author the overlay decision file — one --add per overlay. Set the window
#    explicitly (start/end) or propose it from a spoken phrase (at=…) with
#    --transcript. Hand-edit overlay.def afterward, then render.
video-pipeline overlay -o work/overlay.def.yml --profile reels-9x16 \
    --transcript work/transcript.json \
    --add "kind=image;src=assets/chart.png;start=3.2;end=7.8;placement=bottom-half;transition=fade;fade=0.3" \
    --add "kind=video;src=assets/clip.mov;at=the demo;placement=pip-rect;rect=60,1180,420,560;audio=duck"

# 2. (Source card) Capture a URL into an editable card-content JSON. The live fetch
#    (Chrome / Jina) runs on the daily driver; --from-json structures a saved
#    capture without a fetch. Render the card to a layer, then point a kind=card
#    overlay's src at it.
video-pipeline overlay-card https://example.com/article -o work/card.content.json

# 3. Composite the placed/timed overlays over the base + emit the occupancy
#    descriptor captions/QC consume (frame size from the safe-zone spec; ffmpeg).
video-pipeline overlay-render work/overlay.def.yml -i work/base.mp4 \
    -o review/overlay-composite.mp4 \
    --safezone config/safezone/reels-9x16.safezone.json \
    --occupancy work/overlay.occupancy.json
```

Placements are `full-bleed`, `bottom-half`, or a `pip-rect` (with an explicit
`x,y,w,h`); transitions are a hard `cut` or a `fade`; a video overlay's own audio
can `keep` / `duck` / `mute`. Complex masks, keyframed/stylized motion, and
razor-frame timing stay in the NLE by design — the overlay layer does cut or
simple fade only.

## Control-tower schema (GUI)

The pipeline is the single source of truth for an optional desktop control-tower
GUI — [`video-pipeline-gui`](https://github.com/jasoncookdesign/video-pipeline-gui), a Tauri app that runs these
steps, streams their output, and previews the layers each produces. The pipeline
emits the steps, tasks, artifacts, parameters, and export targets the GUI reads at
launch to build its forms and previewer; adding a step on this side surfaces in the
GUI with no recompile (the overlay step above, including its repeatable per-overlay
table, is surfaced this way). See [`docs/gui-schema.md`](docs/gui-schema.md) for the
pipeline side of the contract.

```bash
video-pipeline schema --format yaml|json [-o file]   # emit the schema
video-pipeline schema --check                        # validate without emitting
```

## Configuration

Settings come from two places, low to high precedence:

1. **Repo defaults**, layered per identity — `config/glossary/` (caption
   vocabulary) and `config/caption-styles/` (caption look + chunking). Each has a
   `global.*` file plus `identities/<identity>.*` that override it.
2. **Per-project overrides** in the project's `project.yml`. A project wins over
   the identity, which wins over global, which wins over the built-in defaults.

The full, authoritative list of keys and types is `schema/project.schema.json`;
`config/glossary/README.md` and `config/caption-styles/README.md` document the
layered files. The commonly-tuned settings:

```yaml
# project.yml — lives in each project folder
identity: <identity>     # selects the glossary + caption-style layer
profile: reels-9x16      # output dimensions + safe-zone spec

rough_cut:
  trim_filler: true      # false = no speech-based cuts (preserves audio continuity)
  silence_gap_s: 0.6     # gap (s) above which dead air is trimmed
  keep_pad_lead_s: 0.06  # padding kept before each kept span
  keep_pad_tail_s: 0.15  # padding kept after each kept span

captions:
  # Words-per-cue RANGE — the main caption control (not two modes, one range):
  #   min_words: 1, max_words: 1  -> single-word, word-by-word captions
  #   min_words: 2, max_words: 4  -> phrase-aware groups (default)
  #   min_words: 1, max_words: 2  -> mostly singles, pairs when they read better
  min_words: 2
  max_words: 4
  target_words: 0        # words-per-cue to aim for; 0 = auto (midpoint of range)
  max_chars: 24          # hard character cap per cue
  max_gap_s: 0.6         # pause (s) that forces a caption break
  karaoke: false         # true = active-word highlight (each word lights up as spoken)
  uppercase: true        # render text uppercase
  position: lower-third  # caption box anchor: upper-third | center | lower-third
  h_offset: clear-notch  # horizontal placement: clear-notch (widest notch-free
                         #   span, may bias off-center) | center (symmetric, still
                         #   clears the notch). Per-run flag: --h-offset.
  # font_family, font_size, fill_color, stroke_color, emphasis_color, …
  #   — full style keys are in config/caption-styles/README.md
```

**Caption breaking** is phrase-aware and balanced within the range: it favours
even cue widths, avoids one-word widows (when `min_words > 1`), and prefers to
break *before* function words ("the", "and", "I") rather than stranding them. The
range is the only control you normally touch — set `1`/`1` for word-by-word,
`2`/`4` for phrases. Output is the editable caption file, so any cue can still be
hand-adjusted.

**Karaoke** (`karaoke: true`) highlights each word as it is spoken — the active
word takes the accent colour and grows slightly, earlier words stay lit, later
words are dimmed. Timing comes from the transcript's per-word timestamps (it
even-splits a cue's duration as a fallback). It composes with any range.

**Trying variations without editing files.** The `captions` command takes
overrides that win over the config, so you can A/B settings from the terminal:

```bash
video-pipeline captions <clip> -o review/captions.yml --identity <identity> \
    --min-words 1 --max-words 1      # single-word captions
video-pipeline captions <clip> -o review/captions.yml --identity <identity> \
    --min-words 2 --max-words 4 --karaoke   # phrase groups + karaoke highlight
```

## Test

```bash
pytest                                 # with the dev extra
python -m unittest discover -s tests   # stdlib-only fallback (no pytest needed)
```

## Layout

```
schema/project.schema.json        project.yml contract (rough_cut + captions + qc blocks)
config/safezone/                  template PNG + generated spec
config/glossary/                  layered caption vocabulary (global + per-identity)
config/caption-styles/            layered caption style/chunk config (global + per-identity)
remotion/                         Remotion caption renderer (style layer; Node)
src/video_pipeline/
  safezone/                       template PNG -> SafeZoneSpec (polygon + bands)
  reframe/                        tracker (seam) -> crop plan -> ffmpeg command
  roughcut/                       transcript seam -> propose -> decision file -> render
  captions/                       chunk (timing) -> caption file -> placement/export -> Remotion (style)
  overlay/                        timed/placed overlay layer + overlay.def + occupancy; window proposer; card producer (capture -> content -> Remotion card)
  qc/                             validate frame layout vs safe polygon -> report + danger preview + clean
  fcpxml/                         base cut + cue cut-time remap -> FCP7 XML (Premiere) / FCPXML (Resolve, FCP)
  manifest.py  project.py         project.yml load/validate + scaffolding
  glossary.py  cli.py
tests/                            unittest/pytest suite (runs without native deps)
docs/phase1.md … phase5.md        what each phase delivers + acceptance steps
```

Projects are **data**, not code: each lives in its own folder (with `source/` as
untouched input) outside this repo, and is never committed here.
