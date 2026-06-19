// Mirrors the props contract emitted by
// video_pipeline.captions.export.track_to_remotion_props (schemaVersion 1).

export type CaptionStyle = {
  font_family: string;
  font_size: number;
  font_weight: number;
  fill_color: string;
  stroke_color: string;
  stroke_width: number;
  emphasis_color: string;
  uppercase: boolean;
  position: "upper-third" | "center" | "lower-third";
  max_words: number;
  min_words: number;
  max_chars: number;
  max_gap_s: number;
  emphasize_glossary_terms: boolean;
};

export type CaptionCue = {
  index: number;
  text: string;
  words: string[];
  emphasis: number[];
  from: number;
  durationInFrames: number;
  startSeconds: number;
  endSeconds: number;
};

export type SafeBox = { x: number; y: number; width: number; height: number };

export type CaptionProps = {
  schemaVersion: number;
  source: string;
  identity: string | null;
  profile: string | null;
  fps: number;
  dimensions: { width: number; height: number };
  safeBox: SafeBox;
  style: CaptionStyle;
  cues: CaptionCue[];
};
