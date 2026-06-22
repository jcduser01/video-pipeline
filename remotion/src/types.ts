// Mirrors the props contract emitted by
// video_pipeline.captions.export.track_to_remotion_props (schemaVersion 2).
// v2 (INI-088 Phase 2) adds the background-plate trio (bg_enabled/bg_color/
// bg_radius); they are optional so a v1 props file still type-checks.

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
  // horizontal placement (v2; consumed Python-side to derive safeBox, not by the
  // renderer — present for parity/debuggability)
  h_offset?: "clear-notch" | "center";
  // background plate (v2)
  bg_enabled?: boolean;
  bg_color?: string;
  bg_radius?: number;
  max_words: number;
  min_words: number;
  max_chars: number;
  max_gap_s: number;
  emphasize_glossary_terms: boolean;
};

export type WordTiming = { from: number; durationInFrames: number };

export type CaptionCue = {
  index: number;
  text: string;
  words: string[];
  emphasis: number[];
  from: number;
  durationInFrames: number;
  startSeconds: number;
  endSeconds: number;
  // per-word frame windows RELATIVE to the cue start (for the karaoke highlight)
  wordTimings?: WordTiming[];
  // v3 (INI-089 caption-dodge): a per-cue box that overrides safeBox so this cue
  // clears an overlay on screen during its window. Absent → use safeBox.
  box?: SafeBox;
};

export type SafeBox = { x: number; y: number; width: number; height: number };

export type CaptionProps = {
  schemaVersion: number;
  source: string;
  identity: string | null;
  profile: string | null;
  fps: number;
  karaoke?: boolean;
  dimensions: { width: number; height: number };
  safeBox: SafeBox;
  style: CaptionStyle;
  cues: CaptionCue[];
};

// ── Source card (INI-089 Phase B) ────────────────────────────────────────────
// Mirrors video_pipeline.overlay.card.props.card_to_remotion_props. The card is a
// static tile rendered at `dimensions` (its placement rect); the on-screen window
// and fade are applied by the ffmpeg overlay primitive, not here.

export type CardStyle = {
  bg_color: string;
  text_color: string;
  accent_color: string;
  heading_size: number;
  body_size: number;
  footer_size: number;
  corner_radius: number;
  padding: number;
  font_family: string;
};

export type CardContent = {
  heading: string;
  body: string;
  footer: string;
  image: string | null;
  citation: string;
};

export type CardProps = {
  schemaVersion: number;
  kind: "card";
  identity: string | null;
  profile: string | null;
  fps: number;
  dimensions: { width: number; height: number };
  style: CardStyle;
  content: CardContent;
};
