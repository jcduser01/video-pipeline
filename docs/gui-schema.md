# Control-tower schema (`video-pipeline schema`)

The pipeline is the **single source of truth** for what a desktop control-tower GUI
shows: the steps, the schedulable tasks, the artifacts they pass between them, the
parameters on each, and the editor export targets. The `schema` subcommand emits
that description as one document; the GUI reads it at launch and builds its forms,
its previewer's source list, and its export menu from it — no GUI recompile when the
pipeline grows.

This is the pipeline side of the contract. The GUI owns what *conformant* means (a
versioned meta-schema grammar lives in the GUI repo); the pipeline owns *what the
steps are* and must keep emitting a conformant document.

## Emit

```bash
# Emit the schema as YAML (the authored form the GUI reads) or JSON
video-pipeline schema --format yaml
video-pipeline schema --format json -o build/schema.json

# Validate the schema is well-formed without emitting it
video-pipeline schema --check
```

`--format yaml` is the canonical form; `--format json` is the same document
normalized for tooling. `-o <file>` writes to a path instead of stdout. `--check`
validates internal consistency (every task references a real step, every consumed
channel is produced somewhere, every previewable artifact has a stacking order, and
so on) and exits non-zero on any problem — emitting nothing. Use it as a fast
sanity gate after editing the definition.

## Module layout

```
src/video_pipeline/schema/
  model.py        grammar dataclasses (Step, Task, Artifact, Param, ExportTarget, …)
  definition.py   the instance — the actual steps/tasks/artifacts this pipeline exposes
  emit.py         serialization (model -> YAML / JSON)
  assemble.py     the reference argv assembler (schema + values -> a real command line)
```

- **`model.py`** defines the shape of each node — the dataclasses that mirror the
  GUI's meta-schema grammar. Change this only when the *grammar* changes (a new
  control hint, a new artifact kind), which is a cross-cutting change the GUI must
  also accommodate.
- **`definition.py`** is the content: the concrete list of steps, tasks, artifacts,
  params, and export targets this pipeline offers. This is the file you edit to add
  or change what the GUI shows.
- **`emit.py`** serializes the definition to YAML or JSON.
- **`assemble.py`** is the **reference argv assembler** — given a task (or export
  target) and a set of parameter values, it produces the exact command line the
  pipeline would run. The GUI assembles `argv` on its own (in Rust), but this is the
  Python reference for the same mapping, and what proves the schema's flag/arity
  metadata resolves to a command that actually runs.

## Adding a step, flag, or export target

The point of the schema is that the GUI never hardcodes any of this, so growth is a
pipeline-only edit:

1. **Edit `definition.py`** to add the task / param / export target, mirroring a
   **real** CLI subcommand and its real flags. The `subcommand`, each param's
   `flag` and `arity`, and the `io` bindings must match what the CLI actually
   accepts — this is what makes the assembled `argv` runnable.
2. **Run `video-pipeline schema --check`** to confirm the document still validates
   (references resolve, no duplicate keys, previewable artifacts have a `z_order`,
   export subcommands name real commands).
3. **Relaunch the GUI.** It re-reads the schema on launch and the new control,
   preview source, or export target appears — **zero GUI recompile.**

> **Grounding note — the schema mirrors the real CLI.** The schema is not a parallel
> universe; it describes the commands that exist, so the `argv` it resolves actually
> runs. When you add to `definition.py`, you are documenting a real subcommand and
> real flags, not inventing them. `assemble.py` and the GUI's contract test exist to
> keep that honest: the assembler must produce a command the CLI accepts, and the
> contract test fails if the emitted document drifts from what the GUI can parse.

## The schema grows by content, not grammar

Two large subsystems landed since the SADD example was first sketched, and both
slotted into the schema as new **content** in `definition.py` — no grammar change,
no GUI recompile. They are the working proof that the single-source-of-truth model
holds as the pipeline grows.

**Overlay subsystem (INI-089) — shipped.** The dedicated overlay step the
architecture document sketched now exists. `definition.py` emits `overlay.define`
(author the editable `overlay.def`), `overlay.card` (capture a source card), and
`overlay.render` (composite the timed/placed overlays and emit the
`overlay.occupancy` descriptor). The occupancy descriptor is the thin cross-branch
edge: caption placement and safe-zone QC read *where* an overlay sits (bounding
regions / coverage), never its pixels. Captions are no longer the only overlay
layer — see [`ini-089-overlay.md`](ini-089-overlay.md).

**Reframe split (INI-090/091) — shipped.** The reframe step is no longer a single
portrait-only task. `definition.py` emits `reframe.propose` (track the subject →
editable `reframe.def`) and `reframe.render` (replay the def → reframed clip),
mirroring the caption define/render pattern, and surfaces the target-format,
framing-intent, and composition-lock controls. See [`reframe.md`](reframe.md).

Both demonstrate the intended growth path from
[Adding a step, flag, or export target](#adding-a-step-flag-or-export-target):
edit `definition.py` to mirror the real CLI, run `schema --check`, relaunch — the
new tasks, artifacts, and controls appear in the GUI on next launch.
