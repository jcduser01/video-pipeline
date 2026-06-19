# Phase 3 — Captions

Captions are **two layers, kept separate** (shaping brief §3.3): the pipeline owns
**timing** (a word-level transcript becomes short on-screen cues) and **placement**
(a caption box inside the safe zone); **Remotion** owns **look** (font, colour,
stroke, emphasis, casing, animation). The seam between them is a props JSON.

As in Phase 2, the **editable artifact is the product**: the caption file
(`review/captions.yml`) is a human-editable YAML round-trip; the rendered overlay
is a regenerable view of it. Fix `text:` (or nudge `start`/`end`, flip `keep:`) and
re-render.

## Flow

```
media ─▶ transcribe ─▶ chunk (glossary) ─▶ caption file ─┬▶ SRT (portable)
        (mlx-whisper /    timing layer        the product  └▶ Remotion props ─▶ overlay.mov
         cached JSON)                                          style layer (daily driver)
```

1. **Transcription** reuses the Phase-2 `Transcriber` seam — mlx-whisper on the
   daily driver, or a cached Whisper-JSON via `--transcript` (the same `work/`
   transcript the rough cut produced; captions never re-transcribe). The
   silence-only fallback has no words and is **not** valid for captions.
2. **Glossary correction first.** Each word passes through the merged glossary
   (`config/glossary/` global + identity) *before* chunking, so proper nouns land
   correctly on the first pass — the DoD item. Multi-word mishears
   (`"sigil zero" → SIGIL.ZERO`) collapse to one token spanning the originals.
3. **Chunking** (`captions/chunk.py`, pure) groups words into 2–4-word cues,
   breaking on sentence punctuation, a `max_gap_s` pause, `max_words`, or
   `max_chars`. Cue timing is the first→last word span. Glossary canonical terms
   are flagged as **emphasis** words.
4. **Placement** (`captions/placement.py`, pure) derives a caption box at the
   style's vertical anchor that is **guaranteed inside the safe polygon** — a
   lower-third box is auto-narrowed to clear the action-button notch.
5. **Export** (`captions/export.py`, pure) writes a portable **SRT** (imports into
   any editor / YouTube) and the **Remotion props** JSON (`schemaVersion: 1`):
   resolved style, the safe box, dimensions/fps, and each kept cue with
   frame-accurate `from`/`durationInFrames` + emphasis indices.
6. **Remotion** (`remotion/`, Node/React, daily driver) renders the styled overlay
   from those props — **ProRes 4444 with alpha**, composited over the reframed
   video in the editor (or onto a labelled FCPXML track in Phase 5).

## Style is layered config

`config/caption-styles/` mirrors the glossary: `global.yml` + `identities/<id>.yml`
(identity wins), with optional `project.yml` `captions:` overrides on top (project
wins). One file holds both *how captions read* (chunk knobs) and *how they look*
(style), so it is a single decision. Unknown keys are ignored (forward-compatible).

## CLI

```bash
# captions (daily driver: mlx-whisper, or pass a cached transcript)
video-pipeline captions source/clip.mp4 -o review/captions.yml \
    --identity dyson-hope --profile reels-9x16 \
    --transcript work/whisper.json \
    --srt out/captions.srt \
    --props work/caption-props.json \
    --safezone config/safezone/reels-9x16.safezone.json

# re-render the styled overlay from a (possibly hand-edited) caption file
video-pipeline captions-render review/captions.yml -o out/captions.mov \
    --identity dyson-hope --safezone config/safezone/reels-9x16.safezone.json
```

One-time on the daily driver: `cd remotion && npm install`.

## What's tested vs. daily-driver

Fully unit-tested in the sandbox (no native deps): glossary correction, chunking,
the caption-file round trip, SRT export, safe-zone-aware placement (proven against
the real reels spec — every box satisfies `rect_clear`), layered style config, and
the Remotion props contract + render-command argv.

Daily-driver / Node only (the seams, by design): live mlx-whisper transcription and
the actual Remotion render. These are the same class as the Phase-2 mlx-whisper seam.

## Remaining Phase-3 DoD (CEO / Ono-Sendai)

- `uv pip install -e '.[roughcut]'`; `cd remotion && npm install`.
- `video-pipeline captions "<real clip>" -o review/captions.yml --identity <id> \
   --srt out/captions.srt --props work/caption-props.json \
   --safezone config/safezone/reels-9x16.safezone.json` (mlx-whisper online once for
  the model pull, then offline).
- `video-pipeline captions-render review/captions.yml -o out/captions.mov \
   --identity <id> --safezone config/safezone/reels-9x16.safezone.json`.
- Confirm captions land with correct proper-noun spellings from the glossary on the
  first pass across ≥2 identities (the initiative DoD caption bullet), and that the
  overlay sits inside the safe zone. Accept, then merge the fork→canonical PR.
