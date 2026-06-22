import React from "react";
import { Composition } from "remotion";
import { Captions } from "./Captions";
import { Card } from "./Card";
import type { CaptionProps, CardProps } from "./types";

// Fallback props so the studio opens without a --props file. The real render is
// driven by the props JSON from video_pipeline.captions.export; calculateMetadata
// re-reads dimensions/fps/total-duration from whatever props are supplied.
const defaultProps: CaptionProps = {
  schemaVersion: 3,
  source: "preview",
  identity: "dyson-hope",
  profile: "reels-9x16",
  fps: 30,
  karaoke: true,
  dimensions: { width: 1080, height: 1920 },
  safeBox: { x: 70, y: 1100, width: 800, height: 360 },
  style: {
    font_family: "Helvetica",
    font_size: 96,
    font_weight: 800,
    fill_color: "#FFFFFF",
    stroke_color: "#000000",
    stroke_width: 8,
    emphasis_color: "#9C97F4",
    uppercase: true,
    position: "lower-third",
    h_offset: "clear-notch",
    bg_enabled: false,
    bg_color: "#000000",
    bg_radius: 0,
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
      wordTimings: [
        { from: 0, durationInFrames: 8 },
        { from: 8, durationInFrames: 8 },
        { from: 16, durationInFrames: 8 },
      ],
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
      wordTimings: [
        { from: 0, durationInFrames: 7 },
        { from: 7, durationInFrames: 7 },
        { from: 14, durationInFrames: 6 },
        { from: 20, durationInFrames: 10 },
      ],
    },
  ],
};

// Fallback props so the Card composition opens in the studio without a --props
// file. The real render is driven by the JSON from
// video_pipeline.overlay.card.props.card_to_remotion_props.
const defaultCardProps: CardProps = {
  schemaVersion: 1,
  kind: "card",
  identity: "dyson-hope",
  profile: "reels-9x16",
  fps: 30,
  // a bottom-half tile (1080x960) is the common card placement
  dimensions: { width: 1080, height: 960 },
  style: {
    bg_color: "#101014",
    text_color: "#FFFFFF",
    accent_color: "#9C97F4",
    heading_size: 64,
    body_size: 40,
    footer_size: 28,
    corner_radius: 24,
    padding: 56,
    font_family: "Helvetica",
  },
  content: {
    heading: "Markets rally on the surprise rate cut",
    body: "Stocks jumped after the central bank trimmed rates, citing cooling inflation.",
    footer: "By A. Reporter",
    image: null,
    citation: "example.com",
  },
};

export const RemotionRoot: React.FC = () => {
  return (
    <>
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
      <Composition
        id="Card"
        component={Card}
        durationInFrames={1}
        fps={30}
        width={1080}
        height={960}
        defaultProps={defaultCardProps}
        calculateMetadata={({ props }) => {
          // The card is static; a single frame is enough — the ffmpeg overlay
          // primitive holds it across its window. Size to the placement rect.
          return {
            durationInFrames: 1,
            fps: props.fps,
            width: props.dimensions.width,
            height: props.dimensions.height,
          };
        }}
      />
    </>
  );
};
