import React from "react";
import { Composition } from "remotion";
import { Captions } from "./Captions";
import type { CaptionProps } from "./types";

// Fallback props so the studio opens without a --props file. The real render is
// driven by the props JSON from video_pipeline.captions.export; calculateMetadata
// re-reads dimensions/fps/total-duration from whatever props are supplied.
const defaultProps: CaptionProps = {
  schemaVersion: 1,
  source: "preview",
  identity: "dyson-hope",
  profile: "reels-9x16",
  fps: 30,
  dimensions: { width: 1080, height: 1920 },
  safeBox: { x: 70, y: 1100, width: 800, height: 360 },
  style: {
    font_family: "Archivo",
    font_size: 96,
    font_weight: 800,
    fill_color: "#FFFFFF",
    stroke_color: "#000000",
    stroke_width: 8,
    emphasis_color: "#9C97F4",
    uppercase: true,
    position: "lower-third",
    max_words: 4,
    min_words: 2,
    max_chars: 24,
    max_gap_s: 0.6,
    emphasize_glossary_terms: true,
  },
  cues: [
    {
      index: 0,
      text: "I USED TO",
      words: ["I", "USED", "TO"],
      emphasis: [],
      from: 0,
      durationInFrames: 24,
      startSeconds: 0.0,
      endSeconds: 0.8,
    },
    {
      index: 1,
      text: "MAKE FUN OF SIGIL.ZERO",
      words: ["MAKE", "FUN", "OF", "SIGIL.ZERO"],
      emphasis: [3],
      from: 24,
      durationInFrames: 30,
      startSeconds: 0.8,
      endSeconds: 1.8,
    },
  ],
};

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Captions"
      component={Captions}
      durationInFrames={300}
      fps={30}
      width={1080}
      height={1920}
      defaultProps={defaultProps}
      calculateMetadata={({ props }) => {
        const last = props.cues[props.cues.length - 1];
        const total = last ? last.from + last.durationInFrames : 300;
        return {
          durationInFrames: Math.max(1, total),
          fps: props.fps,
          width: props.dimensions.width,
          height: props.dimensions.height,
        };
      }}
    />
  );
};
