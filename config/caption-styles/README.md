# Caption styles — the style layer (INI-085 Phase 3)

Captions are **two layers, kept separate** (shaping brief §3.3):

- **Timing layer** — the transcriber gives words + timestamps; the chunker
  (`video_pipeline.captions.chunk`) groups them into 2–4-word cues. Engine-
  agnostic; carries no styling.
- **Style layer** — *the look*: font, size, colour, stroke, position inside the
  safe zone, emphasis treatment, casing, chunk sizing. Owned here and rendered
  by **Remotion** (the `remotion/` project consumes the props JSON the pipeline
  emits).

Styles are **layered, repo-resident config**, exactly like the glossary:

    global.yml                 # shared defaults across every identity
    identities/<identity>.yml  # per-identity overrides (identity wins)

A project's `project.yml` may override individual keys under a `captions:` block
(project wins over identity wins over global). Unset keys fall back through the
layers to the built-in `CaptionStyle` defaults.

## Keys

| Key | Meaning |
| --- | --- |
| `font_family` | Caption typeface (must be installed / bundled for Remotion). |
| `font_size` | Cap height in px at the profile's native height (e.g. 1920). |
| `font_weight` | Numeric weight (400–900). |
| `fill_color` | Text fill (hex). |
| `stroke_color` / `stroke_width` | Outline colour / px (legibility over video). |
| `emphasis_color` | Fill for emphasis words (glossary terms / ALL-CAPS). |
| `uppercase` | Force-uppercase the rendered text (timing/text data unchanged). |
| `position` | Vertical anchor inside the safe zone: `lower-third`, `center`, `upper-third`. |
| `max_words` / `min_words` | Words-per-cue **range** — the primary chunking control (see below). |
| `target_words` | Words-per-cue the chunker aims for (balance target). `0` = auto (midpoint of the range). |
| `max_chars` | Hard character cap per cue. |
| `max_gap_s` | Inter-word gap (s) that forces a cue break (a natural pause). |
| `break_words` | Optional override of the built-in function-word list used for phrase-aware breaking. |
| `emphasize_glossary_terms` | Flag glossary canonical terms as emphasis words. |

### The range is the mode

`min_words` / `max_words` is one parameterizable control, not two discrete modes:

- `1` / `1` → **single-word** captions (one word per cue, word-by-word).
- `2` / `4` → **phrase-aware groups** (the default).
- `1` / `2`, `2` / `3`, … → anything in between.

The chunker breaks **phrase-aware and balanced** within the range: it favours
even cue widths, avoids one-word widows (when `min_words > 1`), and prefers to
break *before* function words ("the", "and", "I") rather than stranding them. At
`1`/`1` every word is its own cue and those refinements simply have nothing to act
on — so the same control expresses every density without a `mode` switch.

The `max_words` / `min_words` / `target_words` / `max_chars` / `max_gap_s` /
`break_words` keys feed the **chunker** (timing-layer grouping); the rest feed the
**Remotion style layer**. Keeping them in one file means "how captions read and
look" is one decision.
