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
    RowField,
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

# Safe-zone modes surfaced as the safezone.gen `mode` dropdown (INI-091). Pulled
# from the Python boundary so the options/default match exactly what the CLI +
# the normalized safe-zone builder accept.
from ..safezone.normalized import DEFAULT_MODE as _SAFE_ZONE_DEFAULT_MODE  # noqa: E402
from ..safezone.normalized import SAFE_ZONE_MODES as _SAFE_ZONE_MODES  # noqa: E402


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


def _format_params() -> list[Param]:
    """Target-format selectors for the reframe step (INI-090): aspect shape +
    resolution tier + optional framing intent. Options come straight from the Python
    boundary (``target_format`` / ``framing``) so the dropdowns match exactly what the
    resolver accepts. Aspect + resolution always emit (format is mandatory); framing is
    opt-in (unset = the legacy centred crop)."""
    from ..reframe.framing import FRAMING_INTENTS
    from ..target_format import ASPECT_PRESETS, DEFAULT_ASPECT, TIERS

    return [
        Param("aspect", "enum", flag="--aspect", options=sorted(ASPECT_PRESETS),
              default=DEFAULT_ASPECT, required=False,
              hint="Target shape (aspect preset).",
              help="The output shape. full-portrait (9:16) is the short-form default; "
                   "also portrait (2:3), wide-portrait (4:5), square (1:1), widescreen "
                   "(16:9), cinematic (21:9), classic-tv (4:3). Drives the crop geometry.",
              example="--aspect full-portrait",
              ui=UI(label="Aspect", control="dropdown", group="Format")),
        Param("resolution", "enum", flag="--resolution", options=["auto", *TIERS],
              default="auto", required=False,
              hint="Resolution tier, or Auto.",
              help="Auto picks the highest tier that fits the crop without upscaling "
                   "beyond 5%; or force 4k / 1440p / 1080p / 720p. Separate from aspect.",
              example="--resolution auto",
              ui=UI(label="Resolution", control="dropdown", group="Format")),
        Param("framing", "enum", flag="--framing", options=sorted(FRAMING_INTENTS),
              default=None, required=False,
              hint="Composition intent (optional).",
              help="performer = widest crop, face high, lower band kept for torso/props "
                   "and captions; talking-head = zoomed on the face; wide-context = scene "
                   "kept, subject centred. Leave unset for the legacy centred crop.",
              example="--framing performer",
              ui=UI(label="Framing", control="dropdown", group="Format")),
    ]


def _caption_style_params() -> list[Param]:
    """The five per-run caption-style controls (INI-088), shared by the caption
    *define* and *render* tasks so the GUI surfaces an identical Style group on
    both and the two argv paths cannot drift. Options/caps come straight from the
    Python boundary (``CaptionStyle``) so the dropdown/slider bounds are exactly
    what ``__post_init__`` enforces. Defaults match the built-in CaptionStyle —
    these are per-run flags with sensible defaults (identity-encoded defaults are
    deferred, INI-088 out of scope), so a GUI run emits them explicitly."""
    from ..captions.style import (
        BG_RADIUS_MAX,
        BG_RADIUS_MIN,
        FONT_ALLOWLIST,
        FONT_SIZE_MAX,
        FONT_SIZE_MIN,
        STROKE_WIDTH_MAX,
        STROKE_WIDTH_MIN,
    )

    return [
        Param("font_family", "enum", flag="--font-family", options=list(FONT_ALLOWLIST),
              default="Helvetica",
              hint="Caption font family.",
              help="The font the captions render in. Curated allowlist (the same set "
                   "the pipeline accepts); Remotion falls back to Helvetica until font "
                   "loading lands (INI-088 Phase 4).",
              ui=UI(label="Font", control="dropdown", group="Style")),
        Param("font_size", "number", flag="--font-size",
              min=FONT_SIZE_MIN, max=FONT_SIZE_MAX, step=2, default=96,
              hint="Caption font size (px at native height).",
              help="Pixel size at the profile's native height (reels = 1920px tall). "
                   "Capped to a legible range at the Python boundary.",
              ui=UI(label="Font size (px)", control="slider", group="Style")),
        Param("fill_color", "string", flag="--fill-color", default="#FFFFFF",
              hint="Text fill color (hex).",
              help="Hex color of the caption text fill, e.g. #FFFFFF. A color-picker "
                   "widget is deferred; enter hex for v1.",
              ui=UI(label="Fill color", control="field", group="Style")),
        Param("stroke_color", "string", flag="--stroke-color", default="#000000",
              hint="Text border/stroke color (hex).",
              help="Hex color of the caption text outline, e.g. #000000.",
              ui=UI(label="Stroke color", control="field", group="Style")),
        Param("stroke_width", "number", flag="--stroke-width",
              min=STROKE_WIDTH_MIN, max=STROKE_WIDTH_MAX, step=1, default=8,
              hint="Text border/stroke thickness (px).",
              help="Outline thickness in px; 0 disables the stroke. Capped at the "
                   "Python boundary.",
              ui=UI(label="Stroke width (px)", control="slider", group="Style")),
        # Background-plate trio (INI-088 Phase 2). bg_color/bg_radius depend on the
        # bg_enabled toggle — the GUI hides them until the plate is switched on.
        Param("bg_enabled", "bool", arity="switch", flag="--bg", default=False,
              hint="Draw a background plate behind the captions.",
              help="A whole-block rounded rectangle behind the caption text, padded "
                   "to clear the stroke. Off by default.",
              ui=UI(label="Background plate", control="toggle", group="Style")),
        Param("bg_color", "string", flag="--bg-color", default="#000000",
              hint="Background plate color (hex).",
              help="Hex fill of the background plate, e.g. #000000.",
              ui=UI(label="Plate color", control="field", group="Style",
                    depends_on_key="bg_enabled", depends_on_equals=True)),
        Param("bg_radius", "number", flag="--bg-radius",
              min=BG_RADIUS_MIN, max=BG_RADIUS_MAX, step=1, default=0,
              hint="Background plate corner radius (px).",
              help="Rounded-corner radius of the plate in px; 0 = square corners.",
              ui=UI(label="Plate corner radius (px)", control="slider", group="Style",
                    depends_on_key="bg_enabled", depends_on_equals=True)),
        # Horizontal placement (INI-088 Phase 3).
        Param("h_offset", "enum", flag="--h-offset",
              options=["clear-notch", "center"], default="clear-notch",
              hint="Horizontal placement of the caption block.",
              help="clear-notch fills the widest notch-free span (wider, may bias "
                   "left of frame-center at lower-third); center keeps the block "
                   "symmetric about frame-center while still clearing the notch.",
              ui=UI(label="Horizontal placement", control="dropdown", group="Style")),
    ]


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
             hint="Reframe the source to the target format.",
             help="Tracks the subject and crops the source to the target format — "
                  "portrait, square, or landscape. "
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
        Step("overlay", "Overlays", order=65,
             hint="Timed/placed content overlays (image / video / source card).",
             help="Places content overlays — a still, a video asset, or a generated "
                  "article card — on a source-time window proposed from the transcript, "
                  "as an editable overlay.def. Renders the placed/timed composite and "
                  "emits overlay.occupancy so captions dodge overlays and QC stays aware."),
        Step("composite", "Composite", order=70,
             hint="Flatten the layers into one preview render.",
             help="Stacks the base and the caption/overlay layers into a single "
                  "previewable video — the in-app preview of the assembled result and "
                  "the guide track the editor handoffs carry on top. A review/handoff "
                  "intermediate, not the final cut (that is yours, from your NLE)."),
        Step("output", "Output / Packaging", order=80,
             hint="Package the project for your editor.",
             help="Editor handoffs: enable the target(s) you want and they run in the "
                  "batch like any other step. Premiere (FCP7 XML) and Final Cut / "
                  "Resolve (FCPXML) assemble the cut + caption + composite-guide tracks; "
                  "CapCut gathers the rendered layers into a folder (it imports no "
                  "project file)."),
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
        hint="Derive the safe-zone spec (none / generic / custom).",
        help="Builds the safe-zone spec consumed downstream. INI-091 modes: none "
             "(full frame is safe), generic (per-aspect conservative insets, the "
             "default), or custom (trace a design template PNG where the danger "
             "region is marked by alpha or a key color).",
        params=[
            # INI-091 safe-zone mode. Options/default come from the Python boundary
            # (normalized.SAFE_ZONE_MODES / DEFAULT_MODE) so the dropdown matches what
            # the CLI accepts. The template + region-key controls are conditional on
            # mode=custom (the only mode that reads a PNG).
            Param("mode", "enum", flag="--mode",
                  options=list(_SAFE_ZONE_MODES), default=_SAFE_ZONE_DEFAULT_MODE,
                  hint="Safe-zone mode.",
                  help="none = the full frame is safe; generic = per-aspect "
                       "conservative inset rectangle (the default); custom = trace a "
                       "template PNG for the exact destination. Generic social safe "
                       "zones are conservative estimates. For platform-specific "
                       "publishing, use a safe-zone template for the exact destination "
                       "platform and placement.",
                  example="--mode generic",
                  ui=UI(label="Safe-zone mode", control="dropdown", group="Mode")),
            Param("template", "path", arity="positional", required=False, order=0,
                  hint="Template PNG with the danger region marked (custom mode).",
                  help="The design template image (custom mode only). The marked region "
                       "becomes the danger polygon (notch-aware).",
                  example="template.png",
                  path=PathSpec(kind="file", extensions=["png", "jpg", "jpeg", "webp"]),
                  ui=UI(label="Template PNG", group="Input",
                        depends_on_key="mode", depends_on_equals="custom")),
            _profile_param(required=False, default=None),
            Param("key", "enum", flag="--key", options=["auto", "alpha", "color"],
                  default="auto",
                  hint="How the danger region is marked (custom mode).",
                  help="auto detects alpha vs a key color; force alpha or color if "
                       "detection guesses wrong.",
                  ui=UI(label="Region key", control="dropdown", group="Input",
                        depends_on_key="mode", depends_on_equals="custom")),
        ],
    ))

    # reframe: base -> base.
    tasks.append(Task(
        id="reframe", step="reframe", label="Reframe to target format",
        subcommand="reframe", optional=True,
        consumes=["base"], produces=["base", "reframed", "subject.occupancy"],
        io=[
            IOBinding(artifact="base", role="input", via="positional", order=0),
            IOBinding(artifact="base", role="output", via="flag", flag="-o"),
            IOBinding(artifact="reframed", role="output", via="flag",
                      flag="--reframed-out"),
            IOBinding(artifact="subject.occupancy", role="output", via="flag",
                      flag="--occupancy-out"),
        ],
        hint="Crop the source to the target format (portrait, square, or landscape).",
        help="Subject-tracking crop. Static holds one crop; dynamic follows the subject. "
             "Daily-driver path needs MediaPipe; --dry-run plans without rendering.",
        params=[
            *_format_params(),
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
            Param("scale", "number", flag="--scale", min=1.0, max=2.5, step=0.05,
                  hint="Punch-in override (optional).",
                  help="Overrides the framing intent's zoom. 1.0 = widest native crop "
                       "(the most of the source the aspect allows, no fill); higher "
                       "punches in. There is no pull-back past 1.0 by design — native "
                       "is the widest. Leave unset to let Framing decide.",
                  ui=UI(label="Punch-in (override)", control="slider", group="Framing")),
            Param("subject_y", "number", flag="--subject-y", min=-1.0, max=1.0, step=0.05,
                  hint="Vertical anchor override (optional, bipolar).",
                  help="Where the subject sits vertically: -1 = top, 0 = centred, "
                       "+1 = bottom. Only bites when there is vertical slack (punched "
                       "in, or a source taller than the target). Unset = let Framing decide.",
                  ui=UI(label="Subject Y (override)", control="slider", group="Framing")),
            # INI-091 Phase 5: set-box pan anchor + composition lock. pan_x/pan_y are
            # the relative crop placement (0..1) the lock holds; lock=none keeps the
            # legacy auto-tracked crop.
            Param("pan_x", "number", flag="--pan-x", min=0.0, max=1.0, step=0.01,
                  hint="Set-box horizontal anchor (0..1).",
                  help="Where the crop window sits horizontally: 0 = hard left, 0.5 = "
                       "centred, 1 = hard right. The set-box placement the composition "
                       "lock holds on the X axis. Unset = let the tracker decide.",
                  ui=UI(label="Pan X", control="slider", group="Framing")),
            Param("pan_y", "number", flag="--pan-y", min=0.0, max=1.0, step=0.01,
                  hint="Set-box vertical anchor (0..1).",
                  help="Where the crop window sits vertically: 0 = top, 0.5 = centred, "
                       "1 = bottom. The set-box placement the composition lock holds on "
                       "the Y axis. Unset = let the tracker decide.",
                  ui=UI(label="Pan Y", control="slider", group="Framing")),
            Param("lock", "enum", flag="--lock", options=["none", "x", "y", "both"],
                  default="none",
                  hint="Composition lock axis.",
                  help="Hold the set-box on the given axis instead of following the "
                       "subject: none = follow (default), x = hold horizontal, y = hold "
                       "vertical, both = a fully static composed crop.",
                  example="--lock both",
                  ui=UI(label="Composition lock", control="dropdown", group="Framing")),
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
            *_caption_style_params(),
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
            Param("subject_occupancy", "path", flag="--subject-occupancy",
                  path=PathSpec(kind="file", extensions=["json"]),
                  hint="Subject occupancy from reframe (optional) — captions dodge it.",
                  help="Point at the reframe step's occupancy file (work/reframe."
                       "occupancy.json, INI-090 Phase 2). When set, captions relocate "
                       "off the subject; absent or missing, captions render normally.",
                  ui=UI(label="Subject occupancy", control="picker", group="Placement")),
            Param("position", "enum", flag="--position",
                  options=["upper-third", "center", "lower-third"],
                  hint="Caption anchor (optional override).",
                  help="Forces the caption band; overrides the style default and any "
                       "framing-intent hint carried in the occupancy file.",
                  ui=UI(label="Caption position", control="dropdown", group="Placement")),
            Param("karaoke", "bool", arity="switch", flag="--karaoke", default=False,
                  hint="Active-word highlight.",
                  ui=UI(label="Karaoke highlight", control="toggle", group="Style")),
            *_caption_style_params(),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Build props without rendering.",
                  ui=UI(label="Dry run", control="toggle", group="Style")),
        ],
    ))

    # caption.preview: bake the caption layer over a checkerboard -> previewable
    # h264 proxy (GUI-only; the webview can't decode the alpha .mov directly).
    tasks.append(Task(
        id="caption.preview", step="caption", label="Build caption preview",
        subcommand="proxy", optional=True,
        consumes=["caption"], produces=["caption.preview"],
        io=[
            IOBinding(artifact="caption", role="input", via="positional", order=0),
            IOBinding(artifact="caption.preview", role="output", via="flag", flag="-o"),
        ],
        hint="Checkerboard-baked preview of the caption layer.",
        help="Renders the transparent caption overlay over a neutral checkerboard "
             "into plain h264 so the previewer can play it in isolation. Optional — "
             "enable it only when you want to preview the caption layer in the app.",
        params=[
            _profile_param(required=False),
            Param("square", "number", flag="--square", min=4, max=64, step=4, default=16,
                  hint="Checkerboard cell size (px).",
                  ui=UI(label="Checker size", control="slider", group="Preview")),
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

    # overlay.define: author the editable overlay decision file (overlay.def).
    # The product the CEO edits; windows can be proposed from the transcript.
    tasks.append(Task(
        id="overlay.define", step="overlay", label="Build overlay file",
        subcommand="overlay", optional=True,
        consumes=[], produces=["overlay.def"],
        io=[
            IOBinding(artifact="overlay.def", role="output", via="flag", flag="-o"),
        ],
        hint="Add one or more overlays — image, video, or source card.",
        help="Writes overlay.def — one line per overlay (kind, src, window, placement, "
             "transition). Add overlays in the Overlays table below; each row becomes "
             "a --add entry. Set a window explicitly (start/end) or let it be proposed "
             "from the spoken span (a phrase in 'at' + a Transcript). You can also "
             "hand-edit the file afterward, then render.",
        params=[
            _profile_param(required=False),
            Param("transcript", "path", flag="--transcript",
                  hint="Word-level transcript JSON for window proposing.",
                  help="When given, an overlay row's window can be proposed from the "
                       "span where its subject is discussed (fill the row's 'at' field "
                       "with a phrase). The AI-leverage step; the LLM never touches the "
                       "render path.",
                  path=PathSpec(kind="file", extensions=["json"]),
                  ui=UI(label="Transcript", group="Timing")),
            Param("source", "string", flag="--source",
                  hint="Base clip name recorded in the file header (advisory).",
                  help="A label written into the file's `source:` header for reference. "
                       "Not the asset and not the base video — the base is supplied at "
                       "render time (Render overlays → base video).",
                  ui=UI(label="Source label", group="Advanced")),
            Param(
                "overlays", "string", arity="rows", flag="--add",
                hint="The overlays to place — one row each.",
                help="Each row is one overlay. Pick a Kind, point it at an asset path "
                     "(Src), set its on-screen window (Start/End seconds, or a Discussed "
                     "phrase with a Transcript loaded), and choose a Placement "
                     "(full-bleed / bottom-half / a PiP rect). A video can duck or mute "
                     "its own audio. Each row emits one --add entry.",
                ui=UI(label="Overlays", group="Overlays"),
                row=[
                    RowField("kind", "Kind", "dropdown",
                             options=["image", "video", "card"], default="image",
                             hint="image = still; video = clip; card = generated source card."),
                    RowField("src", "Src (asset path)", "field",
                             placeholder="assets/chart.png",
                             hint="Path to the image/video asset (or the rendered card)."),
                    RowField("at", "Discussed phrase", "field",
                             placeholder="the Q3 chart  (needs a Transcript)",
                             hint="Propose the window from where this is discussed."),
                    RowField("start", "Start (s)", "field", placeholder="3.2",
                             hint="Explicit window start, source-time seconds."),
                    RowField("end", "End (s)", "field", placeholder="7.8",
                             hint="Explicit window end, source-time seconds."),
                    RowField("placement", "Placement", "dropdown",
                             options=["full-bleed", "bottom-half", "pip-rect"],
                             default="full-bleed",
                             hint="Where it sits; pip-rect needs a Rect."),
                    RowField("rect", "PiP rect (x,y,w,h)", "field",
                             placeholder="60,1180,420,560",
                             hint="Pixel rect for placement = pip-rect."),
                    RowField("transition", "Transition", "dropdown",
                             options=["cut", "fade"], default="cut",
                             hint="Hard cut or a fade in/out."),
                    RowField("fade", "Fade (s)", "field", placeholder="0.3",
                             hint="Fade duration when Transition = fade."),
                    RowField("audio", "Audio (video)", "dropdown",
                             options=["keep", "duck", "mute"], default="keep",
                             hint="A video overlay's own audio."),
                    RowField("text", "Label", "field", placeholder="what it shows",
                             hint="A human label recorded with the overlay."),
                ],
            ),
        ],
    ))

    # overlay.card: capture a URL into an editable card-content JSON (Phase B).
    tasks.append(Task(
        id="overlay.card", step="overlay", label="Capture source card",
        subcommand="overlay-card", optional=True,
        consumes=[], produces=["card.content"],
        io=[
            IOBinding(artifact="card.content", role="output", via="flag", flag="-o"),
        ],
        hint="Capture an article/page into editable card content.",
        help="Fetches a URL (Chrome / Jina, daily-driver) and structures it into a "
             "reviewable card.content JSON (heading/body/footer/image/citation) you "
             "edit before rendering the card overlay.",
        params=[
            Param("url", "string", arity="positional", order=0, required=True,
                  hint="Article / page URL to capture.",
                  example="https://example.com/article",
                  ui=UI(label="URL", group="Input")),
            Param("max_body", "number", flag="--max-body", min=80, max=600, step=10,
                  default=280,
                  hint="Body character budget.",
                  ui=UI(label="Max body chars", control="slider", group="Content")),
            Param("max_heading", "number", flag="--max-heading", min=40, max=200, step=10,
                  default=120,
                  hint="Heading character budget.",
                  ui=UI(label="Max heading chars", control="slider", group="Content")),
        ],
    ))

    # overlay.render: overlay.def + base + safezone -> placed/timed composite +
    # the overlay.occupancy descriptor (captions/QC consume it).
    tasks.append(Task(
        id="overlay.render", step="overlay", label="Render overlays",
        subcommand="overlay-render", optional=True,
        consumes=["overlay.def", "base", "safezone.def"],
        produces=["overlay.composite", "overlay.occupancy"],
        io=[
            IOBinding(artifact="overlay.def", role="input", via="positional", order=0),
            IOBinding(artifact="base", role="input", via="flag", flag="-i"),
            IOBinding(artifact="overlay.composite", role="output", via="flag", flag="-o"),
            IOBinding(artifact="safezone.def", role="input", via="flag", flag="--safezone"),
            IOBinding(artifact="overlay.occupancy", role="output", via="flag",
                      flag="--occupancy"),
        ],
        hint="Composite the placed/timed overlays + emit occupancy.",
        help="Reads the (edited) overlay.def, places/scales/times each overlay over the "
             "base, and writes the preview composite + the overlay.occupancy descriptor "
             "(frame size from the safe-zone spec). Re-runnable after editing overlay.def.",
        params=[
            Param("crf", "number", flag="--crf", min=0, max=51, step=1, default=18,
                  hint="x264 quality (lower = better).",
                  ui=UI(label="Quality (CRF)", control="slider", group="Render")),
            Param("dry_run", "bool", arity="switch", flag="--dry-run", default=False,
                  hint="Show the ffmpeg command without rendering.",
                  ui=UI(label="Dry run", control="toggle", group="Render")),
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

    # ---- Output / packaging tasks (editor handoffs) ---------------------------
    # Modeled as ordinary schedulable tasks: enable the target(s) you want and they
    # run in the batch after the layers exist. The XML targets reference the
    # reframed-UNCUT clip + lay the decision's KEEP segments as trimmable clips.
    _export_fps = Param("fps", "number", flag="--fps", min=24, max=60, step=1, default=30,
                        hint="Sequence frame rate.",
                        ui=UI(label="FPS", control="slider", group="Sequence"))

    tasks.append(Task(
        id="export.premiere", step="output", label="Export — Premiere Pro",
        subcommand="export premiere", optional=True,
        consumes=["roughcut.def", "reframed", "caption.def", "composite"],
        produces=["export.premiere"],
        io=[
            IOBinding(artifact="roughcut.def", role="input", via="positional", order=0),
            IOBinding(artifact="reframed", role="input", via="flag", flag="--reframed"),
            IOBinding(artifact="caption.def", role="input", via="flag", flag="--captions"),
            IOBinding(artifact="composite", role="input", via="flag", flag="--composite"),
            IOBinding(artifact="export.premiere", role="output", via="flag", flag="-o"),
        ],
        hint="FCP7 XML (XMEML) — opens in Premiere Pro.",
        help="Assembles the base cut (the decision's KEEP segments over the reframed-"
             "uncut clip, as trimmable clips) + caption track + the composite as a "
             "disabled top guide track, as FCP7/XMEML XML. Premiere does not import "
             "FCPXML, so this is its dedicated target.",
        params=[_export_fps],
    ))

    tasks.append(Task(
        id="export.fcpx", step="output", label="Export — Final Cut / Resolve",
        subcommand="export fcpxml", optional=True,
        consumes=["roughcut.def", "reframed", "caption.def", "composite"],
        produces=["export.fcpx"],
        io=[
            IOBinding(artifact="roughcut.def", role="input", via="positional", order=0),
            IOBinding(artifact="reframed", role="input", via="flag", flag="--reframed"),
            IOBinding(artifact="caption.def", role="input", via="flag", flag="--captions"),
            IOBinding(artifact="composite", role="input", via="flag", flag="--composite"),
            IOBinding(artifact="export.fcpx", role="output", via="flag", flag="-o"),
        ],
        hint="FCPXML 1.10 — Final Cut Pro / DaVinci Resolve.",
        help="The same timeline (base cut + captions + composite guide) serialized as "
             "FCPXML 1.10 for Final Cut Pro and DaVinci Resolve.",
        params=[
            _export_fps,
            Param("event", "string", flag="--event", default="JasonOS",
                  hint="FCPXML event name.",
                  ui=UI(label="Event name", group="Sequence")),
        ],
    ))

    tasks.append(Task(
        id="export.capcut", step="output", label="Export — CapCut",
        subcommand="export capcut", optional=True,
        consumes=["base", "caption", "composite"],
        produces=["export.capcut"],
        io=[
            IOBinding(artifact="base", role="input", via="flag", flag="--base"),
            IOBinding(artifact="caption", role="input", via="flag", flag="--captions"),
            IOBinding(artifact="composite", role="input", via="flag", flag="--composite"),
            IOBinding(artifact="export.capcut", role="output", via="flag", flag="-o"),
        ],
        hint="Arranged-media folder (CapCut imports no project).",
        help="Gathers the rendered layers (the cut base + caption overlay) and the "
             "composite into a folder with a README listing the z-order, for hand "
             "assembly in CapCut.",
        params=[],
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
        Artifact("reframed", kind="media", path="work/reframed.mp4", previewable=False,
                 hint="Reframed-uncut clip (editor-handoff source).",
                 help="A stable copy of the reframed-but-uncut video, written by reframe "
                      "alongside base.mp4. The editor handoffs reference this (not base, "
                      "which roughcut.render rewrites with the cut) so the decision file's "
                      "KEEP segments lay over the full clip as separate, trimmable clips."),
        Artifact("subject.occupancy", kind="descriptor", path="work/reframe.occupancy.json",
                 previewable=False,
                 hint="The subject's footprint in the reframed frame.",
                 help="Written by reframe (INI-090): the tracked subject's box projected "
                      "into the target frame, as caption avoid-windows. The caption render "
                      "optionally consumes it so captions dodge the subject."),
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
        Artifact("caption", kind="layer", path="layers/captions.mov", previewable=False,
                 z_order=30, codec_hint="hevc-alpha",
                 hint="Styled caption overlay (transparent).",
                 help="The rendered caption layer the NLE stacks on top. Transparent "
                      "HEVC-alpha — not previewed directly (the webview can't be relied "
                      "on to decode alpha); the caption.preview proxy is its previewable "
                      "form."),
        Artifact("caption.preview", kind="media", path="layers/captions.preview.mp4",
                 previewable=True, z_order=30, codec_hint="h264",
                 hint="Caption layer over a checkerboard (preview).",
                 help="The caption overlay baked over a neutral checkerboard into plain "
                      "h264 so the previewer can play it in isolation. A GUI-only proxy — "
                      "never bundled into an editor export."),
        Artifact("overlay.def", kind="manifest", path="work/overlay.def.yml",
                 previewable=False,
                 hint="Editable overlay decision file.",
                 help="One line per overlay (kind, src, source-time window, placement, "
                      "transition). The product — hand-edit before rendering, mirrors "
                      "roughcut.def / caption.def."),
        Artifact("card.content", kind="manifest", path="work/card.content.json",
                 previewable=False,
                 hint="Editable source-card content.",
                 help="heading / body / footer / image / citation captured from a URL. "
                      "Reviewable JSON the card overlay renders from."),
        Artifact("overlay.occupancy", kind="descriptor", path="work/overlay.occupancy.json",
                 previewable=False,
                 hint="Overlay footprints + windows.",
                 help="The rect + time window each overlay occupies. A descriptor: caption "
                      "placement dodges it and QC flags intrusions; no branch reads "
                      "another's pixels."),
        Artifact("overlay.composite", kind="media", path="review/overlay-composite.mp4",
                 previewable=True, z_order=90, codec_hint="h264",
                 hint="Base + placed/timed overlays (preview).",
                 help="The base with the overlay layers placed/timed/faded into one "
                      "previewable .mp4. A review intermediate, not the final cut."),
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
        Artifact("export.premiere", kind="manifest", path="exports/premiere/project.xml",
                 previewable=False,
                 hint="Premiere FCP7 XML project.",
                 help="The assembled editor project (FCP7/XMEML XML) the Premiere export "
                      "writes; open it in Premiere Pro."),
        Artifact("export.fcpx", kind="manifest", path="exports/fcpx/project.fcpxml",
                 previewable=False,
                 hint="FCPXML 1.10 project.",
                 help="The assembled editor project (FCPXML 1.10) for Final Cut Pro / "
                      "DaVinci Resolve."),
        Artifact("export.capcut", kind="manifest", path="exports/capcut",
                 previewable=False,
                 hint="CapCut arranged-media folder.",
                 help="The folder of rendered layers + composite + README the CapCut "
                      "export gathers for hand assembly."),
    ]

    # ---- Export targets -------------------------------------------------------
    # Editor handoffs are now modeled as ordinary tasks in the "output" step (above)
    # so they get an enable checkbox + settings and run in the batch like every
    # other step — the dedicated export_targets surface is retired (kept empty for
    # schema-shape compatibility).
    export_targets: list[ExportTarget] = []

    return Schema(
        engine=engine,
        steps=steps,
        tasks=tasks,
        artifacts=artifacts,
        export_targets=export_targets,
    )
