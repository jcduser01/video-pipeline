# Remotion caption renderer — the style layer (INI-085 Phase 3)

This is the **style layer** of the captions phase. The Python pipeline owns
*timing* (word-level transcript → 2–4-word cues, glossary-corrected) and *placement*
(a safe-zone-derived caption box); this Remotion project owns *look* (font, colour,
stroke, emphasis, casing, animation) and renders a transparent caption overlay.

It is Node/React and runs on the **daily driver (Ono-Sendai)** — not in the JasonOS
sandbox, exactly like mlx-whisper. The Python side is fully unit-tested; the props
contract it emits is the seam.

## One-time setup (daily driver)

```sh
cd remotion
npm install
```

## Render

The pipeline writes a props JSON (`video-pipeline captions … --props work/caption-props.json`).
Render the overlay from it:

```sh
npx remotion render src/index.ts Captions out/captions.mov \
    --props=work/caption-props.json --codec=prores --prores-profile=4444
```

`video-pipeline captions-render <caption-file.yml> -o out/captions.mov` does this for
you: it rebuilds props from the (possibly hand-edited) caption file + the project's
style/safe-zone config, then invokes the command above.

Output is **ProRes 4444 with alpha** — composite it over the reframed video in
Premiere/Resolve (or onto a labelled FCPXML track in Phase 5). Captions stay an
independent, restyleable layer.

## Props contract

`src/types.ts` mirrors `video_pipeline.captions.export.track_to_remotion_props`
(`schemaVersion: 1`): `dimensions`, `fps`, `safeBox` (px, inside the safe polygon),
the resolved `style`, and `cues` (frame-accurate `from` / `durationInFrames` +
`emphasis` word indices). `npm run studio` opens the preview with sample props.

## Fonts

`font_family` must resolve on the render machine. Install the family (Archivo /
IBM Plex Mono / Inter per identity) system-wide, or add `@remotion/google-fonts`
loading in `Captions.tsx` if you prefer bundled web fonts.
