"""Wiring tests: CLI flags + probe resolver + occupancy rescale (INI-090 integration)."""

import unittest

from video_pipeline.cli import build_parser
from video_pipeline.reframe.occupancy import rescale_windows
from video_pipeline.reframe.probe import resolve_output_dims


class TestResolveOutputDims(unittest.TestCase):
    def test_portrait_from_4k_landscape_auto(self):
        t = resolve_output_dims(3840, 2160, "full-portrait", "auto")
        self.assertEqual((t.width, t.height), (1080, 1920))

    def test_explicit_tier(self):
        t = resolve_output_dims(3840, 2160, "full-portrait", "4k")
        self.assertEqual((t.width, t.height), (2160, 3840))

    def test_square_from_4k_landscape_auto_is_4k(self):
        t = resolve_output_dims(3840, 2160, "square", "auto")
        self.assertEqual((t.width, t.height), (2160, 2160))

    def test_scale_shrinks_crop_and_can_drop_a_tier(self):
        # Zooming in shrinks the crop, so Auto may pick a lower tier than at scale 1.
        full = resolve_output_dims(3840, 2160, "full-portrait", "auto", scale=1.0)
        zoom = resolve_output_dims(3840, 2160, "full-portrait", "auto", scale=0.5)
        self.assertGreaterEqual(full.width * full.height, zoom.width * zoom.height)


class TestReframeCliFlags(unittest.TestCase):
    def setUp(self):
        self.p = build_parser()

    def test_reframe_accepts_format_and_framing_flags(self):
        ns = self.p.parse_args([
            "reframe", "in.mp4", "-o", "out.mp4",
            "--aspect", "full-portrait", "--resolution", "1080p",
            "--framing", "performer", "--occupancy-out", "occ.json",
        ])
        self.assertEqual(ns.aspect, "full-portrait")
        self.assertEqual(ns.resolution, "1080p")
        self.assertEqual(ns.framing, "performer")
        self.assertEqual(ns.occupancy_out, "occ.json")

    def test_invalid_aspect_rejected(self):
        with self.assertRaises(SystemExit):
            self.p.parse_args(["reframe", "in.mp4", "-o", "o.mp4", "--aspect", "imax"])

    def test_captions_render_accepts_subject_occupancy(self):
        ns = self.p.parse_args([
            "captions-render", "cap.yml", "-o", "o.mov", "--safezone", "s.json",
            "--subject-occupancy", "occ.json", "--position", "lower-third",
        ])
        self.assertEqual(ns.subject_occupancy, "occ.json")
        self.assertEqual(ns.position, "lower-third")


class TestRescale(unittest.TestCase):
    def test_uniform_rescale(self):
        wins = [(540, 960, 270, 480, 0.0, 1.0)]
        out = rescale_windows(wins, 1080, 1920, 720, 1280)  # 2/3 scale
        self.assertEqual(out, [(360, 640, 180, 320, 0.0, 1.0)])


if __name__ == "__main__":
    unittest.main()
