"""Overlay runner (INI-089) — pure resolution + occupancy emission + dry-run argv."""

import json
import os
import tempfile
import unittest

from video_pipeline.overlay.decision import OverlayItem, OverlayList
from video_pipeline.overlay.runner import (
    render_overlays,
    resolve_placed_overlays,
    write_occupancy,
)


def _item(**kw):
    base = dict(index=0, kind="image", src="assets/a.png", start=1.0, end=4.0)
    base.update(kw)
    return OverlayItem(**base)


W, H = 1080, 1920


class ResolveTests(unittest.TestCase):
    def test_image_loops_and_uses_placement_rect(self):
        ov = OverlayList(source="x", segments=[_item(placement="bottom-half")])
        placed = resolve_placed_overlays(ov, W, H)
        self.assertEqual(len(placed), 1)
        p = placed[0]
        self.assertTrue(p.loop)                       # still image loops
        self.assertEqual((p.x, p.y, p.width, p.height), (0, 960, 1080, 960))
        self.assertEqual((p.start, p.end), (1.0, 4.0))

    def test_video_does_not_loop(self):
        ov = OverlayList(source="x", segments=[_item(kind="video", src="b.mov")])
        self.assertFalse(resolve_placed_overlays(ov, W, H)[0].loop)

    def test_card_loops_like_a_still(self):
        ov = OverlayList(source="x", segments=[_item(kind="card", src="c.mov")])
        self.assertTrue(resolve_placed_overlays(ov, W, H)[0].loop)

    def test_fade_carried_through(self):
        ov = OverlayList(source="x", segments=[
            _item(transition="fade", fade=0.3),
        ])
        self.assertEqual(resolve_placed_overlays(ov, W, H)[0].fade, 0.3)

    def test_pip_rect_geometry(self):
        ov = OverlayList(source="x", segments=[
            _item(placement="pip-rect", rect=(60, 1180, 420, 560)),
        ])
        p = resolve_placed_overlays(ov, W, H)[0]
        self.assertEqual((p.x, p.y, p.width, p.height), (60, 1180, 420, 560))

    def test_z_order_is_list_order(self):
        ov = OverlayList(source="x", segments=[
            _item(index=0, src="low.png"),
            _item(index=1, src="high.png", start=2.0, end=5.0),
        ])
        placed = resolve_placed_overlays(ov, W, H)
        self.assertEqual([p.path for p in placed], ["low.png", "high.png"])

    def test_missing_src_raises(self):
        ov = OverlayList(source="x", segments=[_item(src="")])
        with self.assertRaises(ValueError):
            resolve_placed_overlays(ov, W, H)


class OccupancyWriteTests(unittest.TestCase):
    def test_writes_parseable_descriptor(self):
        ov = OverlayList(source="x", profile="reels-9x16",
                         segments=[_item(placement="bottom-half")])
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "work", "overlay.occupancy.json")
            write_occupancy(ov, W, H, path)
            with open(path) as fh:
                data = json.load(fh)
        self.assertEqual(data["profile"], "reels-9x16")
        self.assertEqual(data["items"][0]["rect"], {"x": 0, "y": 960, "w": 1080, "h": 960})


class RenderOverlaysTests(unittest.TestCase):
    def test_dry_run_returns_argv_and_writes_occupancy(self):
        ov = OverlayList(source="x", segments=[
            _item(placement="bottom-half", transition="fade", fade=0.3),
        ])
        with tempfile.TemporaryDirectory() as d:
            occ = os.path.join(d, "occ.json")
            cmd = render_overlays(
                "work/base.mp4", ov, "review/out.mp4", W, H,
                occupancy_path=occ, dry_run=True,
            )
            # occupancy is written even on dry-run (cheap descriptor the consumers need)
            self.assertTrue(os.path.exists(occ))
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-filter_complex", cmd)
        self.assertIn("[outv]", cmd)
        self.assertEqual(cmd[-1], "review/out.mp4")
        # still image looped
        self.assertIn("-loop", cmd)

    def test_no_overlays_maps_base(self):
        ov = OverlayList(source="x", segments=[])
        cmd = render_overlays("base.mp4", ov, "o.mp4", W, H, dry_run=True)
        self.assertNotIn("-filter_complex", cmd)
        self.assertIn("0:v", cmd)


if __name__ == "__main__":
    unittest.main()
