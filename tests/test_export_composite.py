"""Composite guide track in the editor handoffs (FCPXML + XMEML). Pure."""

import unittest
import xml.etree.ElementTree as ET

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline.captions.cue import CaptionTrack, Cue
from video_pipeline.roughcut.decision import DecisionList, Segment
from video_pipeline.fcpxml.document import assemble_fcpxml
from video_pipeline.fcpxml.xmeml import assemble_xmeml

FPS = 30
SEGS = [
    (0.0, 0.4, False, "silence", ""),
    (0.4, 3.0, True, "", "first kept span"),
    (3.0, 3.5, False, "filler", "um"),
    (3.5, 6.0, True, "", "second kept span"),
]


def _decision():
    segs = [Segment(index=i, start=s, end=e, keep=k, reason=r, text=t)
            for i, (s, e, k, r, t) in enumerate(SEGS)]
    return DecisionList(source="reel.mp4", segments=segs, profile="reels-9x16",
                        trim_filler=True)


def _track():
    return CaptionTrack(source="reel.mp4",
                        cues=[Cue(index=0, start=0.5, end=1.0, words=["hi"])],
                        identity="dyson-hope", profile="reels-9x16")


class FcpxmlCompositeTests(unittest.TestCase):
    def _clips(self, xml):
        return list(ET.fromstring(xml.split("\n", 2)[2]).iter("asset-clip"))

    def test_composite_is_a_disabled_top_lane_guide(self):
        xml, _ = assemble_fcpxml(
            _decision(), _track(),
            reframed_src="/v/clip.mp4", overlay_src="/v/captions.mov",
            composite_src="/v/composite.mp4", fps=FPS,
        )
        guide = [c for c in self._clips(xml) if c.get("name") == "Composite (guide)"]
        self.assertEqual(len(guide), 1)
        g = guide[0]
        self.assertEqual(g.get("enabled"), "0")          # disabled by default
        self.assertEqual(g.get("lane"), "2")             # above captions (lane 1)
        self.assertIn("composite", xml)                  # asset referenced

    def test_no_composite_when_omitted(self):
        xml, _ = assemble_fcpxml(
            _decision(), _track(),
            reframed_src="/v/clip.mp4", overlay_src="/v/captions.mov", fps=FPS,
        )
        self.assertNotIn("Composite (guide)", xml)

    def test_composite_lane_1_without_captions(self):
        xml, _ = assemble_fcpxml(
            _decision(), None,
            reframed_src="/v/clip.mp4", composite_src="/v/composite.mp4", fps=FPS,
        )
        guide = [c for c in self._clips(xml) if c.get("name") == "Composite (guide)"]
        self.assertEqual(guide[0].get("lane"), "1")


class XmemlCompositeTests(unittest.TestCase):
    def _root(self, xml):
        return ET.fromstring(xml.split("\n", 2)[2])

    def test_composite_clipitem_is_disabled(self):
        xml, _ = assemble_xmeml(
            _decision(), _track(),
            reframed_src="/v/clip.mp4", overlay_src="/v/captions.mov",
            composite_src="/v/composite.mp4", fps=FPS,
        )
        root = self._root(xml)
        items = [ci for ci in root.iter("clipitem") if ci.get("id") == "clipitem-composite"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].findtext("enabled"), "FALSE")
        self.assertEqual(items[0].findtext("name"), "Composite (guide)")

    def test_no_composite_track_when_omitted(self):
        xml, _ = assemble_xmeml(
            _decision(), _track(),
            reframed_src="/v/clip.mp4", overlay_src="/v/captions.mov", fps=FPS,
        )
        self.assertNotIn("clipitem-composite", xml)


if __name__ == "__main__":
    unittest.main()
