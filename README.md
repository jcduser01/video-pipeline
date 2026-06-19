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
| 3 — Captions | Glossary-corrected 2–4-word chunker (timing layer); **editable caption file** (the product); safe-zone-aware placement; SRT export; **Remotion** styled-overlay renderer (style layer) driven by a props contract; layered caption-style config | ✅ built (pure logic tested; the Remotion render is the local acceptance gate) |
| 4–6 | Safe-zone QC renderer; FCPXML handoff; source-card overlays | designed; not yet built |

Captions are **two layers, kept separate**: the pipeline owns *timing* (transcript
→ cues, glossary-corrected) and *placement* (a safe-zone-derived box); **Remotion**
owns *look* (`remotion/`). The props JSON is the seam between them.

## How it runs

The pure logic (project contract, safe-zone geometry, rough-cut proposal, caption
chunking/placement/export) is plain Python and runs anywhere — it is what the test
suite covers, with no native toolchain required.

Three steps need a local machine with the native toolchain installed, so they run
on the editing/render machine rather than in CI:

- **Transcription** (`mlx-whisper`) — Apple-Silicon Mac; local, word-level
  timestamps, nothing leaves the machine. A precomputed Whisper-JSON transcript
  can be supplied instead, which removes this requirement.
- **Reframe / rough-cut render** — needs an `ffmpeg` binary.
- **Caption overlay render** — needs Node + the bundled `remotion/` project.

## Install

```bash
pip install -e .            # core: numpy, Pillow, PyYAML, jsonschema
pip install -e '.[reframe]' # native extras: mediapipe, opencv-python
pip install -e '.[dev]'     # pytest
```

The core install and the test suite need **no** native MediaPipe/OpenCV build; the
reframe probe's real run does.

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
```

Captions need real words — supply mlx-whisper (the `[roughcut]` extra) or a cached
Whisper-JSON transcript via `--transcript` (e.g. the one the rough-cut phase wrote
to `work/`). The Remotion overlay needs the bundled project installed once:
`cd remotion && npm install`.

## Test

```bash
pytest                                 # with the dev extra
python -m unittest discover -s tests   # stdlib-only fallback (no pytest needed)
```

## Layout

```
schema/project.schema.json        project.yml contract (rough_cut + captions blocks)
config/safezone/                  template PNG + generated spec
config/glossary/                  layered caption vocabulary (global + per-identity)
config/caption-styles/            layered caption style/chunk config (global + per-identity)
remotion/                         Remotion caption renderer (style layer; Node)
src/video_pipeline/
  safezone/                       template PNG -> SafeZoneSpec (polygon + bands)
  reframe/                        tracker (seam) -> crop plan -> ffmpeg command
  roughcut/                       transcript seam -> propose -> decision file -> render
  captions/                       chunk (timing) -> caption file -> placement/export -> Remotion (style)
  manifest.py  project.py         project.yml load/validate + scaffolding
  glossary.py  cli.py
tests/                            unittest/pytest suite (runs without native deps)
docs/phase1.md  phase2.md  phase3.md   what each phase delivers + acceptance steps
```

Projects are **data**, not code: each lives in its own folder (with `source/` as
untouched input) outside this repo, and is never committed here.
