import React from "react";
import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
} from "remotion";
import type { CaptionProps, CaptionCue, CaptionStyle, SafeBox, WordTiming } from "./types";

// Even-split fallback when a cue carries no (or mismatched) per-word timings.
const wordWindows = (cue: CaptionCue): WordTiming[] => {
  if (cue.wordTimings && cue.wordTimings.length === cue.words.length) {
    return cue.wordTimings;
  }
  const n = cue.words.length || 1;
  const step = cue.durationInFrames / n;
  return cue.words.map((_, i) => ({
    from: Math.round(i * step),
    durationInFrames: Math.max(1, Math.round(step)),
  }));
};

const CueBlock: React.FC<{
  cue: CaptionCue;
  style: CaptionStyle;
  box: SafeBox;
  karaoke: boolean;
}> = ({ cue, style, box, karaoke }) => {
  const frame = useCurrentFrame(); // relative to the cue (Sequence-shifted)
  // Fade in/out, but keep a strictly-increasing input range for very short cues
  // (a 6-frame cue would otherwise give [0,3,3,6] and crash interpolate). fadeLen
  // shrinks toward 0 as the cue shortens; tiny cues just show at full opacity.
  const d = cue.durationInFrames;
  const fadeLen = Math.min(3, Math.floor((d - 1) / 2));
  const fade =
    fadeLen > 0
      ? interpolate(frame, [0, fadeLen, d - fadeLen, d], [0, 1, 1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        })
      : 1;

  const justify =
    style.position === "upper-third"
      ? "flex-start"
      : style.position === "center"
      ? "center"
      : "flex-end";

  const windows = karaoke ? wordWindows(cue) : [];

  return (
    <AbsoluteFill
      style={{
        left: box.x,
        top: box.y,
        width: box.width,
        height: box.height,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: justify,
        opacity: fade,
      }}
    >
      <div
        style={{
          // Sans-serif fallback chain: a missing named font degrades to
          // Helvetica/Arial/sans, never to the browser's serif default.
          fontFamily: `${style.font_family}, Helvetica, Arial, sans-serif`,
          fontSize: style.font_size,
          fontWeight: style.font_weight,
          color: style.fill_color,
          WebkitTextStroke: `${style.stroke_width}px ${style.stroke_color}`,
          paintOrder: "stroke fill",
          textAlign: "center",
          lineHeight: 1.05,
          textTransform: style.uppercase ? "uppercase" : "none",
          // Balance multi-line cues by line width (real font metrics) instead of
          // greedy wrapping — avoids a single word stranded on the last line.
          maxWidth: "100%",
          textWrap: "balance",
        }}
      >
        {cue.words.map((w, i) => {
          const isGlossary = cue.emphasis.includes(i);
          let color = isGlossary ? style.emphasis_color : style.fill_color;
          let opacity = 1;
          let scale = 1;

          if (karaoke) {
            const win = windows[i];
            const spoken = win ? frame >= win.from : false;
            const active = win
              ? frame >= win.from && frame < win.from + win.durationInFrames
              : false;
            opacity = spoken ? 1 : 0.5; // dim until reached
            if (active) {
              color = style.emphasis_color; // light up the spoken word
              scale = 1.08;
            }
          }

          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                color,
                opacity,
                transform: `scale(${scale})`,
                transition: "none",
              }}
            >
              {w}
              {i < cue.words.length - 1 ? " " : ""}
            </span>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

export const Captions: React.FC<CaptionProps> = (props) => {
  const { cues, style, safeBox, karaoke } = props;
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {cues.map((cue) => (
        <Sequence
          key={cue.index}
          from={cue.from}
          durationInFrames={cue.durationInFrames}
        >
          <CueBlock cue={cue} style={style} box={safeBox} karaoke={!!karaoke} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
