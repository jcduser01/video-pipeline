"""Overlay subsystem (INI-089 Phase A) — pure logic: decision round-trip,
occupancy descriptor, and the timed/placed composite argv. No ffmpeg needed."""

import unittest

from video_pipeline.overlay.decision import OverlayItem, OverlayList
from video_pipeline.overlay.occupancy import (
    OccupancyItem,
    active_at,
    build_occupancy,
    occupancy_to_dict,
    resolve_rect,
)
from video_pipeline.composite.render import (
    PlacedOverlay,
    ffmpeg_timed_composite_command,
    timed_overlay_filtergraph,
)


def _img(**kw):
    base = dict(index=0, kind="image", src="assets/a.png", start=1.0, end=4.0)
    base.update(kw)
    return OverlayItem(**base)


class OverlayItemValidationTests(unittest.TestCase):
    def test_defaults_are_full_bleed_cut(self):
        it = _img()
        self.assertEqual(it.placement, "full-bleed")
        self.assertEqual(it.transition, "cut")
        self.assertEqual(it.fade, 0.0)
        self.assertEqual(it.duration, 3.0)

    def test_bad_kind_rejected(self):
        with self.assertRaises(ValueError):
            _img(kind="gif")

    def test_bad_placement_rejected(self):
        with self.assertRaises(ValueError):
            _img(placement="middle")

    def test_zero_or_negative_window_rejected(self):
        with self.assertRaises(ValueError):
            _img(start=4.0, end=4.0)
        with self.assertRaises(ValueError):
            _img(start=5.0, end=4.0)

    def test_cut_forces_fade_zero(self):
        it = _img(transition="cut", fade=0.5)
        self.assertEqual(it.fade, 0.0)

    def test_fade_requires_positive_duration(self):
        with self.assertRaises(ValueError):
            _img(transition="fade", fade=0.0)

    def test_fade_cannot_exceed_half_window(self):
        # 3s window: in+out fades of 1.6s each would overlap.
        with self.assertRaises(ValueError):
            _img(transition="fade", fade=1.6)
        # 1.5s each exactly fills the window — allowed (2*1.5 == 3.0).
        self.assertEqual(_img(transition="fade", fade=1.5).fade, 1.5)

    def test_pip_requires_rect(self):
        with self.assertRaises(ValueError):
            _img(placement="pip-rect")

    def test_rect_only_with_pip(self):
        with self.assertRaises(ValueError):
            _img(placement="bottom-half", rect=(0, 0, 10, 10))

    def test_pip_rect_normalized_to_int_tuple(self):
        it = _img(placement="pip-rect", rect=(10.0, 20.0, 100.0, 200.0))
        self.assertEqual(it.rect, (10, 20, 100, 200))

    def test_video_audio_modes(self):
        for mode in ("keep", "duck", "mute"):
            self.assertEqual(_img(kind="video", src="a.mov", audio=mode).audio, mode)
        with self.assertRaises(ValueError):
            _img(kind="video", src="a.mov", audio="loud")


class OverlayListRoundTripTests(unittest.TestCase):
    def _sample(self) -> OverlayList:
        return OverlayList(
            source="reel.mp4",
            profile="reels-9x16",
            segments=[
                _img(index=0, placement="bottom-half", transition="fade", fade=0.3,
                     text="the chart"),
                _img(index=1, kind="video", src="b.mov", start=6.0, end=10.0,
                     placement="pip-rect", rect=(60, 1180, 420, 560), audio="duck",
                     scale="fill", text="demo"),
            ],
        )

    def test_yaml_round_trips_losslessly(self):
        original = self._sample()
        reloaded = OverlayList.from_yaml(original.to_yaml())
        self.assertEqual(reloaded.to_yaml(), original.to_yaml())
        self.assertEqual(reloaded.source, "reel.mp4")
        self.assertEqual(len(reloaded.segments), 2)
        self.assertEqual(reloaded.segments[1].rect, (60, 1180, 420, 560))
        self.assertEqual(reloaded.segments[1].audio, "duck")
        self.assertEqual(reloaded.segments[0].fade, 0.3)

    def test_header_present_and_parseable(self):
        text = self._sample().to_yaml()
        self.assertIn("# overlay file", text)
        self.assertEqual(len(OverlayList.from_yaml(text).segments), 2)

    def test_hand_edited_minimal_row_loads(self):
        # A human writes the sparest possible overlay line; defaults fill the rest.
        text = (
            "source: reel.mp4\n"
            "segments:\n"
            "  - {kind: image, src: a.png, start: 2.0, end: 5.0}\n"
        )
        ov = OverlayList.from_yaml(text)
        self.assertEqual(len(ov.segments), 1)
        self.assertEqual(ov.segments[0].placement, "full-bleed")
        self.assertEqual(ov.segments[0].transition, "cut")

    def test_reindex_orders_by_window(self):
        ov = OverlayList(
            source="x",
            segments=[
                _img(index=5, start=8.0, end=9.0),
                _img(index=2, start=1.0, end=2.0),
            ],
        )
        ov.reindex()
        self.assertEqual([s.index for s in ov.segments], [0, 1])
        self.assertEqual(ov.segments[0].start, 1.0)

    def test_active_at_window_is_half_open(self):
        ov = self._sample()  # item0 [1,4) fade, item1 [6,10)
        self.assertEqual([i.index for i in ov.active_at(2.0)], [0])
        self.assertEqual(ov.active_at(4.0), [])  # end is exclusive
        self.assertEqual([i.index for i in ov.active_at(6.0)], [1])

    def test_source_duration_from_last_window(self):
        self.assertEqual(self._sample().source_duration(), 10.0)


class OccupancyTests(unittest.TestCase):
    W, H = 1080, 1920

    def test_full_bleed_is_whole_frame(self):
        self.assertEqual(resolve_rect(_img(), self.W, self.H), (0, 0, 1080, 1920))

    def test_bottom_half_is_lower_half(self):
        self.assertEqual(
            resolve_rect(_img(placement="bottom-half"), self.W, self.H),
            (0, 960, 1080, 960),
        )

    def test_pip_rect_passthrough(self):
        it = _img(placement="pip-rect", rect=(60, 1180, 420, 560))
        self.assertEqual(resolve_rect(it, self.W, self.H), (60, 1180, 420, 560))

    def test_pip_rect_clamped_into_frame(self):
        # A rect that runs off the right/bottom edge is clamped, never off-canvas.
        it = _img(placement="pip-rect", rect=(1000, 1850, 400, 400))
        x, y, w, h = resolve_rect(it, self.W, self.H)
        self.assertEqual((x, y), (1000, 1850))
        self.assertEqual(w, 80)   # 1080 - 1000
        self.assertEqual(h, 70)   # 1920 - 1850

    def test_build_and_active_at(self):
        ov = OverlayList(
            source="x",
            segments=[
                _img(index=0, start=1.0, end=4.0, placement="bottom-half"),
                _img(index=1, start=3.0, end=8.0, placement="pip-rect",
                     rect=(60, 1180, 420, 560)),
            ],
        )
        items = build_occupancy(ov, self.W, self.H)
        self.assertEqual(len(items), 2)
        self.assertEqual([i.index for i in active_at(items, 3.5)], [0, 1])
        self.assertEqual([i.index for i in active_at(items, 1.5)], [0])
        self.assertEqual([i.index for i in active_at(items, 7.0)], [1])

    def test_intersects_rect(self):
        it = OccupancyItem(0, "image", "bottom-half", 0.0, 1.0, 0, 960, 1080, 960)
        self.assertTrue(it.intersects_rect(100, 1000, 200, 1100))   # inside lower half
        self.assertFalse(it.intersects_rect(100, 100, 200, 200))    # upper frame, clear

    def test_descriptor_shape(self):
        ov = OverlayList(source="x", profile="reels-9x16",
                         segments=[_img(placement="bottom-half")])
        d = occupancy_to_dict(
            build_occupancy(ov, self.W, self.H),
            profile="reels-9x16", image_width=self.W, image_height=self.H,
        )
        self.assertEqual(d["profile"], "reels-9x16")
        self.assertEqual(d["image"], {"width": 1080, "height": 1920})
        self.assertEqual(d["items"][0]["rect"], {"x": 0, "y": 960, "w": 1080, "h": 960})
        self.assertIn("occupancy_version", d)


class TimedFiltergraphTests(unittest.TestCase):
    def test_empty_is_empty(self):
        self.assertEqual(timed_overlay_filtergraph([]), "")

    def test_single_cut_overlay(self):
        ov = PlacedOverlay("a.png", x=0, y=960, width=1080, height=960,
                           start=1.0, end=4.0)
        fg = timed_overlay_filtergraph([ov])
        self.assertEqual(
            fg,
            "[1:v]scale=1080:960[ovs1];"
            "[0:v][ovs1]overlay=0:960:enable='between(t,1,4)':format=auto[outv]",
        )

    def test_fade_adds_alpha_fades(self):
        ov = PlacedOverlay("a.png", x=0, y=0, width=100, height=100,
                           start=2.0, end=5.0, fade=0.5)
        fg = timed_overlay_filtergraph([ov])
        self.assertIn("format=yuva420p", fg)
        self.assertIn("fade=t=in:st=2:d=0.5:alpha=1", fg)
        # out-fade starts at end - fade = 4.5
        self.assertIn("fade=t=out:st=4.5:d=0.5:alpha=1", fg)

    def test_two_overlays_chain_low_to_high(self):
        a = PlacedOverlay("a.png", 0, 0, 100, 100, 0.0, 2.0)
        b = PlacedOverlay("b.png", 10, 20, 50, 50, 1.0, 3.0)
        fg = timed_overlay_filtergraph([a, b])
        # first overlay produces [ov1], second consumes it and produces [outv]
        self.assertIn("[ov1]", fg)
        self.assertIn("[outv]", fg)
        self.assertTrue(fg.endswith("[outv]"))
        self.assertIn("overlay=10:20:enable='between(t,1,3)'", fg)


class TimedCommandTests(unittest.TestCase):
    def test_command_shape(self):
        ov = PlacedOverlay("a.png", 0, 960, 1080, 960, 1.0, 4.0, loop=True)
        cmd = ffmpeg_timed_composite_command("work/base.mp4", [ov], "review/out.mp4")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-filter_complex", cmd)
        self.assertIn("[outv]", cmd)
        self.assertIn("0:a?", cmd)
        self.assertEqual(cmd[-1], "review/out.mp4")
        # still image is looped
        self.assertIn("-loop", cmd)

    def test_video_overlay_not_looped(self):
        ov = PlacedOverlay("b.mov", 0, 0, 100, 100, 0.0, 2.0, loop=False)
        cmd = ffmpeg_timed_composite_command("base.mp4", [ov], "o.mp4")
        self.assertNotIn("-loop", cmd)
        # base + one overlay = two inputs
        self.assertEqual(cmd.count("-i"), 2)

    def test_no_overlays_maps_base_directly(self):
        cmd = ffmpeg_timed_composite_command("base.mp4", [], "o.mp4")
        self.assertNotIn("-filter_complex", cmd)
        self.assertIn("0:v", cmd)

    def test_empty_base_raises(self):
        with self.assertRaises(ValueError):
            ffmpeg_timed_composite_command("", [], "o.mp4")

    def test_crf_passthrough(self):
        ov = PlacedOverlay("a.png", 0, 0, 10, 10, 0.0, 1.0)
        cmd = ffmpeg_timed_composite_command("b.mp4", [ov], "o.mp4", crf=20, preset="slow")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "20")
        self.assertEqual(cmd[cmd.index("-preset") + 1], "slow")


if __name__ == "__main__":
    unittest.main()
