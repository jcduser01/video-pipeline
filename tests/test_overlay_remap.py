"""Overlay cut-time remap (INI-089) — overlays ride the same source→cut mapping
as caption cues, so the editor handoff opens them at the right cut offsets."""

import os
import tempfile
import unittest

from video_pipeline.overlay.decision import OverlayItem, OverlayList
from video_pipeline.roughcut.decision import DecisionList, Segment
from video_pipeline.fcpxml.timeline import kept_spans, remap_overlay, remap_overlays
from video_pipeline.fcpxml.runner import assemble_project


def _decision(segments, trim_filler=True):
    segs = [Segment(index=i, start=s, end=e, keep=k, text=t)
            for i, (s, e, k, t) in enumerate(segments)]
    return DecisionList(source="reel.mp4", segments=segs, trim_filler=trim_filler)


def _item(**kw):
    base = dict(index=0, kind="image", src="a.png", start=0.0, end=1.0)
    base.update(kw)
    return OverlayItem(**base)


class RemapOverlayTests(unittest.TestCase):
    def setUp(self):
        # keep [0,2), drop [2,4), keep [4,6)  → cut timeline is 0..4
        self.decision = _decision([
            (0.0, 2.0, True, "intro"),
            (2.0, 4.0, False, "dead"),
            (4.0, 6.0, True, "outro"),
        ])
        self.spans = kept_spans(self.decision, 30)

    def test_overlay_in_kept_region_shifts(self):
        # an overlay at source [4.5, 5.5) sits in the second kept span; on the cut
        # that span starts at 2.0, so 4.5→2.5
        item = _item(start=4.5, end=5.5)
        out = remap_overlay(self.spans, item, 30)
        self.assertIsNotNone(out)
        self.assertEqual((out.start, out.end), (2.5, 3.5))

    def test_overlay_in_dropped_region_is_none(self):
        self.assertIsNone(remap_overlay(self.spans, _item(start=2.2, end=3.8), 30))

    def test_overlay_straddling_cut_is_clipped(self):
        # [1.0, 5.0): the dropped [2,4) is removed; clips to kept content
        out = remap_overlay(self.spans, _item(start=1.0, end=5.0), 30)
        self.assertIsNotNone(out)
        self.assertEqual(out.start, 1.0)        # 1.0 in first kept span → 1.0
        self.assertEqual(out.end, 3.0)          # 5.0 → cut 3.0

    def test_fade_shrinks_to_fit_clipped_window(self):
        # source window [1.5, 4.5) with a 0.8s fade; clipped cut window is short
        # enough that the fade must shrink below half
        item = _item(start=1.5, end=4.5, transition="fade", fade=0.8)
        out = remap_overlay(self.spans, item, 30)
        self.assertIsNotNone(out)
        self.assertLessEqual(out.fade * 2, out.duration + 1e-9)

    def test_identity_when_whole_clip_keep(self):
        decision = _decision([(0.0, 6.0, True, "all")], trim_filler=False)
        spans = kept_spans(decision, 30)
        item = _item(start=2.0, end=4.0)
        out = remap_overlay(spans, item, 30)
        self.assertEqual((out.start, out.end), (2.0, 4.0))


class RemapOverlaysTests(unittest.TestCase):
    def test_drops_and_reindexes(self):
        decision = _decision([
            (0.0, 2.0, True, "a"),
            (2.0, 4.0, False, "drop"),
            (4.0, 6.0, True, "b"),
        ])
        overlays = OverlayList(source="reel.mp4", profile="reels-9x16", segments=[
            _item(index=0, start=0.5, end=1.5),    # survives → cut [0.5,1.5)
            _item(index=1, start=2.5, end=3.5),    # in dropped region → gone
            _item(index=2, start=4.5, end=5.5),    # survives → cut [2.5,3.5)
        ])
        out = remap_overlays(overlays, decision, 30)
        self.assertEqual(len(out.segments), 2)
        self.assertEqual([s.index for s in out.segments], [0, 1])  # reindexed
        self.assertEqual((out.segments[0].start, out.segments[0].end), (0.5, 1.5))
        self.assertEqual((out.segments[1].start, out.segments[1].end), (2.5, 3.5))


class CutOverlayFileTests(unittest.TestCase):
    def test_assemble_project_writes_cut_overlays(self):
        with tempfile.TemporaryDirectory() as d:
            dec = _decision([
                (0.0, 2.0, True, "a"),
                (2.0, 4.0, False, "drop"),
                (4.0, 6.0, True, "b"),
            ])
            dec_path = os.path.join(d, "cut.decision.yml")
            dec.write(dec_path)

            ov = OverlayList(source="reel.mp4", profile="reels-9x16", segments=[
                _item(index=0, start=4.5, end=5.5, placement="bottom-half"),
            ])
            ov_path = os.path.join(d, "overlay.def.yml")
            ov.write(ov_path)

            out_xml = os.path.join(d, "out", "proj.fcpxml")
            result = assemble_project(
                dec_path, out_xml,
                reframed_clip=os.path.join(d, "reframed.mp4"),
                overlays_def_path=ov_path,
                fmt="fcpxml",
            )
            self.assertIsNotNone(result["cut_overlays"])
            self.assertTrue(os.path.exists(result["cut_overlays"]))
            # the written cut-time overlay is remapped (4.5→2.5)
            cut = OverlayList.read(result["cut_overlays"])
            self.assertEqual((cut.segments[0].start, cut.segments[0].end), (2.5, 3.5))

    def test_no_overlays_def_means_no_cut_overlays(self):
        with tempfile.TemporaryDirectory() as d:
            dec = _decision([(0.0, 2.0, True, "a")])
            dec_path = os.path.join(d, "cut.decision.yml")
            dec.write(dec_path)
            result = assemble_project(
                dec_path, os.path.join(d, "out", "p.fcpxml"),
                reframed_clip=os.path.join(d, "r.mp4"), fmt="fcpxml",
            )
            self.assertIsNone(result["cut_overlays"])


if __name__ == "__main__":
    unittest.main()
