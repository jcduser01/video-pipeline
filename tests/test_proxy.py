"""Preview-proxy render — pure argv assembly (no ffmpeg needed)."""

import unittest

from video_pipeline.proxy import (
    checkerboard_filtergraph,
    ffmpeg_proxy_command,
    render_proxy,
)


class FiltergraphTests(unittest.TestCase):
    def test_has_generated_bg_geq_and_overlay(self):
        fg = checkerboard_filtergraph(1080, 1920, 30)
        self.assertIn("color=c=gray:s=1080x1920:r=30", fg)
        self.assertIn("geq=lum=", fg)
        self.assertIn("[bg][0:v]overlay=shortest=1", fg)
        self.assertIn("format=yuv420p[outv]", fg)

    def test_commas_inside_geq_are_escaped(self):
        # ffmpeg must see \, inside the expression, not a filter separator.
        fg = checkerboard_filtergraph(100, 100, 30, square=8)
        self.assertIn("\\,", fg)
        self.assertIn("floor(X/8)", fg)


class CommandTests(unittest.TestCase):
    def test_command_shape(self):
        cmd = ffmpeg_proxy_command("layers/captions.mov", "layers/captions.preview.mp4",
                                   width=1080, height=1920)
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertEqual(cmd[cmd.index("-i") + 1], "layers/captions.mov")
        self.assertIn("-filter_complex", cmd)
        self.assertIn("[outv]", cmd)
        self.assertIn("-an", cmd)              # no audio in a proxy
        self.assertIn("libx264", cmd)
        self.assertEqual(cmd[-1], "layers/captions.preview.mp4")

    def test_empty_layer_raises(self):
        with self.assertRaises(ValueError):
            ffmpeg_proxy_command("", "o.mp4", width=1080, height=1920)

    def test_dry_run_returns_argv(self):
        cmd = render_proxy("l.mov", "/nope/o.mp4", width=1080, height=1920, dry_run=True)
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertEqual(cmd[-1], "/nope/o.mp4")


if __name__ == "__main__":
    unittest.main()
