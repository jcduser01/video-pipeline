# Documentation index

A routing map from each document to the part of the project it is the source of
truth for. Before completing a change, find the row(s) whose subject your change
touched and update those documents **in the same change** — stale docs are defects.
When you add or remove a document, or add a subsystem, update this index too.

## Top level

| Document | Source of truth for |
|---|---|
| [README.md](README.md) | What the project is; install/extras; the full CLI usage walkthrough; the `project.yml` configuration surface; repo layout. The entry point — any new command, flag, or config key is reflected here. |

## Pipeline stages and subsystems (`docs/`)

| Document | Source of truth for |
|---|---|
| [docs/phase1.md](docs/phase1.md) | Phase 1 record: project contract + scaffold, the safe-zone spec generator (template PNG → notch-aware polygon/bands), and the reframe probe core. (Reframe since generalized — see `docs/reframe.md`.) |
| [docs/phase2.md](docs/phase2.md) | Phase 2: rough cut + the editable decision file — transcription seam (mlx-whisper / silence), the proposer, decision-file round-trip, FFmpeg trim/concat render, and their `rough_cut` config knobs. |
| [docs/phase3.md](docs/phase3.md) | Phase 3 core: caption timing + placement — glossary correction, chunking, safe-zone-aware box, SRT export, and the Remotion props seam. (Per-run styling controls were added later — see README "Configuration" + `config/caption-styles/README.md`.) |
| [docs/phase4.md](docs/phase4.md) | Phase 4: safe-zone QC — the layout validator (danger intrusion, caption-over-face, face-in-danger), the QC report, danger-zone preview, and clean render; the `qc` config block. |
| [docs/phase5.md](docs/phase5.md) | Phase 5: editor handoff — base cut over the reframed clip + caption overlay, cue cut-time remap, and the FCP7 XML (Premiere) / FCPXML serializers. |
| [docs/reframe.md](docs/reframe.md) | The reframe subsystem: target format (aspect presets, resolution tiers, Auto), framing intents, the propose/render split + editable `reframe.def`, scale/pan/subject-y, composition lock, and the project-level `target`. |
| [docs/ini-089-overlay.md](docs/ini-089-overlay.md) | The overlay subsystem: timed/placed overlay primitive, the editable `overlay.def`, the `overlay.occupancy` descriptor, the transcript→window proposer, and the source-card producer. |
| [docs/gui-schema.md](docs/gui-schema.md) | The pipeline side of the control-tower GUI contract: the `schema` subcommand (emit/`--check`), the `schema/` module layout, and how to add a step/flag/export target. (GUI repo: `video-pipeline-gui`.) |

## Configuration and renderer (layered config + Node)

| Document | Source of truth for |
|---|---|
| [config/glossary/README.md](config/glossary/README.md) | The layered caption vocabulary (global + per-identity glossary files). |
| [config/caption-styles/README.md](config/caption-styles/README.md) | The layered caption style + chunking config (look, casing, the words-per-cue range, the font allowlist and style caps). |
| [config/safezone/README.md](config/safezone/README.md) | The safe-zone template PNG → generated spec workflow. |
| [remotion/README.md](remotion/README.md) | The Remotion caption renderer (the style layer; the Node project the pipeline drives). |
