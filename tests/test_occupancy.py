"""Tests for subject-occupancy projection + round-trip (INI-090 Phase 2)."""

import os
import tempfile
import unittest

from video_pipeline.reframe.occupancy import (
    read_occupancy,
    subject_occupancy_windows,
    write_occupancy,
)
from video_pipeline.reframe.plan import CropPlan, CropWindow
from video_pipeline.reframe.tracker import FrameSubject


def _static_plan(x, y, w, h, out_w=1080, out_h=1920, dur=1.0):
    return CropPlan(
        src_w=1920, src_h=1080, out_w=out_w, out_h=out_h, mode="static",
        windows=[CropWindow(t_start=0.0, t_end=dur, x=x, y=y, w=w, h=h)],
    )


class TestProjection(unittest.TestCase):
    def test_known_projection(self):
        # crop 540x960 at (270,60) -> out 1080x1920 => uniform 2x scale.
        plan = _static_plan(270, 60, 540, 960)
        subj = [FrameSubject(t=0.0, cx=750, cy=460, bbox=(640, 300, 860, 620), confidence=1.0)]
        wins = subject_occupancy_windows(plan, subj)
        self.assertEqual(len(wins), 1)
        x, y, w, h, s, e = wins[0]
        # px0=(640-270)*2=740 ; px1=(860-270)*2=1180 -> clamp 1080 ; w=340
        # py0=(300-60)*2=480  ; py1=(620-60)*2=1120          ; h=640
        self.assertEqual((x, y, w, h), (740, 480, 340, 640))
        self.assertEqual((s, e), (0.0, 1.0))

    def test_subject_outside_crop_yields_no_window(self):
        plan = _static_plan(0, 0, 200, 360)
        subj = [FrameSubject(t=0.0, cx=1500, cy=500, bbox=(1400, 400, 1600, 700), confidence=1.0)]
        self.assertEqual(subject_occupancy_windows(plan, subj), [])

    def test_missing_bbox_is_ignored(self):
        plan = _static_plan(270, 60, 540, 960)
        subj = [FrameSubject(t=0.0, cx=750, cy=460, confidence=1.0)]  # no bbox
        self.assertEqual(subject_occupancy_windows(plan, subj), [])

    def test_pad_inflates_box(self):
        plan = _static_plan(270, 60, 540, 960)
        subj = [FrameSubject(t=0.0, cx=750, cy=460, bbox=(640, 300, 860, 620), confidence=1.0)]
        base = subject_occupancy_windows(plan, subj)[0]
        padded = subject_occupancy_windows(plan, subj, pad_frac=0.1)[0]
        self.assertGreater(padded[2], base[2])  # wider
        self.assertGreater(padded[3], base[3])  # taller

    def test_dynamic_plan_emits_per_segment(self):
        plan = CropPlan(
            src_w=1920, src_h=1080, out_w=1080, out_h=1920, mode="dynamic",
            windows=[
                CropWindow(0.0, 1.0, x=100, y=0, w=540, h=960),
                CropWindow(1.0, 2.0, x=600, y=0, w=540, h=960),
            ],
        )
        subj = [
            FrameSubject(t=0.5, cx=370, cy=400, bbox=(300, 300, 440, 600), confidence=1.0),
            FrameSubject(t=1.5, cx=870, cy=400, bbox=(800, 300, 940, 600), confidence=1.0),
        ]
        wins = subject_occupancy_windows(plan, subj)
        self.assertEqual(len(wins), 2)
        self.assertEqual((wins[0][4], wins[0][5]), (0.0, 1.0))
        self.assertEqual((wins[1][4], wins[1][5]), (1.0, 2.0))


class TestRoundTrip(unittest.TestCase):
    def test_write_then_read(self):
        plan = _static_plan(270, 60, 540, 960)
        subj = [FrameSubject(t=0.0, cx=750, cy=460, bbox=(640, 300, 860, 620), confidence=1.0)]
        wins = subject_occupancy_windows(plan, subj)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "reframe.occupancy.json")
            write_occupancy(p, wins)
            self.assertEqual(read_occupancy(p), wins)


if __name__ == "__main__":
    unittest.main()
