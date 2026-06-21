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


def _cmd_safezone_gen(args: argparse.Namespace) -> int:
    from .safezone import generate_spec

    spec = generate_spec(args.template, profile=args.profile, key=args.key)
    out = args.output or f"{spec.profile}.safezone.json"
    Path(out).write_text(spec.to_json(), encoding="utf-8")
    notch = "with notch" if spec.has_notch else "no notch"
    print(
        f"wrote {out}  profile={spec.profile}  "
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
    from .reframe.probe import reframe

    out_w, out_h = _PROFILE_DIMS.get(args.profile, (1080, 1920))
    cmd = reframe(
        args.input, args.output,
        out_w=out_w, out_h=out_h, mode=args.mode,
        tracker_name=args.tracker, dry_run=args.dry_run,
    )
    if args.dry_run:
        print("ffmpeg command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}")
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
    overrides = {}
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
    style = load_caption_style(args.config_root, identity)
    spec = SafeZoneSpec.from_json(_P(args.safezone).read_text(encoding="utf-8"))
    # karaoke is on if the style/config, the caption file header, or --karaoke says so.
    karaoke = style.karaoke or track.karaoke or args.karaoke
    props = build_props_from_safezone(track, style, spec, fps=args.fps, karaoke=karaoke)

    props_path = args.props or str(_P(args.output).with_suffix(".props.json"))
    write_remotion_props(props, props_path)
    cmd = render_overlay(props_path, args.output, dry_run=args.dry_run)
    if args.dry_run:
        print("remotion command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}")
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


def _cmd_handoff(args: argparse.Namespace) -> int:
    from .fcpxml.runner import assemble_project

    # The `fcpxml` alias forces FCPXML; `handoff` honors --format (premiere default).
    fmt = getattr(args, "format", None) or "fcpxml"

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
    parser.add_argument("decision", help="decision file path (.yml) — the base cut")
    parser.add_argument("-o", "--output", required=True, help="project output path")
    parser.add_argument("--reframed", required=True,
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


def _cmd_export_capcut(args: argparse.Namespace) -> int:
    from .capcut import export_capcut

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

    g = sub.add_parser("safezone-gen", help="derive a safe-zone spec from a template PNG")
    g.add_argument("template")
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

    r = sub.add_parser("reframe", help="run the landscape->portrait reframe probe")
    r.add_argument("input")
    r.add_argument("-o", "--output", required=True)
    r.add_argument("--profile", default="reels-9x16")
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
    cr.add_argument("--props", default=None,
                    help="props JSON path to write (default: alongside output)")
    cr.add_argument("--fps", type=int, default=30)
    cr.add_argument("--karaoke", action="store_true",
                    help="force the karaoke active-word highlight on (also honored "
                         "from the caption file / style config)")
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
    exc.add_argument("-o", "--output", required=True,
                     help="bundle directory (exports/capcut)")
    exc.add_argument("--base", required=True,
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
