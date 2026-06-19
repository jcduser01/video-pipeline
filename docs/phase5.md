# Phase 5 — Editor handoff (Premiere XML / FCPXML)

The pipeline's last automated step. It assembles the accepted layers — the
**base cut** (the rough-cut decision file's KEEP segments) over the **reframed
vertical clip**, plus the **styled caption overlay** — into a single editor
project. The editor's last mile — pacing, transitions, music placement, final
mix — stays with the editor (shaping brief §5).

## Two output formats — Premiere by default

**Adobe Premiere Pro does not import FCPXML.** It imports the older **Final Cut
Pro 7 XML** interchange (XMEML), which DaVinci Resolve and Final Cut also read.
So the handoff emits two formats:

| `--format` | Document | Opens natively in |
|---|---|---|
| `premiere` (default) | FCP7 XML / XMEML (`<!DOCTYPE xmeml>`) | **Premiere Pro**, Resolve, Final Cut |
| `fcpxml` | FCPXML 1.10 (`<fcpxml version="1.10">`) | Resolve, Final Cut |

Default to `premiere` so a reel opens straight in Premiere with no FCPXML→XML
round-trip through Resolve. Both formats are built from the **same** base cut and
the same source→cut caption remap (`timeline.py` is format-agnostic); they differ
only in serialization:

- **XMEML** — times are integer **frames**; the base cut's audio is explicit
  (a video clipitem on V1 plus stereo audio clipitems on A1/A2, tied with
  `<link>` blocks); the caption overlay rides **V2** with
  `<alphatype>straight</alphatype>` so Premiere keys its transparency;
  `pathurl` is `file://localhost/…`.
- **FCPXML 1.10** — times are rational seconds (`frameDuration = 1/fps`); the
  base cut is `asset-clip`s on the `<spine>`; the caption overlay is one
  connected clip (`lane="1"`, role `Captions`).

As with the rest of the pipeline, the assembly is **pure and unit-tested**; the
only machine-specific requirement is that the referenced media (the reframed
clip and the rendered overlay) exists on the editing machine so the project
relinks.

## Flow

```
decision file (KEEP segments) ─▶ base cut: asset-clips on the spine
                                  (labeled "Base Cut", over the reframed clip)
reframed clip ──────────────────▶ the base-cut clips reference it (reframe baked)
caption file ─▶ remap to cut-time ─▶ cut-time caption file ─▶ (captions-render) ─▶ overlay
                                  ▲                                                  │
                                  └── the cut drops segments, so cues must move ─────┘
overlay (.mov) ─────────────────▶ Captions track: one connected clip (lane 1)
                              =  out/<project>.fcpxml
```

## Two design decisions

**The reframe is baked, not a transform.** The base-cut clips reference the
*reframed* vertical clip (`work/<clip>-9x16.mp4`), which the CEO already accepted
on real footage in Phase 1. Re-expressing the reframe as an FCPXML transform
would be lossy and editor-dependent; referencing the rendered clip is frame-exact
and imports identically everywhere. (A future "editable reframe" mode could emit
a transform instead; not built.)

**Captions are remapped to cut time.** Caption cues are timed against the
*source*. The base cut drops segments, so the timeline is **compressed** — a cue
at source `4.0s` may belong at cut `3.1s`. `fcpxml.timeline.remap_track` rebuilds
the caption track in cut time: cues that fall entirely in dropped regions are
omitted, cues that straddle a cut boundary are clipped, and per-word (karaoke)
timings move with them. The runner writes this as `out/<project>.captions.cut.yml`;
render *that* file to the overlay so the captions line up with the compressed cut.
When `trim_filler: false` (a single whole-clip KEEP), the remap is the identity
and captions pass through unchanged.

## Usage

```bash
# Premiere-compatible handoff (default; writes out/reel.xml + the cut-time caption file)
video-pipeline handoff review/decision.yml -o out/reel.xml \
    --reframed work/clip-9x16.mp4 \
    --captions review/captions.yml \
    --profile reels-9x16 --fps 30

# Render the aligned overlay from the cut-time caption file, then re-open the
# project (it already references out/reel.captions.mov):
video-pipeline captions-render out/reel.captions.cut.yml \
    -o out/reel.captions.mov --safezone config/safezone/reels-9x16.safezone.json

# FCPXML instead (Resolve / Final Cut) — either form:
video-pipeline handoff review/decision.yml -o out/reel.fcpxml --format fcpxml --reframed work/clip-9x16.mp4
video-pipeline fcpxml  review/decision.yml -o out/reel.fcpxml --reframed work/clip-9x16.mp4   # back-compat alias
```

Without `--captions`, the project is base-cut only (still opens with the
reframed, trimmed timeline ready to edit). `--overlay` overrides the overlay path
the project references; `--project-name` sets the sequence/project name (`--event`
sets the FCPXML event, fcpxml only). Name the Premiere output `.xml` and the
FCPXML output `.fcpxml`.

## What's pure vs. machine-specific

| Piece | Where | Tested in CI |
|---|---|---|
| Base-cut timeline + cumulative offsets | `fcpxml/timeline.py` | ✅ |
| Source→cut time remap (drop / clip / karaoke) | `fcpxml/timeline.py` | ✅ |
| FCP7 XML / XMEML document (frames, V1/V2, A1/A2 links, alpha) | `fcpxml/xmeml.py` | ✅ |
| FCPXML 1.10 document (rationals, assets, spine, lanes) | `fcpxml/document.py` | ✅ |
| Format dispatch + path resolution + file writes | `fcpxml/runner.py` | reads/writes only |
| Opening the project + relinking media; overlay render | Premiere / Resolve / FCP | local acceptance |

The remaining unverified surface is the same as the other phases' daily-driver
seams: the actual import into Premiere/Resolve and the overlay render. The
document structure (both formats), timeline math, and remap are fully covered by
the suite.
