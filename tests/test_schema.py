"""Tests for the control-tower schema contract (INI-087, GUI Phase 1).

These cover the pipeline side of the contract only — no GUI dependency (the
one-way-dependency invariant). The GUI repo's contract test reaches the other
direction (emit -> validate against the meta-schema grammar).
"""

from __future__ import annotations

import json

import yaml

from video_pipeline import schema as S
from video_pipeline.schema.model import (
    Artifact,
    Engine,
    IOBinding,
    Param,
    Schema,
    Step,
    Task,
    UI,
)

KNOWN_CONTROLS = {"toggle", "slider", "stepper", "dropdown", "field", "picker", "rows"}


def test_schema_is_structurally_conformant():
    assert S.build_schema().validate() == []


def test_emit_yaml_and_json_round_trip_equal():
    d = S.schema_dict()
    assert yaml.safe_load(S.to_yaml(d)) == d
    assert json.loads(S.to_json(d)) == d


def test_every_param_resolves_to_a_known_control():
    sch = S.build_schema()
    for t in sch.tasks:
        for p in t.params:
            assert p.resolved_control() in KNOWN_CONTROLS, (t.id, p.key)


def test_bounded_number_defaults_to_slider_unbounded_to_stepper():
    bounded = Param("x", "number", min=0, max=10, ui=UI(label="x"))
    unbounded = Param("y", "number", ui=UI(label="y"))
    assert bounded.resolved_control() == "slider"
    assert unbounded.resolved_control() == "stepper"


def test_previewable_artifacts_have_paths_and_zorder():
    for a in S.build_schema().artifacts:
        if a.previewable:
            assert a.path, a.id
            assert a.z_order is not None, a.id


def test_every_consumed_channel_has_a_producer():
    sch = S.build_schema()
    produced = {p for t in sch.tasks for p in t.produces}
    for t in sch.tasks:
        for c in t.consumes:
            assert c in produced, f"{t.id} consumes orphan channel {c}"


def test_base_channel_has_the_expected_writer_chain():
    # project.init -> reframe -> roughcut.render all write `base` (SADD §3.2).
    sch = S.build_schema()
    assert sch.writers_of("base") == ["project.init", "reframe", "roughcut.render"]


def test_export_subcommands_are_real_cli_invocations():
    # No fabricated targets: every export subcommand starts with a real command.
    real = {"handoff", "fcpxml"}
    for e in S.build_schema().export_targets:
        assert e.subcommand.split()[0] in real, e.id


# --- argv assembly (the process contract) ---------------------------------

def test_resolve_argv_reframe_is_runnable():
    sch = S.build_schema()
    argv = S.resolve_argv(
        sch, "reframe",
        form_values={"mode": "dynamic", "tracker": "mediapipe", "profile": "reels-9x16"},
        artifact_paths={"base": "work/base.mp4"},
    )
    assert argv[0] == "video-pipeline"
    assert argv[1] == "reframe"
    # input positional present, output flag wired from the produced artifact path
    assert "work/base.mp4" in argv
    assert "-o" in argv and argv[argv.index("-o") + 1] == "work/base.mp4"
    assert "--mode" in argv and argv[argv.index("--mode") + 1] == "dynamic"


def test_resolve_argv_switch_only_emitted_when_true():
    sch = S.build_schema()
    on = S.resolve_argv(sch, "reframe", {"dry_run": True}, {"base": "b.mp4"})
    off = S.resolve_argv(sch, "reframe", {"dry_run": False}, {"base": "b.mp4"})
    assert "--dry-run" in on
    assert "--dry-run" not in off


def test_resolve_argv_orders_positionals_and_wires_io_flags():
    sch = S.build_schema()
    argv = S.resolve_argv(
        sch, "caption.render",
        form_values={"identity": "dyson-hope", "karaoke": True},
        artifact_paths={
            "caption.def": "work/captions.yml",
            "caption": "layers/captions.mov",
            "safezone.def": "work/safezone.json",
        },
    )
    # caption.def is the positional; -o carries the produced layer; --safezone the descriptor
    assert argv[2] == "work/captions.yml"
    assert argv[argv.index("-o") + 1] == "layers/captions.mov"
    assert argv[argv.index("--safezone") + 1] == "work/safezone.json"
    assert "--karaoke" in argv
    assert argv[argv.index("--identity") + 1] == "dyson-hope"


def test_resolve_argv_raises_on_missing_required():
    sch = S.build_schema()
    # project.init needs the `name` positional (required). (safezone.gen's template is
    # optional since INI-091 — only `custom` mode needs it — so it can no longer stand
    # in for the missing-required case.)
    try:
        S.resolve_argv(sch, "project.init", {}, {"base": "work/base.mp4"})
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for missing required positional")


# --- zero-hardcoding probe (the DoD item) ---------------------------------

def test_new_task_surfaces_in_emit_without_code_change():
    """Adding a step/task/artifact to the schema instance surfaces in the emitted
    document and stays conformant — the observable proof of tenet 4 (no GUI
    recompile needed to gain a step)."""
    # Uses a still-hypothetical "watermark" step (the once-hypothetical "overlay"
    # is now a real part of the schema — INI-089 — so it would collide here).
    base = S.build_schema()
    new_step = Step("watermark", "Watermark", order=45)
    new_art = Artifact("watermark", kind="layer", path="layers/watermark.mov",
                       previewable=True, z_order=20)
    occ = Artifact("watermark.occupancy", kind="descriptor",
                   path="layers/watermark.occupancy.json")
    new_task = Task(
        id="watermark.render", step="watermark", label="Render watermark",
        subcommand="watermark render",
        consumes=["base"], produces=["watermark", "watermark.occupancy"],
        io=[
            IOBinding("base", "input", "positional", order=0),
            IOBinding("watermark", "output", "flag", flag="-o"),
        ],
        params=[Param("opacity", "number", flag="--opacity", min=0.0, max=1.0,
                      step=0.05, default=1.0, ui=UI(label="Opacity"))],
    )
    extended = Schema(
        engine=base.engine,
        steps=[*base.steps, new_step],
        tasks=[*base.tasks, new_task],
        artifacts=[*base.artifacts, new_art, occ],
        export_targets=base.export_targets,
    )
    assert extended.validate() == []
    emitted = json.loads(S.to_json(extended.to_dict()))
    ids = [t["id"] for t in emitted["tasks"]]
    assert "watermark.render" in ids
    # and the new previewable layer is discoverable to the previewer
    assert any(a["id"] == "watermark" and a["previewable"] for a in emitted["artifacts"])


def test_validate_catches_orphan_consume():
    bad = Schema(
        engine=Engine("x", "0", "0", "x"),
        steps=[Step("s", "S", order=1)],
        tasks=[Task("t", "s", "T", "sub", params=[], consumes=["ghost"], produces=[])],
        artifacts=[],
        export_targets=[],
    )
    problems = bad.validate()
    assert any("ghost" in p for p in problems)
