// Font registry (INI-088 Phase 4) — make allowlisted brand fonts actually render.
//
// Remotion registers no fonts on its own, so a `font_family` of "Archivo" (etc.)
// previously fell back to the Helvetica chain. We register the loadable brand
// fonts via @fontsource — small, per-family packages (deliberately NOT the
// monolithic @remotion/google-fonts, which unpacks thousands of files). Each CSS
// import installs @font-face under the family's REAL name, so the existing
// `style.font_family` resolves to that typeface with no name remapping. System
// fonts (Helvetica/Arial/…) resolve by name against the render host.
//
// To add a loadable font: install its @fontsource package, import the weights
// here, and add the name to the Python FONT_ALLOWLIST
// (video_pipeline.captions.style.FONT_ALLOWLIST). Keep the two in sync.

import "@fontsource/archivo/400.css";
import "@fontsource/archivo/700.css";
import "@fontsource/archivo/800.css";
import "@fontsource/inter/400.css";
import "@fontsource/inter/700.css";
import "@fontsource/inter/800.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/700.css";
