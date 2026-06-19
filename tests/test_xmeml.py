"""Phase-5 XMEML (Premiere FCP7 XML) tests — the default editor handoff.

All pure; no native toolchain. Reuses the synthetic two-cut decision + caption
fixtures and asserts the FCP7 XML structure (frame-integer times, V1/V2 video
tracks, stereo audio with link blocks, alpha-keyed caption overlay, pathurl).
"""

import unittest
import xml.etree.ElementTree as ET

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline.captions.cue import CaptionTrack, Cue
from video_pipeline.roughcut.decision import DecisionList, Segment
from video_pipeline.fcpxml.xmeml import assemble_xmeml

FPS = 30

TWO_CUT = [
    (0.0, 0.4, False, "silence", ""),
    (0.4, 3.0, True, "", "first kept span"),
    (3.0, 3.5, False, "filler", "um"),
    (3.5, 6.0, True, "", "second kept span"),
    (6.0, 6.4, False, "silence", ""),
]


def make_decision(segments=TWO_CUT, source="2026-06-03-reel.mp4", trim_filler=True):
    segs = [Segment(index=i, start=s, end=e, keep=k, reason=r, text=t)
            for i, (s, e, k, r, t) in enumerate(segments)]
    return DecisionList(source=source, segments=segs, profile="reels-9x16",
                        trim_filler=trim_filler)


def make_track(cues, source="2026-06-03-reel.mp4"):
    out = [Cue(index=i, start=s, end=e, words=t.split()) for i, (s, e, t) in enumerate(cues)]
    return CaptionTrack(source=source, cues=out, identity="dyson-hope", profile="reels-9x16")


def _root(xml):
    return ET.fromstring(xml.split("<!DOCTYPE xmeml>")[1])


class XmemlTests(unittest.TestCase):
    def _assemble(self, with_caps=True):
        track = make_track([(1.0, 1.8, "first words"),
                            (4.0, 4.8, "second words")]) if with_caps else None
        return assemble_xmeml(
            make_decision(), track,
            reframed_src="/Video/work/clip-9x16.mp4",
            overlay_src="/Video/out/clip.captions.mov" if with_caps else None,
            fps=FPS,
        )

    def test_doctype_and_root(self):
        xml, _ = self._assemble()
        self.assertTrue(xml.startswith('<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertIn("<!DOCTYPE xmeml>", xml)
        self.assertEqual(_root(xml).tag, "xmeml")

    def test_sequence_rate_is_integer_fps_non_ntsc(self):
        xml, _ = self._assemble()
        rate = _root(xml).find("./sequence/rate")
        self.assertEqual(rate.find("timebase").text, "30")
        self.assertEqual(rate.find("ntsc").text, "FALSE")

    def test_sequence_duration_in_frames(self):
        # 5.1s cut at 30fps = 153 frames
        xml, _ = self._assemble()
        self.assertEqual(_root(xml).find("./sequence/duration").text, "153")

    def test_base_cut_clipitems_have_frame_times(self):
        xml, _ = self._assemble()
        v1 = _root(xml).findall("./sequence/media/video/track")[0]
        clips = v1.findall("clipitem")
        self.assertEqual(len(clips), 2)
        # clip 1: source in 0.4s=12f, out 3.0s=90f -> length 78; timeline 0..78
        self.assertEqual(clips[0].find("in").text, "12")
        self.assertEqual(clips[0].find("out").text, "90")
        self.assertEqual(clips[0].find("start").text, "0")
        self.assertEqual(clips[0].find("end").text, "78")
        # clip 2: source 3.5s=105f..6.0s=180f -> length 75; timeline 78..153
        self.assertEqual(clips[1].find("in").text, "105")
        self.assertEqual(clips[1].find("start").text, "78")
        self.assertEqual(clips[1].find("end").text, "153")

    def test_first_file_defined_then_referenced(self):
        xml, _ = self._assemble()
        v1 = _root(xml).findall("./sequence/media/video/track")[0]
        clips = v1.findall("clipitem")
        # first clip fully defines file-1 (has a pathurl)
        f0 = clips[0].find("file")
        self.assertEqual(f0.attrib["id"], "file-1")
        self.assertIsNotNone(f0.find("pathurl"))
        self.assertTrue(f0.find("pathurl").text.startswith("file://localhost/"))
        # second clip is a bare back-reference
        f1 = clips[1].find("file")
        self.assertEqual(f1.attrib["id"], "file-1")
        self.assertIsNone(f1.find("pathurl"))

    def test_stereo_audio_tracks_with_links(self):
        xml, _ = self._assemble()
        atracks = _root(xml).findall("./sequence/media/audio/track")
        self.assertEqual(len(atracks), 2)
        a1_clips = atracks[0].findall("clipitem")
        self.assertEqual(len(a1_clips), 2)
        # each audio clip links to the video + both audio members of its group
        links = a1_clips[0].findall("link")
        refs = {lk.find("linkclipref").text for lk in links}
        self.assertEqual(refs, {"clipitem-v-1", "clipitem-a1-1", "clipitem-a2-1"})
        # audio clip timing mirrors the video clip
        self.assertEqual(a1_clips[1].find("start").text, "78")

    def test_caption_overlay_on_v2_with_alpha(self):
        xml, _ = self._assemble(with_caps=True)
        vtracks = _root(xml).findall("./sequence/media/video/track")
        self.assertEqual(len(vtracks), 2)  # V1 base cut, V2 captions
        cap = vtracks[1].find("clipitem")
        self.assertEqual(cap.find("name").text, "Captions")
        self.assertEqual(cap.find("alphatype").text, "straight")
        self.assertEqual(cap.find("start").text, "0")
        self.assertEqual(cap.find("end").text, "153")  # spans the whole cut
        self.assertEqual(cap.find("file").attrib["id"], "file-2")

    def test_no_v2_without_captions(self):
        xml, _ = self._assemble(with_caps=False)
        vtracks = _root(xml).findall("./sequence/media/video/track")
        self.assertEqual(len(vtracks), 1)

    def test_no_overlay_when_all_cues_dropped(self):
        track = make_track([(3.1, 3.4, "um")])  # only cue is in a dropped gap
        xml, cut = assemble_xmeml(
            make_decision(), track, reframed_src="/v/c.mp4",
            overlay_src="/v/cap.mov", fps=FPS,
        )
        self.assertEqual(len(_root(xml).findall("./sequence/media/video/track")), 1)
        self.assertEqual(cut.kept(), [])

    def test_cut_track_returned_and_remapped(self):
        _, cut = self._assemble()
        self.assertEqual(len(cut.kept()), 2)
        self.assertAlmostEqual(cut.cues[1].start, 3.1, places=6)

    def test_trim_filler_false_single_clip(self):
        d = make_decision([(0.0, 6.0, True, "", "whole")], trim_filler=False)
        xml, _ = assemble_xmeml(d, None, reframed_src="/v/c.mp4", fps=FPS)
        clips = _root(xml).findall("./sequence/media/video/track")[0].findall("clipitem")
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0].find("end").text, "180")  # 6.0s = 180f


if __name__ == "__main__":
    unittest.main()
