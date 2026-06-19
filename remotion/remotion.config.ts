import { Config } from "@remotion/cli/config";

// Transparent overlay output: ProRes 4444 keeps the alpha channel so captions
// composite cleanly over the reframed video in the editor (or an FCPXML track).
Config.setVideoImageFormat("png");
Config.setPixelFormat("yuva444p10le");
Config.setCodec("prores");
Config.setProResProfile("4444");
