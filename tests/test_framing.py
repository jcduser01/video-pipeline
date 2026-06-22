"""Tests for framing intents + the crop-plan scale / vertical-anchor knobs (INI-090 C)."""

import unittest

from video_pipeline.reframe.framing import (
    DEFAULT_FRAMING,
    FRAMING_INTENTS,
    framing_intent,
)
from video_pipeline.reframe.plan import build_crop_plan
from video_pipeline.reframe.tracker import FrameSubject


def _subjects(cx, cy, n=4):
    return [FrameSubject(t=i * 0.2, cx=cx, cy=cy, confidence=1.0) for i in range(n)]


class TestFramingIntents(unittest.TestCase):
    def test_three_intents_present(self):
        self.assertEqual(set(FRAMING_INTENTS), {"talking-head", "performer", "wide-context"})

    def test_accessor_and_fields(self):
        f = framing_intent("performer")
        self.assertEqual(f.subject_scale, 1.0)
        self.assertEqual(f.caption_position, "lower-third")
        self.assertIsNotNone(f.subject_y_frac)

    def test_talking_head_is_zoomed_and_high(self):
        f = framing_intent("talking-head")
        self.assertLess(f.subject_scale, 1.0)
        self.assertLess(f.subject_y_frac, 0.5)  # face above centre

    def test_default_is_performer(self):
        self.assertEqual(DEFAULT_FRAMING, "performer")

    def test_unknown_raises(self):
        with self.assertRaises(ValueError):
            framing_intent("nope")


class TestCropScaleAndAnchor(unittest.TestCase):
    SRC_W, SRC_H = 1920, 1080  # landscape source, 9:16 target

    def _win(self, **kw):
        plan = build_crop_plan(
            _subjects(cx=960, cy=400), self.SRC_W, self.SRC_H,
            out_w=9, out_h=16, mode="static", duration=1.0, **kw,
        )
        return plan.windows[0]

    def test_defaults_unchanged(self):
        # Legacy: full-height crop, centred (y == 0 since crop_h == src_h).
        w = self._win()
        self.assertEqual(w.h, self.SRC_H)
        self.assertEqual(w.y, 0)

    def test_scale_below_one_shrinks_crop(self):
        full = self._win()
        zoom = self._win(scale=0.5)
        self.assertLess(zoom.w, full.w)
        self.assertLess(zoom.h, full.h)
        # aspect preserved (~9:16) within even-rounding
        self.assertAlmostEqual(zoom.w / zoom.h, full.w / full.h, places=1)

    def test_vertical_anchor_uses_cy_when_there_is_slack(self):
        # Zoomed crop (crop_h < src_h) -> anchor bites. cy=400, frac=0.33.
        w = self._win(scale=0.5, subject_y_frac=0.33)
        crop_h = w.h
        expected = min(max(round(400 - 0.33 * crop_h), 0), self.SRC_H - crop_h)
        self.assertEqual(w.y, expected)

    def test_anchor_none_centres_the_zoomed_crop(self):
        w = self._win(scale=0.5)
        self.assertEqual(w.y, (self.SRC_H - w.h) // 2)

    def test_full_height_crop_has_no_vertical_freedom(self):
        # Even with an anchor, a full-height crop clamps to y == 0.
        w = self._win(subject_y_frac=0.1)
        self.assertEqual(w.h, self.SRC_H)
        self.assertEqual(w.y, 0)

    def test_scale_above_one_clamps_to_source(self):
        w = self._win(scale=2.0)
        self.assertLessEqual(w.w, self.SRC_W)
        self.assertLessEqual(w.h, self.SRC_H)
        self.assertEqual(w.h, self.SRC_H)  # height clamped at source

    def test_crop_stays_inside_source(self):
        w = self._win(scale=0.5, subject_y_frac=0.9)
        self.assertGreaterEqual(w.y, 0)
        self.assertLessEqual(w.y + w.h, self.SRC_H)


if __name__ == "__main__":
    unittest.main()
