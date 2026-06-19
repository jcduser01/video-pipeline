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

    track = make_captions(
        args.input,
        caption_out=args.output,
        identity=args.identity,
        profile=args.profile,
        config_root=args.config_root,
        transcript_json=args.transcript,
        transcriber=transcriber,
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
    props = build_props_from_safezone(track, style, spec, fps=args.fps)

    props_path = args.props or str(_P(args.output).with_suffix(".props.json"))
    write_remotion_props(props, props_path)
    cmd = render_overlay(props_path, args.output, dry_run=args.dry_run)
    if args.dry_run:
        print("remotion command (dry run):")
        print("  " + " ".join(cmd))
    else:
        print(f"wrote {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="video-pipeline", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

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
    cr.add_argument("--config-root", default=str(_DEFAULT_CONFIG_ROOT))
    cr.add_argument("--dry-run", action="store_true",
                    help="print the Remotion command without rendering")
    cr.set_defaults(func=_cmd_captions_render)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
