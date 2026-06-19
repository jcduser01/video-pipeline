import React from "react";
import {
  AbsoluteFill,
  Sequence,
  useCurrentFrame,
  interpolate,
} from "remotion";
import type { CaptionProps, CaptionCue, CaptionStyle, SafeBox } from "./types";

// A single caption, positioned inside the safe-zone box. Emphasis words take the
// style's emphasis colour; a short fade keeps cue transitions from snapping.
const CueBlock: React.FC<{
  cue: CaptionCue;
  style: CaptionStyle;
  box: SafeBox;
}> = ({ cue, style, box }) => {
  const frame = useCurrentFrame();
  const fade = interpolate(
    frame,
    [0, 3, cue.durationInFrames - 3, cue.durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  const justify =
    style.position === "upper-third"
      ? "flex-start"
      : style.position === "center"
      ? "center"
      : "flex-end";

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
          fontFamily: style.font_family,
          fontSize: style.font_size,
          fontWeight: style.font_weight,
          color: style.fill_color,
          WebkitTextStroke: `${style.stroke_width}px ${style.stroke_color}`,
          paintOrder: "stroke fill",
          textAlign: "center",
          lineHeight: 1.05,
          textTransform: style.uppercase ? "uppercase" : "none",
        }}
      >
        {cue.words.map((w, i) => (
          <span
            key={i}
            style={{
              color: cue.emphasis.includes(i)
                ? style.emphasis_color
                : style.fill_color,
            }}
          >
            {w}
            {i < cue.words.length - 1 ? " " : ""}
          </span>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const Captions: React.FC<CaptionProps> = (props) => {
  const { cues, style, safeBox } = props;
  return (
    <AbsoluteFill style={{ backgroundColor: "transparent" }}>
      {cues.map((cue) => (
        <Sequence
          key={cue.index}
          from={cue.from}
          durationInFrames={cue.durationInFrames}
        >
          <CueBlock cue={cue} style={style} box={safeBox} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
