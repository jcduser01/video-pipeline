# video-pipeline

AI **pre-editing** pipeline for vertical short-form video (Instagram Reels first;
other targets are profiles, not forks). The machine does the mechanical labor —
cut to length, reframe to vertical, caption, place overlays inside the safe zone
— and hands off an **editable project** (FCPXML → Premiere Pro). Pacing, taste,
transitions, and music placement stay with the operator.

> The edit-decision artifacts are the product; renders are regenerable views of
> them. The code is governed (JasonOS INI-085); execution is CEO-operated on the
> daily driver (Ono-Sendai), outside the director perimeter.

Full design rationale: governance repo `architecture/video-pipeline/shaping-brief.md`.

## Status

| Phase | What | State |
|---|---|---|
| 1 — Probe | Repo scaffold + project contract; safe-zone spec generator (template PNG → notch-aware polygon/mask); reframe probe (subject tracking → FFmpeg vertical crop) | ✅ accepted on real footage |
| 2 — Rough cut | Transcription seam (mlx-whisper) + silence fallback; pure proposer (filler / false-start / dead-air, honors `trim_filler`); **editable decision file** (the product); FFmpeg trim/concat render | ✅ accepted on real footage |
| 3 — Captions | Glossary-corrected 2–4-word chunker (timing layer); **editable caption file** (the product); safe-zone-aware placement; SRT export; **Remotion** styled-overlay renderer (style layer) driven by a props contract; layered caption-style config | ✅ built (pure logic tested; Remotion render is the daily-driver acceptance gate) |
| 4–6 | Safe-zone QC renderer; FCPXML handoff; source-card overlays | scoped in brief §5; not yet built |

Captions are **two layers kept separate** (brief §3.3): the pipeline owns *timing*
(transcript → cues, glossary-corrected) and *placement* (a safe-zone-derived box);
**Remotion** owns *look* (`remotion/`). Like mlx-whisper, the Remotion render runs
on the daily driver, not in the sandbox; the props JSON is the seam.

## Install

```bash
pip install -e .            # core: numpy, Pillow, PyYAML, jsonschema
pip install -e '.[reframe]' # daily-driver extras: mediapipe, opencv-python
pip install -e '.[dev]'     # pytest
```

The core install + the test suite need **no** native MediaPipe/OpenCV build. The
reframe probe's real run does (daily driver only).

## Usage

```bash
# 1. Regenerate the safe-zone spec from a template PNG (update-resilient)
video-pipeline safezone-gen config/safezone/instagram-safe-zone-reels-9x16.png \
    --profile reels-9x16 -o config/safezone/reels-9x16.safezone.json

# 2. Scaffold a new project (creates source/work/review/out/render + project.yml)
video-pipeline project-init "2026-06-03 Reel Project - I used to make fun of ravers" \
    --identity dyson-hope --profile reels-9x16

# 3. Reframe a landscape clip to vertical (daily driver; --dry-run prints the cmd)
video-pipeline reframe source/clip.mp4 -o out/clip-9x16.mp4 --profile reels-9x16

# 4. Rough cut -> editable decision file (daily driver: mlx-whisper, or pass --transcript)
video-pipeline roughcut source/clip.mp4 -o review/decision.yml --render work/rough.mp4

# 5. Captions -> editable caption file + SRT + Remotion props (mlx-whisper or --transcript)
video-pipeline captions source/clip.mp4 -o review/captions.yml --identity dyson-hope \
    --srt out/captions.srt --props work/caption-props.json \
    --safezone config/safezone/reels-9x16.safezone.json

# 6. Render the styled caption overlay from the (editable) caption file (daily driver: Remotion)
video-pipeline captions-render review/captions.yml -o out/captions.mov \
    --identity dyson-hope --safezone config/safezone/reels-9x16.safezone.json
```

Captions need real words — supply mlx-whisper (the `[roughcut]` extra) or a cached
Whisper-JSON transcript via `--transcript` (e.g. the one the rough-cut phase wrote
to `work/`). The Remotion overlay needs the bundled project installed once:
`cd remotion && npm install`.

## Test

```bash
pytest                                 # on the daily driver
python -m unittest discover -s tests   # stdlib-only fallback (no pytest needed)
```

## Layout

```
schema/project.schema.json        project.yml contract (rough_cut + captions blocks)
config/safezone/                  template PNG + generated spec
config/glossary/                  layered caption vocabulary (global + per-identity)
config/caption-styles/            layered caption style/chunk config (global + per-identity)
remotion/                         Remotion caption renderer (style layer; daily driver/Node)
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

Projects are **data**, not code: they live under `~/Video/Projects/` on the daily
driver and archive to Drive. They are never committed to this repo.
