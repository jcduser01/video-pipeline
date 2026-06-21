"""The JasonOS video-pipeline control-tower schema instance.

This is the single source of truth the GUI consumes (SADD §3). It is authored
to mirror the *real* CLI surface in ``video_pipeline.cli`` so the resolved argv
the GUI shows actually runs. Adding a step/flag/export target here (and the
matching CLI code) is all it takes for the GUI to surface it on next launch —
no GUI recompile (tenet 4).

Grounding note (divergence from the SADD worked example): the SADD §4.2 example
shows a dedicated ``overlay.*`` branch. The current CLI has no ``overlay``
subcommand — captions-render *is* the overlay-equivalent (it renders the styled
caption .mov that the NLE stacks as an overlay track). So this schema models the
real graph: base → caption.def → caption-layer, with safezone as a descriptor and
qc as an advisory consumer. The occupancy-descriptor mechanism (§3.3) is preserved
in the grammar; when a true overlay step lands in Python it slots in as a new
producer of an ``overlay`` channel + an ``overlay.occupancy`` descriptor that
``caption.render`` consumes — and appears in the GUI automatically.
"""

from __future__ import annotations

from pathlib import Path

from .model import (
    Artifact,
    Compose,
    ComposePart,
    Engine,
    ExportTarget,
    IOBinding,
    Param,
    PathSpec,
    Schema,
    Step,
    Task,
    UI,
    SCHEMA_VERSION,
)

# Valid project-name tokens (the content-type word in the folder convention
# "YYYY-MM-DD <Token> Project - <Hook>"). Curated set surfaced as a dropdown.
_PROJECT_TOKENS = ["Reel", "Story", "Post"]

# Profiles the CLI understands (kept in sync with cli._PROFILE_DIMS).
_PROFILES = [
    "reels-9x16",
    "story-9x16",
    "feed-portrait-4x5",
    "feed-square-1x1",
    "feed-landscape-16x9",
]

# Known creator identities are config layers on disk (a caption-style layer and/or
# a glossary layer). Discover them so the GUI's Identity control is a dropdown of
# real choices instead of free text — kept zero-hardcoded: add an identity YAML and
# it appears on next emit, no code change. config/ sits at the repo root (the CLI's
# default --config-root); definition.py is at src/video_pipeline/schema/.
_CONFIG_ROOT = Path(__file__).resolve().parents[3] / "config"


def _known_identities() -> list[str]:
    ids: set[str] = set()
    for sub in ("caption-styles/identities", "glossary/identities"):
        d = _CONFIG_ROOT / sub
        if d.is_dir():
            ids.update(p.stem for p in d.glob("*.yml"))
    return sorted(ids)


_IDENTITIES = _known_identities()


def _identity_param(
    *,
    required: bool,
    ui: UI,
    hint: str,
    help: str = "",
    example: str | None = None,
) -> Param:
    """The Identity control. When identities are discoverable it's an ``enum``
    (→ dropdown) of the known ones; otherwise a free-text ``string`` so emit never
    breaks. Required instances default to the first known identity so the visible
    selection always matches the resolved argv."""
    if _IDENTITIES:
        return Param(
            "identity", "enum", flag="--identity", required=required,
            options=_IDENTITIES,
            default=_IDENTITIES[0] if required else None,
            hint=hint, help=help, example=example, ui=ui,
        )
    return Param(
        "identity", "string", flag="--identity", required=required,
        hint=hint, help=help, example=example, ui=ui,
    )


def _profile_param(*, required: bool, default: str | None = "reels-9x16") -> Param:
    return Param(
        key="profile",
        type="enum",
        flag="--profile",
        options=_PROFILES,
        default=default,
        required=required,
        hint="Output aspect/dimensions preset.",
        help="The target frame. reels-9x16 (1080x1920) is the default for vertical "
        "Reels; the feed-* presets cover 4:5, 1:1, and 16:9. Drives crop math and "
        "safe-zone derivation.",
        example="--profile reels-9x16",
        ui=UI(label="Profile", control="dropdown", group="Output"),
    )


def build_schema() -> Schema:
    """Construct the conformant schema instance."""

    engine = Engine(
        name="video-pipeline",
        version="0.1.0",
        schema_version=SCHEMA_VERSION,
        cli_entrypoint="video-pipeline",
    )

    # ---- Steps (UI groupings) -------------------------------------------------
    steps = [
        Step("project", "Project", order=10, optional=False,
             hint="Scaffold the project folder + project.yml.",
             help="Creates the source/work/review/out/render layout and the "
                  "project.yml that the later steps read defaults from. Always runs first."),
        Step("safezone", "Safe Zone", order=20,
             hint="Derive the safe-zone spec from a template PNG.",
             help="Turns a design template (with the danger region marked) into the "
                  "machine-readable safe-zone polygon that caption placement and QC use."),
        Step("reframe", "Reframe", order=30,
             hint="Landscape -> portrait reframe.",
             help="Tracks the subject and crops the source to the target portrait frame. "
                  "Skippable when the source is already in-aspect."),
        Step("roughcut", "Rough Cut", order=40,
             hint="Propose cuts -> editable decision file.",
             help="Transcribes the clip and proposes a tightened cut (drops filler, "
                  "false starts, dead air) as an editable decision file you can hand-tune "
                  "before rendering."),
        Step("caption", "Captions", order=50,
             hint="Transcribe + style burned-in captions.",
             help="Builds glossary-corrected word-timed cues, lets you edit the caption "
                  "file, and renders the styled caption overlay layer."),
        Step("qc", "Safe-Zone QC", order=60,
             hint="Check captions/faces against the danger zone.",
             help="Advisory check: flags protected elements intruding on the danger "
                  "polygon and captions over the speaker's face. Advises, never blocks."),
        Step("composite", "Composite", order=70,
             hint="Flatten the layers into one preview render.",
             help="Stacks the base and the caption/overlay layers into a single "
                  "previewable video — the in-app preview of the assembled result and "
                  "the guide track the editor handoffs carry on top. A review/handoff "
                  "intermediate, not the final cut (that is yours, from your NLE)."),
    ]

    # ---- Tasks (graph nodes) --------------------------------------------------
    tasks: list[Task] = []

    # project-init: origin of the `base` channel. Its sole positional is the
    # project folder name (CLI `project-init "<name>" ...`); `base` is produced for
    # the dependency graph but is NOT a project-init argument (paths are derived
    # internally from root + name), so it carries no io binding here.
    tasks.append(Task(
        id="project.init", step="project", label="Initialize project",
        subcommand="project-init", optional=False,
        consumes=[], produces=["base", "project"],
        io=[],
        hint="Scaffold a new project folder.",
        help="Creates the project layout and project.yml. The folder name encodes "
             "date/token/hook; identity and profile seed the project defaults.",
        params=[
            Param("name", "string", arity="positional", order=0, required=True,
                  hint="Project folder name.",
                  help="Assembled to the convention \"YYYY-MM-DD <Token> Project - "
                       "<Hook>\" from the fields below, so it always matches.",
                  compose=Compose(
                      template="{date} {token} Project - {hook}",
                      parts=[
                          ComposePart("date", "Date", control="date", default="today"),
                          ComposePart("token", "Token", control="dropdown",
                                      options=_PROJECT_TOKENS, default="Reel",
                                      hint="Content-type token; part of the folder "
                                           "name and the render filename."),
                          ComposePart("hook", "Hook", control="field",
                                      placeholder="short description",
                                      hint="A short description — becomes the render-file slug."),
                      ],
                  ),
                  ui=UI(label="Project name", group="Setup")),
            Param("source", "path", flag="--source",
                  hint="Source video to ingest.",
                  help="Pick the source clip. On run it is copied into the project — "
                       "archived in source/ and seeded as the base layer "
                       "(work/base.mp4) — so the reframe / rough-cut steps have their "
                       "input. Browse or drag a file onto the field.",
                  path=PathSpec(kind="file",
                                extensions=["mp4", "mov", "m4v", "webm", "mkv", "avi"]),
                  ui=UI(label="Source video", group="Setup")),
            _identity_param(required=True,
                  hint="Brand/identity id for styling defaults.",
                  help="Selects the caption style + glossary set for this creator "
                       "identity (e.g. a DH or SIGIL.ZERO identity).",
                  example="--identity dyson-hope",
                  ui=UI(label="Identity", group="Setup")),
            _profile_param(required=True),
            Param("root", "path", flag="--root",
                  hint="Parent folder for projects.",
                  help="Where the project folder is created. Defaults to ~/Video/Projects.",
                  path=PathSpec(kind="directory"),
                  ui=UI(label="Projects root", group="Setup")),
            Param("no_trim_filler", "bool", arity="switch", flag="--no-trim-filler",
                  default=False,
                  hint="Seed the project to preserve filler.",
                  help="Sets the project default so rough cut keeps audio continuity "
                       "(no filler/false-start trimming) unless overridden per run.",
                  ui=UI(label="Preserve filler by default", control="toggle", group="Setup")),
        ],
    ))

    # safezone-gen: descriptor producer.
    tasks.append(Task(
        id="safezone.gen", step="safezone", label="Generate safe-zone spec",
        subcommand="safezone-gen", optional=False,
        # Depends on `project` (no argv binding) so it runs AFTER project-init has
        # created the project dir it writes into — project-init also errors if the
        # dir already exists, so the two must not run in parallel.
        consumes=["project"], produces=["safezone.def"],
        io=[
            IOBinding(artifact="safezone.def", role="output", via="flag", flag="-o"),
        ],
        hint="Derive the safe-zone polygon from a template PNG.",
        help="Reads a design template where the danger region is marked (by alpha or "
             "by a key color) and emits the safe-zone spec JSON consumed downstream.",
        params=[
            Param("template", "path", arity="positional", required=True, order=0,
                  hint="Template PNG with the danger region marked.",
                  help="The design template image. The marked region becomes the danger "
                       "polygon (notch-aware).",
                  example="template.png",
                  path=PathSpec(kind="file", extensions=["png", "jpg", "jpeg", "webp"]),
                  ui=UI(label="Template PNG", group="Input")),
            _profile_param(required=False, default=None),
            Param("key", "enum", flag="--key", options=["auto", "alpha", "color"],
                  default="auto",
                  hint="How the danger region is marked.",
                  help="auto detects alpha vs a key color; force alpha or color if "
                       "detection guesses wrong.",
                  ui=UI(label="Region key", control="dropdown", group="Input")),
        ],
    ))

    # reframe: base -> base.
    tasks.append(Task(
        id="reframe", step="reframe", label="Reframe to portrait",
        subcommand="reframe", optional=True,
        consumes=["base"], produces=["base"],
        io=[
            IOBinding(artifact="base", role="input", via="positional", order=0),
            IOBinding(artifact="base", role="output", via="flag", flag="-o"),
        ],
        hint="Crop landscape source to the portrait frame.",
        help="Subject-tracking crop. Static holds one crop; dynamic follows the subject. "
             "Daily-driver path needs MediaPipe; --dry-run plans without rendering.",
        params=[
            _profile_param(required=False),
            Param("mode", "enum", flag="--mode", options=["static", "dynamic"],
                  default="static",
                  hint="Static hold vs subject-following crop.",
                  help="static picks one crop window for the whole clip (predictable); "
                       "dynamic pans to follow the subject (more motion).",
                  ui=UI(label="Crop mode", control="dropdown", group="Crop")),
            Param("tracker", "enum", flag="--tracker", options=["opencv", "mediapipe"],
                  default="opencv",
                  hint="Subject detector backend.",
                  help="opencv is dependency-light; mediapipe is the higher-quality "
                       "daily-driver face/pose tracker.",
                  ui=UI(label="Tracker", control="dropdown", group="Crop")),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Plan the crop without rendering.",
                  help="Computes and prints the crop plan but writes no video — fast way "
                       "to sanity-check tracking before a full render.",
                  ui=UI(label="Dry run", control="toggle", group="Crop")),
        ],
    ))

    # roughcut: base -> roughcut.def (the editable decision file).
    tasks.append(Task(
        id="roughcut", step="roughcut", label="Propose rough cut",
        subcommand="roughcut", optional=True,
        consumes=["base"], produces=["roughcut.def"],
        io=[
            IOBinding(artifact="base", role="input", via="positional", order=0),
            IOBinding(artifact="roughcut.def", role="output", via="flag", flag="-o"),
        ],
        hint="Transcribe + propose an editable cut decision file.",
        help="Produces the decision file (one scannable line per segment) you can "
             "hand-edit before rendering. Daily driver transcribes with mlx-whisper "
             "unless a --transcript is supplied.",
        params=[
            Param("no_trim_filler", "bool", arity="switch", flag="--no-trim-filler",
                  default=False, hint="Keep filler / preserve audio continuity.",
                  help="Disables filler/dead-air trimming — one whole-clip KEEP, no "
                       "speech edits. Use when continuity matters more than tightness.",
                  ui=UI(label="Preserve filler", control="toggle", group="Cut")),
            Param("no_false_starts", "bool", arity="switch", flag="--no-false-starts",
                  default=False, hint="Don't drop false starts.",
                  ui=UI(label="Keep false starts", control="toggle", group="Cut")),
            Param("silence_gap", "number", flag="--silence-gap", default=0.6,
                  min=0.0, max=3.0, step=0.05,
                  hint="Dead-air gap (s) that triggers a cut.",
                  help="Silence longer than this (seconds) is treated as dead air and "
                       "removed. Lower = tighter, more aggressive.",
                  ui=UI(label="Silence gap (s)", control="slider", group="Cut")),
            Param("pad_lead", "number", flag="--pad-lead", default=0.06,
                  min=0.0, max=0.5, step=0.01,
                  hint="Lead padding before each kept segment (s).",
                  help="Asymmetric cut padding. Small lead avoids clipping the first "
                       "word's onset.",
                  ui=UI(label="Lead pad (s)", control="slider", group="Padding")),
            Param("pad_tail", "number", flag="--pad-tail", default=0.15,
                  min=0.0, max=0.5, step=0.01,
                  hint="Tail padding after each kept segment (s).",
                  help="Larger tail than lead avoids clipping word-ends (a real defect "
                       "found in Phase-2 testing).",
                  ui=UI(label="Tail pad (s)", control="slider", group="Padding")),
            Param("online", "bool", arity="switch", flag="--online", default=False,
                  hint="Allow model download (default offline).",
                  help="mlx-whisper runs offline by default; --online permits a one-time "
                       "model fetch.",
                  ui=UI(label="Online model fetch", control="toggle", group="Transcription")),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Propose without rendering a cut.",
                  ui=UI(label="Dry run", control="toggle", group="Cut")),
        ],
    ))

    # roughcut-render: base + roughcut.def -> base (the cut clip).
    tasks.append(Task(
        id="roughcut.render", step="roughcut", label="Render rough cut",
        subcommand="roughcut-render", optional=True,
        consumes=["base", "roughcut.def"], produces=["base"],
        io=[
            IOBinding(artifact="roughcut.def", role="input", via="positional", order=0),
            IOBinding(artifact="base", role="input", via="flag", flag="-i"),
            IOBinding(artifact="base", role="output", via="flag", flag="-o"),
        ],
        hint="Re-render the cut from the (edited) decision file.",
        help="Applies the decision file's KEEP segments to the source via ffmpeg "
             "trim/concat. Re-runnable after you hand-edit the decision file.",
        params=[
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Show the trim/concat plan without rendering.",
                  ui=UI(label="Dry run", control="toggle", group="Render")),
        ],
    ))

    # captions: base -> caption.def (editable caption file).
    tasks.append(Task(
        id="caption.define", step="caption", label="Build caption file",
        subcommand="captions", optional=True,
        consumes=["base", "safezone.def"], produces=["caption.def"],
        io=[
            IOBinding(artifact="base", role="input", via="positional", order=0),
            IOBinding(artifact="caption.def", role="output", via="flag", flag="-o"),
            IOBinding(artifact="safezone.def", role="input", via="flag", flag="--safezone"),
        ],
        hint="Transcribe -> glossary-corrected word-timed cues.",
        help="Produces the editable caption file (phrase-aware, balanced cues). "
             "--safezone lets it emit the styled props; you can hand-edit the caption "
             "file before rendering.",
        params=[
            _identity_param(required=True,
                  hint="Identity for glossary + caption style.",
                  example="--identity dyson-hope",
                  ui=UI(label="Identity", group="Setup")),
            _profile_param(required=False),
            Param("karaoke", "bool", arity="switch", flag="--karaoke", default=False,
                  hint="Active-word highlight.",
                  help="Karaoke mode highlights the currently-spoken word within each cue.",
                  ui=UI(label="Karaoke highlight", control="toggle", group="Style")),
            Param("min_words", "number", flag="--min-words", min=1, max=8, step=1,
                  hint="Minimum words per cue.",
                  help="Lower bound of the words-per-cue range. 1/1 gives single-word "
                       "cues; widen for phrase cues.",
                  ui=UI(label="Min words / cue", control="slider", group="Timing")),
            Param("max_words", "number", flag="--max-words", min=1, max=8, step=1,
                  hint="Maximum words per cue.",
                  help="Upper bound of the words-per-cue range (e.g. 4 for 2/4 phrasing).",
                  ui=UI(label="Max words / cue", control="slider", group="Timing")),
            Param("online", "bool", arity="switch", flag="--online", default=False,
                  hint="Allow model download (default offline).",
                  ui=UI(label="Online model fetch", control="toggle", group="Transcription")),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Build cues without rendering.",
                  ui=UI(label="Dry run", control="toggle", group="Timing")),
        ],
    ))

    # captions-render: caption.def + safezone.def -> caption layer (.mov).
    tasks.append(Task(
        id="caption.render", step="caption", label="Render caption overlay",
        subcommand="captions-render", optional=True,
        consumes=["caption.def", "safezone.def"], produces=["caption"],
        io=[
            IOBinding(artifact="caption.def", role="input", via="positional", order=0),
            IOBinding(artifact="caption", role="output", via="flag", flag="-o"),
            IOBinding(artifact="safezone.def", role="input", via="flag", flag="--safezone"),
        ],
        hint="Render the styled caption overlay layer (alpha .mov).",
        help="Rebuilds props from the (possibly hand-edited) caption file + style/safe-"
             "zone config and renders the transparent caption overlay via Remotion "
             "(daily driver). This is the previewable caption layer the NLE stacks on top.",
        params=[
            _identity_param(required=False,
                  hint="Override the identity style.",
                  ui=UI(label="Identity", group="Style")),
            Param("karaoke", "bool", arity="switch", flag="--karaoke", default=False,
                  hint="Active-word highlight.",
                  ui=UI(label="Karaoke highlight", control="toggle", group="Style")),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Build props without rendering.",
                  ui=UI(label="Dry run", control="toggle", group="Style")),
        ],
    ))

    # qc: advisory check over base + caption.
    tasks.append(Task(
        id="safezone.qc", step="qc", label="Safe-zone QC",
        subcommand="qc", optional=True,
        consumes=["base", "caption", "safezone.def"], produces=["qc.report"],
        io=[
            IOBinding(artifact="base", role="input", via="positional", order=0),
            IOBinding(artifact="safezone.def", role="input", via="flag", flag="--safezone"),
            IOBinding(artifact="qc.report", role="output", via="flag", flag="--report"),
        ],
        hint="Advisory: flag danger-zone intrusions + captions over faces.",
        help="Checks protected elements against the danger polygon (notch-aware) and "
             "captions over the speaker's face. Advisory only — it warns at the export/"
             "composite control but never blocks (SADD §4.2). --strict makes it exit "
             "non-zero for CI gating.",
        params=[
            Param("strict", "bool", arity="switch", flag="--strict", default=False,
                  hint="Exit non-zero on any violation.",
                  help="Turns advisory warnings into a hard failure (exit 1) — for "
                       "scripted gating, not the interactive default.",
                  ui=UI(label="Strict (gate)", control="toggle", group="QC")),
            Param("no_face_check", "bool", arity="switch", flag="--no-face-check",
                  default=False, hint="Skip caption-over-face detection.",
                  ui=UI(label="Skip face check", control="toggle", group="QC")),
            Param("occlusion_frac", "number", flag="--occlusion-frac", min=0.0, max=1.0,
                  step=0.05, hint="Caption∩face fraction that flags occlusion.",
                  ui=UI(label="Occlusion frac", control="slider", group="Thresholds")),
        ],
    ))

    # composite: flatten base + caption (+ future overlays) -> a preview render.
    # Consumes the same layer set as QC; QC advises but does NOT gate it (SADD §4).
    # Multiple layers bind via repeated --layer flags, declared low->high z-order.
    tasks.append(Task(
        id="composite", step="composite", label="Composite layers",
        subcommand="composite", optional=True,
        consumes=["base", "caption"], produces=["composite"],
        io=[
            IOBinding(artifact="base", role="input", via="positional", order=0),
            IOBinding(artifact="caption", role="input", via="flag", flag="--layer"),
            IOBinding(artifact="composite", role="output", via="flag", flag="-o"),
        ],
        hint="Flatten base + overlay layers into review/composite.mp4.",
        help="Stacks the layers bottom-to-top by z-order (base, then the caption "
             "overlay + any future overlays) into one previewable .mp4 — the in-app "
             "preview of the assembled result and the guide track the editor handoffs "
             "carry on top. QC advises but never blocks it. A review/handoff "
             "intermediate, not your final cut.",
        params=[
            Param("crf", "number", flag="--crf", min=0, max=51, step=1, default=18,
                  hint="x264 quality (lower = better).",
                  help="Quality of the composite render; 18 is near-visually-lossless. "
                       "This is a preview/handoff render, not your final master.",
                  ui=UI(label="Quality (CRF)", control="slider", group="Render")),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Show the ffmpeg command without rendering.",
                  ui=UI(label="Dry run", control="toggle", group="Render")),
        ],
    ))

    # ---- Artifacts (channels + descriptors) -----------------------------------
    artifacts = [
        Artifact("project", kind="descriptor", path="project.yml", previewable=False,
                 hint="The initialized project (project.yml + folder layout).",
                 help="Produced by project-init; consumed as an ordering dependency by "
                      "steps that write into the project but don't read a media channel "
                      "(e.g. safe-zone generation)."),
        Artifact("base", kind="layer", path="work/base.mp4", previewable=True, z_order=0,
                 hint="The working video (reframed/cut).",
                 help="The base video channel. project.init seeds it; reframe and "
                      "roughcut.render each rewrite it. The previewer's base layer."),
        Artifact("safezone.def", kind="descriptor", path="work/safezone.json",
                 previewable=False,
                 hint="Safe-zone polygon spec.",
                 help="Machine-readable danger/safe polygon (notch-aware). A descriptor: "
                      "caption placement and QC read it; no branch reads another's pixels."),
        Artifact("roughcut.def", kind="manifest", path="work/roughcut.decision.yml",
                 previewable=False,
                 hint="Editable cut decision file.",
                 help="The product of the rough-cut step — one scannable KEEP/DROP line "
                      "per segment. Hand-edit it, then roughcut.render applies it."),
        Artifact("caption.def", kind="manifest", path="work/captions.yml",
                 previewable=False,
                 hint="Editable caption file.",
                 help="Word-timed, glossary-corrected cues. Hand-edit before rendering."),
        Artifact("caption", kind="layer", path="layers/captions.mov", previewable=True,
                 z_order=30, codec_hint="hevc-alpha",
                 hint="Styled caption overlay (transparent).",
                 help="The rendered caption layer the NLE stacks on top. Transparent "
                      "HEVC-alpha — the one previewer alpha case (see the WKWebView spike)."),
        Artifact("qc.report", kind="manifest", path="review/qc-report.json",
                 previewable=False,
                 hint="Safe-zone QC findings.",
                 help="JSON report of danger-zone intrusions and caption-over-face hits. "
                      "Advisory input to the export/composite warning."),
        Artifact("composite", kind="media", path="review/composite.mp4",
                 previewable=True, z_order=100, codec_hint="h264",
                 hint="Flattened preview of all layers.",
                 help="The base + caption/overlay layers composited into one .mp4 — the "
                      "in-app 'assembled result' preview and the muted guide track the "
                      "editor handoffs include on top (highest z-order). A preview/handoff "
                      "intermediate in review/, not the final cut (that is render/, yours "
                      "from the NLE)."),
    ]

    # ---- Export targets -------------------------------------------------------
    # The unified `export <target>` CLI: premiere (FCP7 XML / XMEML, opens in
    # Premiere natively) and fcpxml (FCPXML 1.10 for Resolve / Final Cut) both
    # assemble tracks referencing the media (the composite rides on top as a
    # disabled guide clip); capcut writes an arranged-media folder because CapCut
    # imports no project — the layers + composite are just gathered for hand
    # assembly (shaping brief §3.2; SADD §3.5 "no manifest, arranged media only").
    export_targets = [
        ExportTarget(
            id="premiere", label="Adobe Premiere Pro",
            subcommand="export premiere", bundle="exports/premiere",
            hint="FCP7 XML (XMEML) — opens in Premiere Pro.",
            help="Assembles the base cut + caption overlay as tracks referencing the "
                 "media files (never reconstructed), as FCP7/XMEML XML, with the "
                 "composite as a disabled top-track guide clip. Premiere does not "
                 "import FCPXML, so this is its dedicated target.",
            params=[
                Param("fps", "number", flag="--fps", default=30, min=24, max=60, step=1,
                      hint="Sequence frame rate.",
                      ui=UI(label="FPS", control="slider", group="Sequence")),
            ],
        ),
        ExportTarget(
            id="fcpx", label="Final Cut Pro / DaVinci Resolve",
            subcommand="export fcpxml", bundle="exports/fcpx",
            hint="FCPXML 1.10 — Final Cut / Resolve.",
            help="Same timeline model serialized as FCPXML for Final Cut Pro and "
                 "DaVinci Resolve, with the composite as a disabled top-track guide "
                 "clip.",
            params=[
                Param("fps", "number", flag="--fps", default=30, min=24, max=60, step=1,
                      hint="Sequence frame rate.",
                      ui=UI(label="FPS", control="slider", group="Sequence")),
                Param("event", "string", flag="--event", default="JasonOS",
                      hint="FCPXML event name.",
                      ui=UI(label="Event name", group="Sequence")),
            ],
        ),
        ExportTarget(
            id="capcut", label="CapCut",
            subcommand="export capcut", bundle="exports/capcut",
            hint="Arranged-media folder (no project file).",
            help="CapCut imports no timeline/EDL, so this gathers the rendered layers "
                 "(base cut + caption overlay) and the composite into a folder with a "
                 "README listing the z-order, for hand assembly in CapCut.",
            params=[],
        ),
    ]

    return Schema(
        engine=engine,
        steps=steps,
        tasks=tasks,
        artifacts=artifacts,
        export_targets=export_targets,
    )
