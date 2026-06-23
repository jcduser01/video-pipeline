"""video-pipeline command-line entry point.

Subcommands:
  safezone-gen <template.png> --profile NAME -o spec.json
      Derive a safe-zone spec from a template PNG.

  project-init "<YYYY-MM-DD Token Project - Hook>" --identity ID --profile NAME
      Scaffold a project's source/work/review/out/render layout + project.yml.

  reframe <input.mp4> -o <out.mp4> [--profile reels-9x16] [--mode static|dynamic]
      Run the landscape->portrait reframe probe (daily driver: needs MediaPipe).

  roughcut <input.mp4> -o <decision.yml> [--transcript whisper.json] [--render cut.mp4]
      Propose a rough cut -> editable decision file (daily driver: needs mlx-whisper
      unless --transcript is supplied). --no-trim-filler preserves audio continuity.

  roughcut-render <decision.yml> -i <input.mp4> -o <cut.mp4>
      Re-render the rough cut from a (possibly hand-edited) decision file.

  captions <input.mp4> -o <captions.yml> --identity ID [--transcript whisper.json]
      Transcribe (daily driver: mlx-whisper unless --transcript) -> glossary-
      corrected 2-4-word cues -> editable caption file. --srt / --props also emit
      a portable SRT and the Remotion style-layer props (the latter needs a
      safe-zone spec via --safezone). --render also renders the styled overlay.

  captions-render <captions.yml> -o <overlay.mov> --identity ID --safezone spec.json
      Rebuild props from a (possibly hand-edited) caption file + style/safe-zone
      config and render the styled caption overlay via Remotion (daily driver).

  qc <input.mp4> --safezone spec.json [--props props.json] [--project DIR]
      Safe-zone QC: flag captions/logos/CTAs intruding on the danger polygon
      (notch included) and captions over the speaker's face; write a QC report
      and a danger-zone preview (daily driver: face detection + FFmpeg burn-in).

  handoff <decision.yml> -o <out.xml> --reframed <clip.mp4> [--captions cap.yml]
      Assemble the editor project: the decision file's KEEP segments laid over
      the reframed clip on a Base Cut track, plus the caption overlay on a
      Captions track. Default format is Premiere-compatible FCP7 XML (Premiere Pro
      does not import FCPXML); --format fcpxml targets Resolve / Final Cut. Also
      writes a cut-time caption file to render the aligned overlay (captions-render).

  fcpxml <decision.yml> -o <out.fcpxml> --reframed <clip.mp4> [--captions cap.yml]
      Back-compat alias of `handoff --format fcpxml` (always emits FCPXML).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROFILE_DIMS = {
    "reels-9x16": (1080, 1920),
    "story-9x16": (1080, 1920),
    "feed-portrait-4x5": (1080, 1350),
    "feed-square-1x1": (1080, 1080),
    "feed-landscape-16x9": (1920, 1080),
}

# Repo config/ dir (holds glossary/ + caption-styles/ + safezone/). cli.py is at
# src/video_pipeline/cli.py -> parents[2] = repo root.
_DEFAULT_CONFIG_ROOT = Path(__file__).resolve().parents[2] / "config"

# Standard project layout (mirrors the schema artifact paths). Lets `export
# --project <root>` resolve every input + the bundle output by convention.
# `reframed` is the persistent reframed-uncut clip (reframe --reframed-out), so the
# handoff has it after roughcut.render rewrites base.mp4 with the cut.
_PROJECT_LAYOUT = {
    "decision": "work/roughcut.decision.yml",
    "reframed": "work/reframed.mp4",
    "captions": "work/captions.yml",
    "overlay": "layers/captions.mov",
    "composite": "review/composite.mp4",
    "base": "work/base.mp4",
}


def _resolve_project_paths(args: argparse.Namespace, keys, *, output_name: str) -> None:
    """Fill any unset path args from ``args.project`` using the standard layout.

    Only fills attributes left as ``None`` (explicit flags win). ``output_name`` is
    the bundle-relative output (e.g. ``exports/premiere/reel.xml``).
    """
    root = getattr(args, "project", None)
    if not root:
        return
    base = Path(root)
    for k in keys:
        if getattr(args, k, None) is None:
            setattr(args, k, str(base / _PROJECT_LAYOUT[k]))
    if getattr(args, "output", None) is None:
        args.output = str(base / output_name)


# Per-run caption-style override flags shared by `captions` and `captions-render`
# (INI-088). One helper feeds both so the two style-resolution paths cannot
# diverge — each command builds its overrides the same way, then both flow through
# load_caption_style(..., overrides=...). The values are coerced/capped
# authoritatively in CaptionStyle.__post_init__; these flags are just the surface.
# value-style flags (None default = fall through to config); bg_enabled is a
# separate switch handled below.
_CAPTION_STYLE_FLAG_DESTS = (
    "font_family", "font_size", "fill_color", "stroke_color", "stroke_width",
    "bg_color", "bg_radius", "h_offset",
)


def _add_caption_style_flags(parser: argparse.ArgumentParser) -> None:
    """Add the per-run caption-style override flags (font / size / colors / stroke
    + the background-plate trio). Value flags default to None so an omitted flag
    falls through to the identity/global config (terminal users keep the config
    defaults); the GUI passes explicit values from the schema."""
    from .captions.style import FONT_ALLOWLIST

    parser.add_argument(
        "--font-family", default=None, metavar="NAME",
        help="caption font family (allowlist: " + ", ".join(FONT_ALLOWLIST) + ")",
    )
    parser.add_argument("--font-size", type=int, default=None, metavar="PX",
                        help="caption font size in px at the profile's native height")
    parser.add_argument("--fill-color", default=None, metavar="HEX",
                        help="caption text fill color (hex, e.g. #FFFFFF)")
    parser.add_argument("--stroke-color", default=None, metavar="HEX",
                        help="caption text border/stroke color (hex, e.g. #000000)")
    parser.add_argument("--stroke-width", type=int, default=None, metavar="PX",
                        help="caption text border/stroke thickness in px (0 = none)")
    # background plate (INI-088 Phase 2)
    parser.add_argument("--bg", dest="bg_enabled", action="store_true",
                        help="draw a rounded background plate behind the caption block")
    parser.add_argument("--bg-color", default=None, metavar="HEX",
                        help="background plate color (hex; with --bg)")
    parser.add_argument("--bg-radius", type=int, default=None, metavar="PX",
                        help="background plate corner radius in px (with --bg; 0 = square)")
    # horizontal placement (INI-088 Phase 3)
    parser.add_argument("--h-offset", default=None, choices=["clear-notch", "center"],
                        help="horizontal placement: clear-notch (widest notch-free span) "
                             "or center (frame-centered, symmetric)")


def _style_overrides_from_args(args: argparse.Namespace) -> dict:
    """Collect the per-run style overrides the user actually set (others fall
    through to config). Shared by both caption commands."""
    out = {}
    for dest in _CAPTION_STYLE_FLAG_DESTS:
        val = getattr(args, dest, None)
        if val is not None:
            out[dest] = val
    # bg_enabled is a presence switch (store_true): only override when set on.
    if getattr(args, "bg_enabled", False):
        out["bg_enabled"] = True
    return out


def _resolve_safezone_mode(args: argparse.Namespace) -> str:
    """Resolve the effective safe-zone mode (INI-091).

    ``--mode`` is honored when set. When it is left unset (terminal back-compat for
    callers that just pass a template positional), the mode is inferred from the
    presence of a template: a template present ⇒ ``custom`` (the legacy PNG path);
    absent ⇒ ``generic`` (the locked default). This keeps every pre-INI-091 caller
    — ``safezone-gen template.png ...`` — behaving exactly as before.
    """
    from .safezone import MODE_CUSTOM, MODE_GENERIC

    mode = getattr(args, "mode", None)
    if mode:
        return mode
    return MODE_CUSTOM if getattr(args, "template", None) else MODE_GENERIC


def _safezone_aspect(args: argparse.Namespace, profile: str | None) -> str:
    """The aspect the generic/none zone is built for (INI-091).

    Precedence: explicit ``--aspect`` → the project Target's aspect (``--project``)
    → the default aspect. The mode-driven (none/generic) write path needs a real
    aspect to build a per-aspect inset rectangle and to resolve it to pixels.
    """
    from .target_format import DEFAULT_ASPECT

    aspect = getattr(args, "aspect", None)
    if aspect:
        return aspect
    if getattr(args, "project", None):
        from .manifest import load_manifest

        return load_manifest(args.project).target.aspect
    return DEFAULT_ASPECT


def _cmd_safezone_gen(args: argparse.Namespace) -> int:
    from .safezone import MODE_CUSTOM, generate_spec

    # INI-091: when --project is given, the safe zone keys off the SAME project-level
    # target as the reframe (one target drives both). The spec's profile label is the
    # target's derived profile slug, unless --profile is set explicitly.
    profile = args.profile
    if getattr(args, "project", None) and profile is None:
        from .manifest import _ASPECT_TO_PROFILE, load_manifest

        tgt = load_manifest(args.project).target
        profile = _ASPECT_TO_PROFILE.get(tgt.aspect, "reels-9x16")

    mode = _resolve_safezone_mode(args)

    # custom — the legacy PNG → polygon path (a template is required + a PNG is read).
    if mode == MODE_CUSTOM:
        if not getattr(args, "template", None):
            print("error: safe-zone mode 'custom' needs a template PNG positional",
                  file=sys.stderr)
            return 2
        spec = generate_spec(args.template, profile=profile, key=args.key)
        out = args.output or f"{spec.profile}.safezone.json"
        Path(out).write_text(spec.to_json(), encoding="utf-8")
        notch = "with notch" if spec.has_notch else "no notch"
        print(
            f"wrote {out}  mode=custom  profile={spec.profile}  "
            f"safe={spec.safe_fraction:.1%}  {notch}  "
            f"polygon={len(spec.polygon)} verts"
        )
        return 0

    # none / generic — resolution-independent normalized zone (no PNG, no Pillow).
    # Built per-aspect and resolved to the aspect's labeled-default pixel size so the
    # written spec is a drop-in for the legacy pixel API (caption placement + QC).
    from .safezone import build_safe_zone
    from .target_format import default_target

    aspect = _safezone_aspect(args, profile)
    zone = build_safe_zone(mode, aspect)
    tgt = default_target(aspect)
    spec = zone.resolve(tgt.width, tgt.height, profile=profile or aspect)
    out = args.output or f"{spec.profile}.safezone.json"
    Path(out).write_text(spec.to_json(), encoding="utf-8")
    notch = "with notch" if spec.has_notch else "no notch"
    print(
        f"wrote {out}  mode={mode}  aspect={aspect}  profile={spec.profile}  "
        f"safe={spec.safe_fraction:.1%}  {notch}  "
        f"polygon={len(spec.polygon)} verts"
    )
    return 0


def _cmd_project_init(args: argparse.Namespace) -> int:
    from .project import create_project

    paths = create_project(
        args.root,
        args.folder_name,
        identity=args.identity,
        profile=args.profile,
        trim_filler=not args.no_trim_filler,
        source_video=args.source,
        # Idempotent: re-running refreshes the project rather than erroring. The
        # GUI confirms an overwrite before re-running; terminal users re-run knowingly.
        exist_ok=True,
    )
    print(f"created project: {paths.root}")
    return 0


def _cmd_reframe(args: argparse.Namespace) -> int:
    import shutil

    from .reframe.framing import framing_intent
    from .reframe.probe import reframe

    # Framing intent -> crop scale / vertical anchor / paired caption anchor.
    # Explicit --scale / --subject-y override the intent.
    scale, subject_y_frac, caption_position = 1.0, None, None
    if getattr(args, "framing", None):
        fi = framing_intent(args.framing)
        scale, subject_y_frac, caption_position = (
            fi.subject_scale, fi.subject_y_frac, fi.caption_position)
    if getattr(args, "scale", None) is not None:
        scale = args.scale
    if getattr(args, "subject_y", None) is not None:
        # Bipolar override: -1 = subject at top, 0 = centred, +1 = bottom.
        subject_y_frac = max(0.0, min(1.0, (args.subject_y + 1.0) / 2.0))

    # Target (INI-091): the project-level Target is the source of truth. When
    # --project is given we read it from project.yml; explicit --aspect/--resolution
    # still override (per-run nudge). Falls back to the legacy --profile dims when no
    # aspect is in play at all.
    aspect = getattr(args, "aspect", None)
    resolution = getattr(args, "resolution", "auto")
    project_root = getattr(args, "project", None)
    if project_root and aspect is None:
        from .manifest import load_manifest

        tgt = load_manifest(project_root).target
        aspect = tgt.aspect
        # --resolution defaults to "auto"; only let the project's tier through when
        # the user didn't pass an explicit non-auto tier.
        if resolution == "auto":
            resolution = tgt.resolution
    out_w, out_h = _PROFILE_DIMS.get(args.profile, (1080, 1920))

    cmd = reframe(
        args.input, args.output,
        out_w=out_w, out_h=out_h, mode=args.mode,
        tracker_name=args.tracker, dry_run=args.dry_run,
        aspect=aspect, resolution=resolution,
        scale=scale, subject_y_frac=subject_y_frac,
        occupancy_out=getattr(args, "occupancy_out", None),
        caption_position=caption_position,
        # INI-091 Phase 5: composition lock + set-box pan anchor. Default lock="none"
        # / pan unset keeps the legacy crop identical.
        lock=getattr(args, "lock", "none"),
        pan_x=getattr(args, "pan_x", None),
        pan_y=getattr(args, "pan_y", None),
    )
    if args.dry_run:
        print("ffmpeg command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}")
        if getattr(args, "occupancy_out", None):
            print(f"wrote {args.occupancy_out}  (subject occupancy -> caption dodge)")
        # Persist the reframed-but-uncut clip as a stable artifact so the editor
        # handoff still has it after roughcut.render rewrites base.mp4 with the cut.
        if getattr(args, "reframed_out", None):
            Path(args.reframed_out).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.output, args.reframed_out)
            print(f"wrote {args.reframed_out}  (reframed-uncut handoff source)")
    return 0


def _cmd_roughcut(args: argparse.Namespace) -> int:
    from .roughcut.propose import ProposeConfig
    from .roughcut.runner import make_rough_cut

    cfg = ProposeConfig(
        trim_filler=not args.no_trim_filler,
        silence_gap_s=args.silence_gap,
        keep_pad_lead_s=args.pad_lead,
        keep_pad_tail_s=args.pad_tail,
        detect_false_starts=not args.no_false_starts,
    )

    # Build the transcriber with its knobs (unless a precomputed transcript is given).
    transcriber = None
    if not args.transcript:
        if args.transcriber == "silence":
            from .roughcut.transcript import SilenceTranscriber
            transcriber = SilenceTranscriber(
                noise_db=args.noise_db, min_silence_s=args.min_silence
            )
        else:  # mlx-whisper (offline by default; --online allows the one-time download)
            from .roughcut.transcript import MLXWhisperTranscriber
            transcriber = MLXWhisperTranscriber(
                model=args.model, offline=not args.online
            )

    decision = make_rough_cut(
        args.input,
        decision_out=args.output,
        render_out=args.render,
        transcript_json=args.transcript,
        transcriber=transcriber,
        config=cfg,
        profile=args.profile,
        dry_run=args.dry_run,
    )
    kept = decision.kept()
    print(
        f"wrote {args.output}  segments={len(decision.segments)}  "
        f"kept={len(kept)}  kept_duration={decision.kept_duration():.2f}s "
        f"of {decision.source_duration():.2f}s  trim_filler={decision.trim_filler}"
    )
    if args.render:
        action = "would render (dry run)" if args.dry_run else "rendered"
        print(f"{action} rough cut -> {args.render}")
    return 0


def _cmd_roughcut_render(args: argparse.Namespace) -> int:
    from .roughcut.runner import render_from_decision

    cmd = render_from_decision(
        args.decision, args.input, args.output, dry_run=args.dry_run
    )
    if args.dry_run:
        print("ffmpeg command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}")
    return 0


def _cmd_captions(args: argparse.Namespace) -> int:
    from .captions.runner import make_captions

    transcriber = None
    if not args.transcript:
        # Captions need real words; the silence fallback has none. mlx-whisper only.
        from .roughcut.transcript import MLXWhisperTranscriber
        transcriber = MLXWhisperTranscriber(model=args.model, offline=not args.online)

    # Per-run style overrides (highest precedence over identity/global config).
    # The five visual knobs come from the shared helper (same path as
    # captions-render); the timing/karaoke knobs are define-time only.
    overrides = _style_overrides_from_args(args)
    if args.min_words is not None:
        overrides["min_words"] = args.min_words
    if args.max_words is not None:
        overrides["max_words"] = args.max_words
    if args.target_words is not None:
        overrides["target_words"] = args.target_words
    if args.karaoke:
        overrides["karaoke"] = True

    track = make_captions(
        args.input,
        caption_out=args.output,
        identity=args.identity,
        profile=args.profile,
        config_root=args.config_root,
        transcript_json=args.transcript,
        transcriber=transcriber,
        style_overrides=overrides or None,
        srt_out=args.srt,
        props_out=args.props,
        safezone_spec_path=args.safezone,
        fps=args.fps,
    )
    kept = track.kept()
    print(
        f"wrote {args.output}  cues={len(track.cues)}  kept={len(kept)}  "
        f"identity={args.identity}  profile={args.profile}"
    )
    if args.srt:
        print(f"wrote SRT -> {args.srt}")
    if args.props:
        print(f"wrote Remotion props -> {args.props}")
    if args.render:
        from .captions.remotion import render_overlay
        cmd = render_overlay(args.props, args.render, dry_run=args.dry_run)
        action = "would render (dry run)" if args.dry_run else "rendered"
        print(f"{action} overlay -> {args.render}")
        if args.dry_run:
            print("  " + " ".join(cmd))
    return 0


def _cmd_captions_render(args: argparse.Namespace) -> int:
    from pathlib import Path as _P

    from .captions.cue import CaptionTrack
    from .captions.export import build_props_from_safezone, write_remotion_props
    from .captions.remotion import render_overlay
    from .captions.style import load_caption_style
    from .safezone.spec import SafeZoneSpec

    track = CaptionTrack.read(args.decision).reindex()
    identity = args.identity or track.identity
    if not identity:
        print("error: --identity required (caption file has no identity)", file=sys.stderr)
        return 2
    # Per-run style overrides — the same seam as `captions`, so a hand-edited
    # caption file re-rendered here honors the same --font-*/-color/-size flags.
    overrides = _style_overrides_from_args(args)
    style = load_caption_style(args.config_root, identity, overrides=overrides or None)
    spec = SafeZoneSpec.from_json(_P(args.safezone).read_text(encoding="utf-8"))
    # karaoke is on if the style/config, the caption file header, or --karaoke says so.
    karaoke = style.karaoke or track.karaoke or args.karaoke

    # Subject occupancy (INI-090 Phase 2): captions dodge the subject the same way
    # they dodge overlays. Rescaled onto the safe-zone spec's frame; the framing
    # intent's paired caption anchor is honored unless --position overrides it.
    avoid_windows = None
    position = getattr(args, "position", None)
    occ = getattr(args, "subject_occupancy", None)
    if occ and _P(occ).exists():
        # Tolerant: a path may be wired by the GUI even when reframe was skipped and
        # wrote no occupancy — render normally rather than fail.
        from .reframe.occupancy import read_occupancy
        import json as _json
        avoid_windows = read_occupancy(occ, to_w=spec.image_width, to_h=spec.image_height)
        if position is None:
            hint = _json.loads(_P(occ).read_text(encoding="utf-8")).get("caption_position")
            position = hint

    props = build_props_from_safezone(
        track, style, spec, fps=args.fps, karaoke=karaoke,
        position=position, avoid_windows=avoid_windows,
    )

    props_path = args.props or str(_P(args.output).with_suffix(".props.json"))
    write_remotion_props(props, props_path)
    cmd = render_overlay(props_path, args.output, dry_run=args.dry_run)
    if args.dry_run:
        print("remotion command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}")

    # Verification seam (INI-088): grab representative stills off the rendered
    # overlay, composited over a neutral plate, for Claude to read back.
    if args.preview_frames:
        from .captions.preview import (
            DEFAULT_PREVIEW_BG,
            extract_preview_frames,
            preview_frame_times,
        )

        times = preview_frame_times(props, args.preview_frames)
        out_dir = args.preview_dir or str(_P(args.output).with_suffix("")) + "-frames"
        dims = props["dimensions"]
        results = extract_preview_frames(
            args.output, times, out_dir,
            width=dims["width"], height=dims["height"],
            background=args.preview_bg or DEFAULT_PREVIEW_BG, dry_run=args.dry_run,
        )
        verb = "would write" if args.dry_run else "wrote"
        print(f"{verb} {len(results)} preview frame(s) -> {out_dir}")
        for png, fcmd in results:
            print(f"  {verb} {png}")
            if args.dry_run:
                print("    " + " ".join(fcmd))
    return 0


def _cmd_qc(args: argparse.Namespace) -> int:
    from .qc.runner import run_qc

    # Pull thresholds + static brand-mark elements from project.yml if given.
    extra_elements = []
    knobs = dict(
        occlusion_frac=args.occlusion_frac,
        face_danger_frac=args.face_danger_frac,
        intrusion_frac=args.intrusion_frac,
        check_caption_over_face=not args.no_face_check,
        check_face_in_danger=not args.no_face_check,
    )
    if args.project:
        from .manifest import load_manifest

        cfg = load_manifest(args.project).qc_config()
        extra_elements = cfg["elements"]
        # project.yml values are the baseline; explicit CLI flags override below.
        for k in ("occlusion_frac", "face_danger_frac", "intrusion_frac"):
            if getattr(args, k) is None:
                knobs[k] = cfg[k]
    # Fall back to validator defaults for any threshold still unset.
    knobs["occlusion_frac"] = knobs["occlusion_frac"] if knobs["occlusion_frac"] is not None else 0.1
    knobs["face_danger_frac"] = knobs["face_danger_frac"] if knobs["face_danger_frac"] is not None else 0.2
    knobs["intrusion_frac"] = knobs["intrusion_frac"] if knobs["intrusion_frac"] is not None else 0.0

    report = run_qc(
        args.input,
        args.safezone,
        props_path=args.props,
        extra_elements=extra_elements,
        report_out=args.report,
        preview_out=args.preview,
        clean_out=args.clean,
        detect_faces=not args.no_face_check,
        tracker_name=args.tracker,
        dry_run=args.dry_run,
        **knobs,
    )
    print(report.to_text(), end="")
    if args.report:
        print(f"wrote QC report -> {args.report}")
    if args.preview:
        action = "would render (dry run)" if args.dry_run else "rendered"
        print(f"{action} danger-zone preview -> {args.preview}")
    if args.clean:
        action = "would render (dry run)" if args.dry_run else "rendered"
        print(f"{action} clean render -> {args.clean}")
    if args.strict and not report.passed:
        return 1
    return 0


def _cmd_composite(args: argparse.Namespace) -> int:
    from .composite.runner import render_composite

    overlays = list(args.layer or [])
    cmd = render_composite(
        args.base, overlays, args.output,
        crf=args.crf, preset=args.preset, dry_run=args.dry_run,
    )
    if args.dry_run:
        print("composite command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}  layers={1 + len(overlays)}")
    return 0


def _cmd_overlay_define(args: argparse.Namespace) -> int:
    from .overlay.decision import OverlayItem, OverlayList

    transcript = None
    if getattr(args, "transcript", None):
        import json as _json
        from .roughcut.transcript import transcript_from_whisper_dict

        transcript = transcript_from_whisper_dict(
            _json.loads(Path(args.transcript).read_text(encoding="utf-8"))
        )

    items = []
    for i, spec in enumerate(args.add or []):
        d = {}
        for kv in spec.split(";"):
            kv = kv.strip()
            if not kv:
                continue
            k, _, v = kv.partition("=")
            d[k.strip()] = v.strip()
        start, end = d.get("start"), d.get("end")
        if (start is None or end is None) and d.get("at") and transcript is not None:
            from .overlay.propose import propose_window

            pad = float(d.get("pad", "0") or 0)
            win = propose_window(transcript, d["at"], pad_lead=pad, pad_tail=pad)
            if win:
                start, end = win
        if start is None or end is None:
            print(f"error: overlay #{i} needs start=/end= (or at= with --transcript)",
                  file=sys.stderr)
            return 2
        rect = tuple(int(x) for x in d["rect"].split(",")) if d.get("rect") else None
        items.append(OverlayItem(
            index=i, kind=d.get("kind", "image"), src=d.get("src", ""),
            start=float(start), end=float(end),
            placement=d.get("placement", "full-bleed"), rect=rect,
            transition=d.get("transition", "cut"), fade=float(d.get("fade", "0") or 0),
            audio=d.get("audio", "keep"), scale=d.get("scale", "fit"),
            matte=d.get("matte", "none"), text=d.get("text", ""),
        ))

    ov = OverlayList(source=args.source or "", segments=items, profile=args.profile).reindex()
    if args.dry_run:
        print(ov.to_yaml())
        return 0
    ov.write(args.output)
    print(f"wrote {args.output}  overlays={len(items)}")
    return 0


def _cmd_overlay_card(args: argparse.Namespace) -> int:
    import json as _json
    from .overlay.card.capture import CapturedPage, card_from_page

    if getattr(args, "from_json", None):
        page = CapturedPage(**_json.loads(Path(args.from_json).read_text(encoding="utf-8")))
    else:  # pragma: no cover - live capture is a daily-driver seam (Chrome MCP / Jina)
        print("error: live URL capture runs on the daily driver; pass --from-json "
              "<captured-page.json> to structure a card without a live fetch",
              file=sys.stderr)
        return 2
    content = card_from_page(
        page, max_body_chars=args.max_body, max_heading_chars=args.max_heading
    )
    content.write(args.output)
    print(f"wrote {args.output}  heading={content.heading[:40]!r}")
    return 0


def _cmd_overlay_render(args: argparse.Namespace) -> int:
    from .overlay.decision import OverlayList
    from .overlay.runner import render_overlays
    from .safezone.spec import SafeZoneSpec

    spec = SafeZoneSpec.from_json(Path(args.safezone).read_text(encoding="utf-8"))
    ov = OverlayList.read(args.overlays)
    cmd = render_overlays(
        args.input, ov, args.output, spec.image_width, spec.image_height,
        crf=args.crf, preset=args.preset,
        occupancy_path=args.occupancy, dry_run=args.dry_run,
    )
    if args.dry_run:
        print("overlay-render command (dry run):")
        print("  " + " ".join(cmd))
    else:
        tail = f"  occupancy={args.occupancy}" if args.occupancy else ""
        print(f"wrote {args.output}  overlays={len(ov.segments)}{tail}")
    return 0


def _cmd_handoff(args: argparse.Namespace) -> int:
    from .fcpxml.runner import assemble_project

    # The `fcpxml` alias forces FCPXML; `handoff` honors --format (premiere default).
    fmt = getattr(args, "format", None) or "fcpxml"

    # Resolve inputs + output from --project when given (explicit flags win).
    ext = ".xml" if fmt == "premiere" else ".fcpxml"
    name = args.project_name or (Path(args.project).name if args.project else "reel")
    _resolve_project_paths(
        args, ["decision", "reframed", "captions", "overlay", "composite"],
        output_name=f"exports/{fmt}/{name}{ext}",
    )
    missing = [k for k in ("decision", "reframed", "output") if getattr(args, k) is None]
    if missing:
        print(f"error: missing {', '.join(missing)} — give them explicitly or use "
              f"--project <root>", file=sys.stderr)
        return 2

    out_w, out_h = _PROFILE_DIMS.get(args.profile, (1080, 1920))
    result = assemble_project(
        args.decision,
        args.output,
        reframed_clip=args.reframed,
        caption_path=args.captions,
        overlay_path=args.overlay,
        composite_path=args.composite,
        fmt=fmt,
        width=out_w,
        height=out_h,
        fps=args.fps,
        event_name=args.event,
        project_name=args.project_name,
    )
    label = "Premiere FCP7 XML" if result["format"] == "premiere" else "FCPXML"
    print(
        f"wrote {result['project']}  [{label}]  base-cut clips={result['clips']}  "
        f"duration={result['kept_duration']:.2f}s"
    )
    if result["cut_captions"]:
        print(f"wrote cut-time caption file -> {result['cut_captions']}")
        print(
            "  render the aligned overlay:  video-pipeline captions-render "
            f"{result['cut_captions']} -o {result['overlay']} --safezone <spec.json>"
        )
    return 0


def _add_handoff_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("decision", nargs="?", default=None,
                        help="decision file path (.yml) — the base cut "
                             "(resolved from --project when omitted)")
    parser.add_argument("-o", "--output", default=None,
                        help="project output path (defaults under the project's "
                             "exports/ when --project is given)")
    parser.add_argument("--project", default=None,
                        help="project root; resolves decision/reframed/captions/overlay/"
                             "composite + the output path from the standard layout, so "
                             "the GUI can drive the export with just --project + params")
    parser.add_argument("--reframed", default=None,
                        help="the reframed vertical clip the base cut references "
                             "(work/<clip>-9x16.mp4); reframe is baked, not a transform")
    parser.add_argument("--captions", default=None,
                        help="caption file (.yml); remapped to cut-time + referenced as "
                             "the Captions overlay track")
    parser.add_argument("--overlay", default=None,
                        help="path the cut-time caption overlay (.mov) will be rendered to "
                             "and referenced from (default: alongside the project)")
    parser.add_argument("--composite", default=None,
                        help="composite render (review/composite.mp4) to include as a "
                             "disabled top-track guide clip (omit to skip the guide)")
    parser.add_argument("--profile", default="reels-9x16",
                        help="output profile -> sequence dimensions (default reels-9x16)")
    parser.add_argument("--fps", type=int, default=30, help="sequence frame rate (default 30)")
    parser.add_argument("--event", default="JasonOS", help="FCPXML event name (fcpxml only)")
    parser.add_argument("--project-name", default=None,
                        help="project/sequence name (default: the decision file's source)")


def _cmd_proxy(args: argparse.Namespace) -> int:
    from .proxy import render_proxy

    w, h = _PROFILE_DIMS.get(args.profile, (1080, 1920))
    cmd = render_proxy(
        args.layer, args.output, width=w, height=h, fps=args.fps,
        square=args.square, dry_run=args.dry_run,
    )
    if args.dry_run:
        print("proxy command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}  ({w}x{h} checkerboard preview)")
    return 0


def _cmd_export_capcut(args: argparse.Namespace) -> int:
    from .capcut import export_capcut

    # Resolve media + bundle dir from --project (explicit flags win). CapCut's
    # --captions is the rendered overlay .mov (not the caption .yml), so it maps to
    # the "overlay" layout entry.
    if args.project:
        root = Path(args.project)
        if args.base is None:
            args.base = str(root / _PROJECT_LAYOUT["base"])
        if args.captions is None:
            args.captions = str(root / _PROJECT_LAYOUT["overlay"])
        if args.composite is None:
            args.composite = str(root / _PROJECT_LAYOUT["composite"])
        if args.output is None:
            args.output = str(root / "exports/capcut")
    if args.base is None:
        print("error: missing base — give --base or --project <root>", file=sys.stderr)
        return 2

    result = export_capcut(
        args.output,
        base=args.base,
        captions=args.captions,
        composite=args.composite,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        print(f"capcut bundle (dry run) -> {result['bundle']}  layers={result['layers']}")
        for m in result["media"]:
            print(f"  would copy -> {m}")
    else:
        print(f"wrote capcut bundle -> {result['bundle']}  layers={result['layers']}")
        print(f"  README -> {result['readme']}")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    """Emit the control-tower schema (the GUI's single source of truth, INI-087).

    The GUI discovers every step/flag/layer/export target from this document at
    runtime; nothing about the pipeline is hardcoded in the GUI.
    """
    from . import schema as _schema

    problems = _schema.check()
    if args.check:
        if problems:
            for prob in problems:
                print(f"schema: {prob}", file=sys.stderr)
            print(f"schema: {len(problems)} problem(s)", file=sys.stderr)
            return 1
        print("schema: OK")
        return 0
    if problems:
        # Never emit a non-conformant schema — fail loudly (deny-by-default).
        for prob in problems:
            print(f"schema: {prob}", file=sys.stderr)
        return 1

    text = _schema.to_json() if args.format == "json" else _schema.to_yaml()
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"schema -> {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="video-pipeline", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    sc = sub.add_parser("schema",
                        help="emit the control-tower GUI schema (SSOT, INI-087)")
    sc.add_argument("--format", default="yaml", choices=["yaml", "json"],
                    help="yaml (authored SSOT, default) or json (Rust-boundary form)")
    sc.add_argument("-o", "--output", default=None, help="write here instead of stdout")
    sc.add_argument("--check", action="store_true",
                    help="validate the schema and exit (no emit)")
    sc.set_defaults(func=_cmd_schema)

    g = sub.add_parser("safezone-gen",
                       help="derive a safe-zone spec (none | generic | custom from a PNG)")
    # INI-091: the template positional is OPTIONAL now — only `custom` mode reads a
    # PNG. `none`/`generic` build a resolution-independent per-aspect zone with no
    # template. When --mode is unset the mode is inferred (template ⇒ custom, else
    # generic) so every legacy `safezone-gen template.png` invocation is unchanged.
    g.add_argument("template", nargs="?", default=None,
                   help="template PNG with the danger region marked (custom mode only)")
    from .safezone import SAFE_ZONE_MODES as _SZ_MODES
    g.add_argument("--mode", default=None, choices=sorted(_SZ_MODES),
                   help="safe-zone mode: none (full frame) | generic (per-aspect "
                        "conservative insets, the default) | custom (trace a template "
                        "PNG). Default inferred: a template ⇒ custom, else generic.")
    from .target_format import ASPECT_PRESETS as _SZ_AP
    g.add_argument("--aspect", default=None, choices=sorted(_SZ_AP),
                   help="aspect the none/generic zone is built for (INI-091); "
                        "defaults to the --project target's aspect, else full-portrait")
    g.add_argument("--project", default=None,
                   help="project root (or project.yml): derive the spec profile + the "
                        "generic/none aspect from the project-level target (INI-091); "
                        "--profile / --aspect override")
    g.add_argument("--profile", default=None)
    g.add_argument("--key", default="auto", choices=["auto", "alpha", "color"])
    g.add_argument("-o", "--output", default=None)
    g.set_defaults(func=_cmd_safezone_gen)

    i = sub.add_parser("project-init", help="scaffold a new project folder")
    i.add_argument("folder_name")
    i.add_argument("--identity", required=True)
    i.add_argument("--profile", required=True)
    i.add_argument("--root", default=str(Path.home() / "Video" / "Projects"))
    i.add_argument("--source", default=None,
                   help="source video to ingest: archived in source/ and seeded as "
                        "the base channel (work/base.mp4) so downstream steps have input")
    i.add_argument("--no-trim-filler", action="store_true",
                   help="disable speech/filler trimming (e.g. live-off-the-mixer DJ sets)")
    i.set_defaults(func=_cmd_project_init)

    r = sub.add_parser("reframe", help="reframe the source to the target format")
    r.add_argument("input")
    r.add_argument("-o", "--output", required=True)
    r.add_argument("--reframed-out", default=None,
                   help="also copy the reframed-uncut clip here (work/reframed.mp4) "
                        "as the stable editor-handoff source")
    r.add_argument("--project", default=None,
                   help="project root (or project.yml): reads the project-level "
                        "target (aspect + resolution; INI-091) so the reframe and "
                        "safezone share one target; explicit --aspect/--resolution win")
    r.add_argument("--profile", default="reels-9x16",
                   help="legacy fixed-dimension profile (used when --aspect is omitted)")
    from .target_format import ASPECT_PRESETS as _AP, TIERS as _TIERS
    from .reframe.framing import FRAMING_INTENTS as _FI
    r.add_argument("--aspect", default=None, choices=sorted(_AP),
                   help="target aspect preset (INI-090); overrides --profile dims")
    r.add_argument("--resolution", default="auto", choices=("auto", *_TIERS),
                   help="resolution tier or 'auto' (highest non-upscaling tier)")
    r.add_argument("--framing", default=None, choices=sorted(_FI),
                   help="composition intent: talking-head | performer | wide-context")
    r.add_argument("--scale", type=float, default=None,
                   help="punch-in override (1.0=widest native full frame; >1 punches in)")
    r.add_argument("--subject-y", type=float, default=None, dest="subject_y",
                   help="vertical anchor override, bipolar (-1=top, 0=centre, +1=bottom)")
    # INI-091 Phase 5: set-box pan anchor + composition lock. Defaults keep the
    # legacy auto-tracked crop (lock=none, pan unset) byte-identical.
    r.add_argument("--pan-x", type=float, default=None, dest="pan_x",
                   help="set-box horizontal anchor 0..1 (0=left, 1=right); the "
                        "relative crop placement the lock holds. Unset = auto-track")
    r.add_argument("--pan-y", type=float, default=None, dest="pan_y",
                   help="set-box vertical anchor 0..1 (0=top, 1=bottom). Unset = auto-track")
    r.add_argument("--lock", default="none", choices=["none", "x", "y", "both"],
                   help="composition lock: hold the set-box on the x / y / both axes "
                        "instead of following the subject (default none = follow)")
    r.add_argument("--occupancy-out", default=None, dest="occupancy_out",
                   help="write subject occupancy here for the caption layer to dodge")
    r.add_argument("--mode", default="static", choices=["static", "dynamic"])
    r.add_argument("--tracker", default="opencv", choices=["opencv", "mediapipe"],
                   help="subject tracker: opencv (default, bundled, no download) "
                        "or mediapipe (Tasks API; downloads a model on first use)")
    r.add_argument("--dry-run", action="store_true",
                   help="print the FFmpeg command without tracking/rendering")
    r.set_defaults(func=_cmd_reframe)

    rc = sub.add_parser("roughcut", help="propose a rough cut -> editable decision file")
    rc.add_argument("input")
    rc.add_argument("-o", "--output", required=True, help="decision file path (.yml)")
    rc.add_argument("--transcript", default=None,
                    help="precomputed Whisper-shaped JSON; skips transcription")
    rc.add_argument("--transcriber", default="mlx-whisper",
                    choices=["mlx-whisper", "silence"],
                    help="mlx-whisper (default; needs the [roughcut] extra) or "
                         "silence (ASR-free FFmpeg silencedetect — trims dead air "
                         "only, runs anywhere with ffmpeg). Ignored if --transcript is set.")
    rc.add_argument("--render", default=None,
                    help="also render the rough cut to this path")
    rc.add_argument("--profile", default=None)
    rc.add_argument("--no-trim-filler", action="store_true",
                    help="preserve audio continuity: no speech-based edits "
                         "(e.g. live-off-the-mixer DJ record showcases)")
    rc.add_argument("--no-false-starts", action="store_true",
                    help="disable false-start (immediate-repeat) detection")
    rc.add_argument("--silence-gap", type=float, default=0.6,
                    help="inter-word gap (s) above which dead air is trimmed (default 0.6)")
    rc.add_argument("--pad-lead", type=float, default=0.06,
                    help="padding (s) kept BEFORE speech at each cut (default 0.06)")
    rc.add_argument("--pad-tail", type=float, default=0.15,
                    help="padding (s) kept AFTER speech at each cut (default 0.15; "
                         "larger because Whisper clips word ends early)")
    # mlx-whisper-only knobs:
    rc.add_argument("--model", default=None,
                    help="[mlx-whisper] HF model repo (default "
                         "mlx-community/whisper-large-v3-turbo)")
    rc.add_argument("--online", action="store_true",
                    help="[mlx-whisper] allow network: download the model if it is "
                         "not cached (default is OFFLINE/cache-only). huggingface_hub "
                         "auto-uses the ambient HF_TOKEN env var if set.")
    # silence-transcriber-only knobs:
    rc.add_argument("--noise-db", type=float, default=-30.0,
                    help="[--transcriber silence] silence threshold in dB (default -30)")
    rc.add_argument("--min-silence", type=float, default=0.6,
                    help="[--transcriber silence] min silence duration (s) to detect "
                         "(default 0.6)")
    rc.add_argument("--dry-run", action="store_true",
                    help="write the decision file but do not run the render")
    rc.set_defaults(func=_cmd_roughcut)

    rr = sub.add_parser("roughcut-render",
                        help="re-render a rough cut from a decision file")
    rr.add_argument("decision", help="decision file path (.yml)")
    rr.add_argument("-i", "--input", required=True, help="source clip")
    rr.add_argument("-o", "--output", required=True, help="rough-cut output path")
    rr.add_argument("--dry-run", action="store_true",
                    help="print the FFmpeg command without rendering")
    rr.set_defaults(func=_cmd_roughcut_render)

    c = sub.add_parser("captions",
                       help="transcript -> glossary-corrected cues -> editable caption file")
    c.add_argument("input")
    c.add_argument("-o", "--output", required=True, help="caption file path (.yml)")
    c.add_argument("--identity", required=True,
                   help="glossary + caption-style identity layer (e.g. dyson-hope)")
    c.add_argument("--profile", default="reels-9x16")
    c.add_argument("--transcript", default=None,
                   help="precomputed Whisper-shaped JSON; skips transcription "
                        "(reuses the rough-cut phase's cached work/ transcript)")
    c.add_argument("--srt", default=None, help="also write a portable SRT here")
    c.add_argument("--props", default=None,
                   help="also write Remotion style-layer props JSON here "
                        "(requires --safezone)")
    c.add_argument("--safezone", default=None,
                   help="safe-zone spec JSON (for --props caption-box placement)")
    c.add_argument("--render", default=None,
                   help="also render the styled overlay here via Remotion "
                        "(daily driver; requires --props)")
    c.add_argument("--fps", type=int, default=30, help="frame rate for props (default 30)")
    # words-per-cue range overrides (default: the identity/global caption-style config)
    c.add_argument("--min-words", type=int, default=None,
                   help="min words per cue (1/1 with --max-words = single-word captions)")
    c.add_argument("--max-words", type=int, default=None,
                   help="max words per cue (e.g. 4 for phrase groups)")
    c.add_argument("--target-words", type=int, default=None,
                   help="words-per-cue the chunker aims for (0 = auto midpoint)")
    c.add_argument("--karaoke", action="store_true",
                   help="karaoke active-word highlight (each word lights up as spoken)")
    # per-run caption-style overrides (font / size / colors / stroke)
    _add_caption_style_flags(c)
    c.add_argument("--config-root", default=str(_DEFAULT_CONFIG_ROOT),
                   help="repo config/ dir (glossary + caption-styles)")
    c.add_argument("--model", default=None,
                   help="[mlx-whisper] HF model repo (default whisper-large-v3-turbo)")
    c.add_argument("--online", action="store_true",
                   help="[mlx-whisper] allow the one-time model download (default offline)")
    c.add_argument("--dry-run", action="store_true",
                   help="with --render, print the Remotion command without running it")
    c.set_defaults(func=_cmd_captions)

    cr = sub.add_parser("captions-render",
                        help="render a styled caption overlay from a caption file (Remotion)")
    cr.add_argument("decision", help="caption file path (.yml)")
    cr.add_argument("-o", "--output", required=True, help="overlay output path (.mov)")
    cr.add_argument("--identity", default=None,
                    help="caption-style identity (default: from the caption file)")
    cr.add_argument("--safezone", required=True, help="safe-zone spec JSON")
    cr.add_argument("--subject-occupancy", default=None, dest="subject_occupancy",
                    help="subject occupancy JSON from reframe --occupancy-out; "
                         "captions dodge the subject (INI-090 Phase 2)")
    cr.add_argument("--position", default=None,
                    choices=["upper-third", "center", "lower-third"],
                    help="caption anchor; overrides the style + any framing-intent hint")
    cr.add_argument("--props", default=None,
                    help="props JSON path to write (default: alongside output)")
    cr.add_argument("--fps", type=int, default=30)
    cr.add_argument("--karaoke", action="store_true",
                    help="force the karaoke active-word highlight on (also honored "
                         "from the caption file / style config)")
    # per-run caption-style overrides (font / size / colors / stroke)
    _add_caption_style_flags(cr)
    # verification seam (INI-088): render then grab representative stills.
    cr.add_argument("--preview-frames", type=int, default=0, metavar="N",
                    help="after rendering, grab N representative still PNGs "
                         "(composited over a neutral plate) for visual verification")
    cr.add_argument("--preview-dir", default=None,
                    help="directory for the preview frames (default: <output>-frames)")
    cr.add_argument("--preview-bg", default=None,
                    help="background color the transparent overlay is grabbed over "
                         "(hex; default neutral mid-gray)")
    cr.add_argument("--config-root", default=str(_DEFAULT_CONFIG_ROOT))
    cr.add_argument("--dry-run", action="store_true",
                    help="print the Remotion command without rendering")
    cr.set_defaults(func=_cmd_captions_render)

    q = sub.add_parser("qc", help="safe-zone QC: report + danger-zone preview + clean render")
    q.add_argument("input", help="the rendered/composited clip to check (profile frame)")
    q.add_argument("--safezone", required=True, help="safe-zone spec JSON")
    q.add_argument("--props", default=None,
                   help="Remotion caption props JSON (checks each cue's box vs the safe zone)")
    q.add_argument("--project", default=None,
                   help="project.yml (or its dir): pulls qc: thresholds + static elements")
    q.add_argument("--report", default=None, help="write the QC report JSON here")
    q.add_argument("--preview", default=None,
                   help="write the danger-zone preview (overlay burned in) here")
    q.add_argument("--clean", default=None,
                   help="write the clean render (stream-copied deliverable) here")
    q.add_argument("--tracker", default="opencv", choices=["opencv", "mediapipe"],
                   help="face detector for the subject-aware checks (default opencv)")
    q.add_argument("--no-face-check", action="store_true",
                   help="skip face detection (geometry-only: danger intrusion)")
    q.add_argument("--occlusion-frac", type=float, default=None,
                   help="caption-over-face overlap threshold (default 0.10)")
    q.add_argument("--face-danger-frac", type=float, default=None,
                   help="face-in-danger threshold (default 0.20)")
    q.add_argument("--intrusion-frac", type=float, default=None,
                   help="danger-intrusion tolerance for protected elements (default 0)")
    q.add_argument("--strict", action="store_true",
                   help="exit non-zero if QC does not pass (for automation gates)")
    q.add_argument("--dry-run", action="store_true",
                   help="compute + write the report but do not run FFmpeg renders")
    q.set_defaults(func=_cmd_qc)

    # composite — flatten the base + overlay layers into a preview render.
    co = sub.add_parser(
        "composite",
        help="flatten base + overlay layers into review/composite.mp4 (preview)",
    )
    co.add_argument("base", help="base video (work/base.mp4)")
    co.add_argument("-o", "--output", required=True,
                    help="composite output path (review/composite.mp4)")
    co.add_argument("--layer", action="append", default=[],
                    help="an overlay layer to stack over the base; repeatable in "
                         "low->high z-order (e.g. the caption .mov)")
    co.add_argument("--crf", type=int, default=18,
                    help="x264 quality, lower = better (default 18)")
    co.add_argument("--preset", default="medium", help="x264 preset (default medium)")
    co.add_argument("--dry-run", action="store_true",
                    help="print the ffmpeg command without rendering")
    co.set_defaults(func=_cmd_composite)

    # overlay — author the editable overlay decision file (overlay.def).
    ov = sub.add_parser(
        "overlay", help="author the editable overlay decision file (overlay.def)")
    ov.add_argument("-o", "--output", required=True, help="overlay.def output path")
    ov.add_argument("--source", default=None,
                    help="base clip name recorded in the file (advisory)")
    ov.add_argument("--profile", default=None, help="output profile recorded in the file")
    ov.add_argument("--transcript", default=None,
                    help="word-level transcript JSON; lets an overlay's window be "
                         "proposed from the spoken span (at=...)")
    ov.add_argument("--add", action="append", default=[],
                    help="an overlay spec, e.g. "
                         "'kind=image;src=a.png;start=3.2;end=7.8;placement=bottom-half' "
                         "(or at=\"the chart\" with --transcript); repeatable, low->high z")
    ov.add_argument("--dry-run", action="store_true",
                    help="print the overlay file without writing it")
    ov.set_defaults(func=_cmd_overlay_define)

    # overlay-card — capture a URL into editable card content.
    ocd = sub.add_parser(
        "overlay-card", help="capture a URL into editable source-card content (JSON)")
    ocd.add_argument("url", help="article / page URL to capture")
    ocd.add_argument("-o", "--output", required=True, help="card.content output path")
    ocd.add_argument("--max-body", type=int, default=280, dest="max_body",
                     help="body character budget (default 280)")
    ocd.add_argument("--max-heading", type=int, default=120, dest="max_heading",
                     help="heading character budget (default 120)")
    ocd.add_argument("--from-json", default=None, dest="from_json",
                     help="a captured-page JSON (CapturedPage fields) — structure a card "
                          "without a live fetch (the live fetch runs on the daily driver)")
    ocd.set_defaults(func=_cmd_overlay_card)

    # overlay-render — composite the placed/timed overlays + emit occupancy.
    orr = sub.add_parser(
        "overlay-render",
        help="composite the placed/timed overlays from overlay.def + emit occupancy")
    orr.add_argument("overlays", help="overlay decision file (overlay.def .yml)")
    orr.add_argument("-i", "--input", required=True, help="base video (work/base.mp4)")
    orr.add_argument("-o", "--output", required=True,
                     help="overlay composite output (review/overlay-composite.mp4)")
    orr.add_argument("--safezone", required=True,
                     help="safe-zone spec JSON (supplies the frame dimensions)")
    orr.add_argument("--occupancy", default=None,
                     help="overlay.occupancy descriptor output path (captions/QC consume it)")
    orr.add_argument("--crf", type=int, default=18,
                     help="x264 quality, lower = better (default 18)")
    orr.add_argument("--preset", default="medium", help="x264 preset (default medium)")
    orr.add_argument("--dry-run", action="store_true",
                     help="print the ffmpeg command without rendering")
    orr.set_defaults(func=_cmd_overlay_render)

    # proxy — bake a transparent layer over a checkerboard into h264 for preview.
    px = sub.add_parser(
        "proxy",
        help="bake a transparent layer over a checkerboard into an h264 preview proxy",
    )
    px.add_argument("layer", help="the transparent layer (.mov) to preview")
    px.add_argument("-o", "--output", required=True,
                    help="proxy output path (layers/<name>.preview.mp4)")
    px.add_argument("--profile", default="reels-9x16",
                    help="output profile -> proxy dimensions (default reels-9x16)")
    px.add_argument("--fps", type=int, default=30, help="frame rate (default 30)")
    px.add_argument("--square", type=int, default=16,
                    help="checkerboard cell size in px (default 16)")
    px.add_argument("--dry-run", action="store_true",
                    help="print the ffmpeg command without rendering")
    px.set_defaults(func=_cmd_proxy)

    # handoff — the editor project. Default format is Premiere-compatible FCP7
    # XML (Premiere does not import FCPXML); --format fcpxml targets Resolve / FCP.
    h = sub.add_parser(
        "handoff",
        help="assemble the editor project (base cut + captions); "
             "Premiere FCP7 XML by default, --format fcpxml for Resolve/Final Cut",
    )
    _add_handoff_args(h)
    h.add_argument("--format", default="premiere", choices=["premiere", "fcpxml"],
                   help="premiere = FCP7/XMEML XML (default, opens in Premiere Pro); "
                        "fcpxml = FCPXML 1.10 (Resolve / Final Cut)")
    h.set_defaults(func=_cmd_handoff)

    # fcpxml — back-compat alias; always emits FCPXML (honors its name).
    fx = sub.add_parser("fcpxml", help="assemble the FCPXML handoff (Resolve / Final Cut)")
    _add_handoff_args(fx)
    fx.set_defaults(func=_cmd_handoff)

    # export <target> — the unified packaging command the GUI drives (SADD §3.5).
    # premiere/fcpxml reuse the handoff assembler; capcut writes an arranged-media
    # folder. `handoff`/`fcpxml` above stay as back-compat aliases.
    ex = sub.add_parser("export", help="package the project for an editor "
                                       "(premiere | fcpxml | capcut)")
    ex_sub = ex.add_subparsers(dest="target", required=True)

    exp = ex_sub.add_parser("premiere",
                            help="FCP7 XML (XMEML) — opens in Premiere Pro")
    _add_handoff_args(exp)
    exp.set_defaults(func=_cmd_handoff, format="premiere")

    exf = ex_sub.add_parser("fcpxml",
                            help="FCPXML 1.10 — DaVinci Resolve / Final Cut Pro")
    _add_handoff_args(exf)
    exf.set_defaults(func=_cmd_handoff, format="fcpxml")

    exc = ex_sub.add_parser("capcut",
                            help="arranged-media folder (CapCut imports no project)")
    exc.add_argument("-o", "--output", default=None,
                     help="bundle directory (default <project>/exports/capcut)")
    exc.add_argument("--project", default=None,
                     help="project root; resolves base/captions/composite + the "
                          "bundle dir from the standard layout")
    exc.add_argument("--base", default=None,
                     help="the rendered base cut (work/base.mp4)")
    exc.add_argument("--captions", default=None,
                     help="the caption overlay layer (.mov)")
    exc.add_argument("--composite", default=None,
                     help="the composite render (review/composite.mp4)")
    exc.add_argument("--dry-run", action="store_true",
                     help="show the copy plan without writing")
    exc.set_defaults(func=_cmd_export_capcut)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
