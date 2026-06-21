"""Dataclasses for the GUI control-tower schema (the SSOT contract).

This is the *instance* side of the schema contract described in the
control-tower SADD (INI-087). The pipeline authors a schema document here;
the GUI's Rust validator owns the canonical *grammar* (``schema/meta-schema.json``
in the video-pipeline-gui repo) and is the deny-by-default enforcement point.

Design rules (from the SADD):
  * Tenet 3 (zero core hardcoding): the GUI bakes in no step/flag/layer. Every
    addressable surface is described here and discovered at runtime.
  * Tenet 2 (simplify without obfuscating): every node carries ``label``/``hint``/
    ``help`` plus, for params, the underlying ``flag`` and an ``example`` — the
    same fields feed the GUI tooltips/help panel and the CLI ``--help``.
  * §3.2 artifacts are *channels*: a consumer binds to the latest enabled writer
    of a channel, so skipping a step rewires cleanly.
  * §3.3 cross-branch edges stay metadata-weight via *descriptor* artifacts.

Everything here is pure data with deterministic ``to_dict()`` serialization;
there is no I/O and no dependency on the rest of the package, so it imports
cheaply and tests without ffmpeg/whisper present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Grammar version. Bumped only when the *shape* of a node changes (a new field,
# a new control kind), never when the pipeline adds a step/flag. The split
# trigger in the SADD (§9.1) is this reaching a stable "1.0".
SCHEMA_VERSION = "0.1.0"

# Deterministic param-type -> default control mapping (SADD §3.4). A param may
# override via ``ui.control``; this is the fallback the GUI applies.
DEFAULT_CONTROL = {
    "bool": "toggle",
    "enum": "dropdown",
    "string": "field",
    "path": "picker",
    # number resolves to slider when bounded, stepper otherwise (see Param).
    "number": "stepper",
}

# How the GUI turns a param + its form value into argv tokens.
#   positional : bare value, order-significant, no flag
#   value      : ``<flag> <value>``
#   switch     : ``<flag>`` emitted only when the bool value is true (store_true)
ARITY = {"positional", "value", "switch"}


@dataclass(frozen=True)
class UI:
    """Presentation hints for a param. None fields fall back to deterministic
    defaults so the schema stays terse — only override when it earns it."""

    label: str
    control: str | None = None          # toggle|slider|stepper|dropdown|field|picker
    group: str | None = None            # form section header
    depends_on_key: str | None = None   # conditional visibility: show when...
    depends_on_equals: Any = None       # ...sibling param == this value

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"label": self.label}
        if self.control:
            d["control"] = self.control
        if self.group:
            d["group"] = self.group
        if self.depends_on_key is not None:
            d["depends_on"] = {"key": self.depends_on_key, "equals": self.depends_on_equals}
        return d


@dataclass(frozen=True)
class PathSpec:
    """Selection metadata for a ``type="path"`` param (SADD §3.4).

    Drives the GUI's native file/folder picker and drag-drop filtering so the
    operator can only choose the right kind of object. The GUI hardcodes none of
    this — a new picker's behavior is described here and discovered at runtime.

    ``kind``       — "file" (default) or "directory".
    ``extensions`` — lowercase, dot-less file extensions to mask to (files only).
    ``multiple``   — allow selecting more than one path.
    """

    kind: str = "file"                      # file | directory
    extensions: list[str] | None = None     # files only, e.g. ["png", "jpg"]
    multiple: bool = False

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.extensions:
            d["extensions"] = list(self.extensions)
        if self.multiple:
            d["multiple"] = True
        return d


@dataclass(frozen=True)
class ComposePart:
    """One labelled sub-field of a composed value (SADD §3.4).

    ``control``: field | dropdown | date. ``default`` "today" on a date control
    pre-fills the current date in the GUI."""

    key: str
    label: str
    control: str = "field"
    options: list[str] | None = None
    default: str | None = None
    hint: str = ""
    placeholder: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"key": self.key, "label": self.label, "control": self.control}
        if self.options is not None:
            d["options"] = list(self.options)
        if self.default is not None:
            d["default"] = self.default
        if self.hint:
            d["hint"] = self.hint
        if self.placeholder is not None:
            d["placeholder"] = self.placeholder
        return d


@dataclass(frozen=True)
class Compose:
    """Build one param value from labelled sub-fields, so the GUI can ask for the
    parts of a conventional string (e.g. a project folder name) instead of a free
    string the user has to format correctly. ``template`` interpolates ``{key}``
    placeholders; the assembled string is the param's value (and its argv token)."""

    template: str
    parts: list[ComposePart]

    def to_dict(self) -> dict[str, Any]:
        return {"template": self.template, "parts": [p.to_dict() for p in self.parts]}


@dataclass(frozen=True)
class Param:
    """A single controllable input to a task.

    ``arity`` governs argv assembly. ``type`` governs the control. A bounded
    number (both ``min`` and ``max`` set) defaults to a slider; otherwise a
    stepper field.
    """

    key: str
    type: str                       # bool|number|enum|string|path
    ui: UI
    arity: str = "value"            # positional|value|switch
    order: int = 0                  # positional placement on argv (with io positionals)
    flag: str | None = None         # e.g. "--wpc-max"; None for positional
    default: Any = None
    required: bool = False
    options: list[Any] | None = None        # enum
    min: float | None = None
    max: float | None = None
    step: float | None = None
    hint: str = ""                  # one-line tooltip
    help: str = ""                  # docked-panel long form
    example: str | None = None      # example invocation fragment
    compose: "Compose | None" = None  # build this value from labelled sub-fields
    path: PathSpec | None = None    # type="path" only: picker/drag-drop metadata

    def resolved_control(self) -> str:
        if self.ui.control:
            return self.ui.control
        if self.type == "number" and self.min is not None and self.max is not None:
            return "slider"
        return DEFAULT_CONTROL.get(self.type, "field")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "key": self.key,
            "type": self.type,
            "arity": self.arity,
            "control": self.resolved_control(),
            "ui": self.ui.to_dict(),
        }
        if self.arity == "positional":
            d["order"] = self.order
        if self.flag is not None:
            d["flag"] = self.flag
        if self.default is not None:
            d["default"] = self.default
        if self.required:
            d["required"] = True
        if self.options is not None:
            d["options"] = list(self.options)
        for k in ("min", "max", "step"):
            v = getattr(self, k)
            if v is not None:
                d[k] = v
        if self.hint:
            d["hint"] = self.hint
        if self.help:
            d["help"] = self.help
        if self.example:
            d["example"] = self.example
        if self.compose is not None:
            d["compose"] = self.compose.to_dict()
        if self.path is not None:
            d["path"] = self.path.to_dict()
        return d


@dataclass(frozen=True)
class Step:
    """A UI grouping the user thinks about (Reframe, Caption...). Contains one
    or more schedulable tasks (SADD §3.1)."""

    id: str
    label: str
    order: int
    optional: bool = True
    hint: str = ""
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = {"id": self.id, "label": self.label, "order": self.order, "optional": self.optional}
        if self.hint:
            d["hint"] = self.hint
        if self.help:
            d["help"] = self.help
        return d


@dataclass(frozen=True)
class IOBinding:
    """Ties a consumed/produced artifact to its place on the command line, so
    the GUI scheduler can assemble runnable argv from the graph alone.

    The scheduler resolves the artifact id to a concrete file path (a consumed
    channel binds to the *latest enabled writer*'s path; a produced artifact uses
    its declared path) and places it on argv either positionally or behind a flag.
    Outputs are also what the process supervisor checks for on disk post-exit.
    """

    artifact: str
    role: str                 # input | output
    via: str                  # positional | flag
    flag: str | None = None   # required when via == flag (e.g. "-o", "--safezone")
    order: int = 0            # positional ordering within the task

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"artifact": self.artifact, "role": self.role, "via": self.via}
        if self.flag is not None:
            d["flag"] = self.flag
        if self.via == "positional":
            d["order"] = self.order
        return d


@dataclass(frozen=True)
class Task:
    """An atomic schedulable graph node (SADD §3.1/§3.4).

    ``subcommand`` is appended to ``engine.cli_entrypoint`` to form argv — it is
    the *real* CLI subcommand so the resolved command actually runs. ``consumes``
    and ``produces`` are artifact-channel ids; they are the edges of the DAG.
    ``io`` binds those artifacts onto the command line (see IOBinding).
    """

    id: str
    step: str
    label: str
    subcommand: str
    params: list[Param]
    consumes: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    io: list[IOBinding] = field(default_factory=list)
    optional: bool = True
    hint: str = ""
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "step": self.step,
            "label": self.label,
            "subcommand": self.subcommand,
            "optional": self.optional,
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "io": [b.to_dict() for b in self.io],
            "params": [p.to_dict() for p in self.params],
        }
        if self.hint:
            d["hint"] = self.hint
        if self.help:
            d["help"] = self.help
        return d


@dataclass(frozen=True)
class Artifact:
    """A channel or descriptor — the edges of the dependency graph (SADD §3.2/§3.3).

    ``kind``: layer | descriptor | media | manifest. ``previewable`` layers feed
    the previewer's source list. ``z_order`` stacks layers in the composite/export.
    ``path`` is relative to the project root.
    """

    id: str
    kind: str
    path: str
    previewable: bool = False
    z_order: int | None = None
    codec_hint: str | None = None
    hint: str = ""
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "previewable": self.previewable,
        }
        if self.z_order is not None:
            d["z_order"] = self.z_order
        if self.codec_hint:
            d["codec_hint"] = self.codec_hint
        if self.hint:
            d["hint"] = self.hint
        if self.help:
            d["help"] = self.help
        return d


@dataclass(frozen=True)
class ExportTarget:
    """An editor packaging target (SADD §3.5). ``subcommand`` is the real CLI
    invocation; adding an editor later = one exporter in Python + one entry here,
    no GUI change."""

    id: str
    label: str
    subcommand: str
    bundle: str
    params: list[Param] = field(default_factory=list)
    hint: str = ""
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "subcommand": self.subcommand,
            "bundle": self.bundle,
            "params": [p.to_dict() for p in self.params],
        }
        if self.hint:
            d["hint"] = self.hint
        if self.help:
            d["help"] = self.help
        return d


@dataclass(frozen=True)
class Engine:
    name: str
    version: str
    schema_version: str
    cli_entrypoint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "schema_version": self.schema_version,
            "cli_entrypoint": self.cli_entrypoint,
        }


@dataclass(frozen=True)
class Schema:
    engine: Engine
    steps: list[Step]
    tasks: list[Task]
    artifacts: list[Artifact]
    export_targets: list[ExportTarget]

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine.to_dict(),
            "steps": [s.to_dict() for s in self.steps],
            "tasks": [t.to_dict() for t in self.tasks],
            "artifacts": [a.to_dict() for a in self.artifacts],
            "export_targets": [e.to_dict() for e in self.export_targets],
        }

    # --- consistency helpers (used by tests and by `schema --check`) ---

    def artifact_ids(self) -> set[str]:
        return {a.id for a in self.artifacts}

    def writers_of(self, channel: str) -> list[str]:
        """Task ids that produce ``channel``, in declaration order (= the chain
        the GUI resolves 'latest enabled writer' over)."""
        return [t.id for t in self.tasks if channel in t.produces]

    def validate(self) -> list[str]:
        """Structural self-check. Returns a list of human-readable problems;
        empty list == conformant. This mirrors (a subset of) what the Rust
        gateway enforces, so the pipeline can fail loudly before emitting."""
        problems: list[str] = []
        step_ids = {s.id for s in self.steps}
        art_ids = self.artifact_ids()

        for t in self.tasks:
            if t.step not in step_ids:
                problems.append(f"task {t.id!r} references unknown step {t.step!r}")
            for c in t.consumes:
                if c not in art_ids:
                    problems.append(f"task {t.id!r} consumes unknown artifact {c!r}")
            for p in t.produces:
                if p not in art_ids:
                    problems.append(f"task {t.id!r} produces unknown artifact {p!r}")
            keys = [p.key for p in t.params]
            if len(keys) != len(set(keys)):
                problems.append(f"task {t.id!r} has duplicate param keys")
            for p in t.params:
                if p.arity not in ARITY:
                    problems.append(f"task {t.id!r} param {p.key!r} has bad arity {p.arity!r}")
                if p.arity in ("value", "switch") and not p.flag:
                    problems.append(f"task {t.id!r} param {p.key!r} ({p.arity}) needs a flag")
                if p.arity == "positional" and p.flag:
                    problems.append(f"task {t.id!r} param {p.key!r} is positional but has a flag")
                if p.type == "enum" and not p.options:
                    problems.append(f"task {t.id!r} param {p.key!r} is enum without options")
                # depends_on must reference a sibling param key
                if p.ui.depends_on_key is not None and p.ui.depends_on_key not in keys:
                    problems.append(
                        f"task {t.id!r} param {p.key!r} depends_on unknown sibling "
                        f"{p.ui.depends_on_key!r}"
                    )
                # path metadata is only meaningful on a path param, and extensions
                # only on a file picker
                if p.path is not None:
                    if p.type != "path":
                        problems.append(
                            f"task {t.id!r} param {p.key!r} has path metadata but is "
                            f"type {p.type!r}, not 'path'"
                        )
                    if p.path.kind not in ("file", "directory"):
                        problems.append(
                            f"task {t.id!r} param {p.key!r} path.kind must be "
                            f"'file' or 'directory', got {p.path.kind!r}"
                        )
                    if p.path.extensions and p.path.kind != "file":
                        problems.append(
                            f"task {t.id!r} param {p.key!r} path.extensions set on a "
                            f"non-file picker (kind={p.path.kind!r})"
                        )
            # io bindings must reference declared consumes/produces and be runnable
            for b in t.io:
                if b.role == "input" and b.artifact not in t.consumes:
                    problems.append(f"task {t.id!r} io binds input {b.artifact!r} not in consumes")
                if b.role == "output" and b.artifact not in t.produces:
                    problems.append(f"task {t.id!r} io binds output {b.artifact!r} not in produces")
                if b.role not in ("input", "output"):
                    problems.append(f"task {t.id!r} io binding has bad role {b.role!r}")
                if b.via == "flag" and not b.flag:
                    problems.append(f"task {t.id!r} io binding for {b.artifact!r} needs a flag")
                if b.via not in ("positional", "flag"):
                    problems.append(f"task {t.id!r} io binding has bad via {b.via!r}")

        # every previewable layer must declare a path; every consumed channel
        # must have at least one producer somewhere (else it can never resolve)
        for a in self.artifacts:
            if a.previewable and not a.path:
                problems.append(f"previewable artifact {a.id!r} has no path")
        produced = {p for t in self.tasks for p in t.produces}
        for t in self.tasks:
            for c in t.consumes:
                if c not in produced:
                    problems.append(
                        f"task {t.id!r} consumes channel {c!r} that no task produces"
                    )

        # ids unique across their collections
        for label, ids in (
            ("step", [s.id for s in self.steps]),
            ("task", [t.id for t in self.tasks]),
            ("artifact", [a.id for a in self.artifacts]),
            ("export_target", [e.id for e in self.export_targets]),
        ):
            if len(ids) != len(set(ids)):
                problems.append(f"duplicate {label} id(s)")
        return problems
