"""Caption-dodge (INI-089) — captions consume overlay.occupancy and relocate to
clear an overlay during a cue's window. Pure geometry + props wiring."""

import tempfile
import unittest
from pathlib import Path

from tests._util import make_template_png

from video_pipeline.safezone import generate_spec
from video_pipeline.captions.placement import caption_box, caption_box_avoiding
from video_pipeline.captions.cue import CaptionTrack, Cue
from video_pipeline.captions.style import CaptionStyle
from video_pipeline.captions.export import build_props_from_safezone
from video_pipeline.overlay.decision import OverlayItem, OverlayList
from video_pipeline.overlay.occupancy import (
    avoid_windows,
    build_occupancy,
    rects_active_in_window,
)


def _full_frame_spec(d):
    """A spec whose safe region spans almost the whole frame (all anchors usable)."""
    png = Path(d) / "t.png"
    make_template_png(png, 1080, 1920, safe_rect=(40, 160, 1040, 1760))
    return generate_spec(str(png), profile="reels-9x16")


class CaptionBoxAvoidingTests(unittest.TestCase):
    def test_no_avoid_matches_plain_box(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _full_frame_spec(d)
            self.assertEqual(
                caption_box_avoiding(spec, [], position="lower-third"),
                caption_box(spec, position="lower-third"),
            )

    def test_bottom_half_overlay_flips_caption_up(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _full_frame_spec(d)
            bottom_half = (0, 960, 1080, 960)  # lower half busy
            box = caption_box_avoiding(spec, [bottom_half], position="lower-third")
            # the dodged box must not overlap the overlay …
            self.assertFalse(
                box.x < bottom_half[0] + bottom_half[2]
                and bottom_half[0] < box.x1
                and box.y < bottom_half[1] + bottom_half[3]
                and bottom_half[1] < box.y1
            )
            # … and it relocated upward (clear of the lower half)
            self.assertLessEqual(box.y1, 960)

    def test_full_bleed_overlay_best_effort_returns_requested(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _full_frame_spec(d)
            full = (0, 0, 1080, 1920)
            # every anchor is blocked → best effort = the requested position
            self.assertEqual(
                caption_box_avoiding(spec, [full], position="lower-third"),
                caption_box(spec, position="lower-third"),
            )


class OccupancyWindowTests(unittest.TestCase):
    def _occ(self):
        ov = OverlayList(source="x", segments=[
            OverlayItem(index=0, kind="image", src="a.png", start=2.0, end=6.0,
                        placement="bottom-half"),
        ])
        return build_occupancy(ov, 1080, 1920)

    def test_rects_active_in_window_overlaps(self):
        items = self._occ()
        self.assertEqual(rects_active_in_window(items, 3.0, 4.0), [(0, 960, 1080, 960)])
        self.assertEqual(rects_active_in_window(items, 7.0, 8.0), [])  # after the overlay
        self.assertEqual(rects_active_in_window(items, 5.5, 9.0), [(0, 960, 1080, 960)])

    def test_avoid_windows_flattens_with_times(self):
        self.assertEqual(avoid_windows(self._occ()), [(0, 960, 1080, 960, 2.0, 6.0)])


class PerCueDodgeWiringTests(unittest.TestCase):
    def _track(self):
        # two cues: one during the overlay window, one after it
        return CaptionTrack(
            source="reel.mp4",
            identity="dyson-hope",
            profile="reels-9x16",
            cues=[
                Cue(index=0, start=3.0, end=4.0, words=["over", "the", "card"]),
                Cue(index=1, start=8.0, end=9.0, words=["clear", "now"]),
            ],
        )

    def test_only_overlapping_cue_gets_a_box(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _full_frame_spec(d)
            ov = OverlayList(source="x", segments=[
                OverlayItem(index=0, kind="image", src="a.png", start=2.0, end=6.0,
                            placement="bottom-half"),
            ])
            props = build_props_from_safezone(
                self._track(), CaptionStyle(), spec,
                avoid_windows=avoid_windows(build_occupancy(ov, 1080, 1920)),
            )
            cues = {c["index"]: c for c in props["cues"]}
            self.assertEqual(props["schemaVersion"], 3)
            self.assertIn("box", cues[0])          # cue during the overlay dodges
            self.assertNotIn("box", cues[1])       # cue after the overlay keeps default
            # the dodged cue box clears the lower half
            self.assertLessEqual(cues[0]["box"]["y"] + cues[0]["box"]["height"], 960)

    def test_no_avoid_windows_emits_no_per_cue_boxes(self):
        with tempfile.TemporaryDirectory() as d:
            spec = _full_frame_spec(d)
            props = build_props_from_safezone(self._track(), CaptionStyle(), spec)
            self.assertTrue(all("box" not in c for c in props["cues"]))


if __name__ == "__main__":
    unittest.main()
