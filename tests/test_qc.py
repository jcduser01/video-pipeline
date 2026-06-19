"""Phase-4 safe-zone QC tests — geometry, validation, report, and rendering.

All pure; no native toolchain. Builds a small synthetic safe-zone spec with a
lower-right notch (the same shape as the real Reels spec) and exercises the
validator on clean and deliberately-violating layouts.
"""

import json
import math
import os
import tempfile
import unittest

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline.safezone.spec import Band, SafeZoneSpec
from video_pipeline.qc.report import QCElement, QCReport, Rect, Violation
from video_pipeline.qc.validate import (
    danger_overlap,
    safe_area_of_rect,
    validate,
)


def make_spec(
    width=1000,
    height=2000,
    safe=(100, 200, 900, 1800),
    notch=(700, 1500, 900, 1800),
):
    """A safe rectangle with a lower-right notch carved out (row-convex bands)."""
    sx0, sy0, sx1, sy1 = safe
    bands = []
    if notch:
        nx0, ny0, nx1, ny1 = notch
        # Above the notch: full safe width.
        bands.append(Band(sx0, sy0, sx1, ny0))
        # Beside the notch: safe width minus the carved corner.
        bands.append(Band(sx0, ny0, nx0, ny1))
        notch_rects = [notch]
        safe_area = (sx1 - sx0) * (sy1 - sy0) - (nx1 - nx0) * (ny1 - ny0)
        # polygon is only used by overlay/printing; a minimal one is fine here.
        polygon = [(sx0, sy0), (sx1, sy0), (sx1, ny0), (nx0, ny0),
                   (nx0, ny1), (sx0, ny1)]
    else:
        bands.append(Band(sx0, sy0, sx1, sy1))
        notch_rects = []
        safe_area = (sx1 - sx0) * (sy1 - sy0)
        polygon = [(sx0, sy0), (sx1, sy0), (sx1, sy1), (sx0, sy1)]
    return SafeZoneSpec(
        profile="test-9x16",
        source_template="synthetic.png",
        image_width=width,
        image_height=height,
        key_mode="alpha",
        key_threshold=128,
        bounding_box=safe,
        polygon=polygon,
        bands=bands,
        notch_rects=notch_rects,
        safe_area_px=safe_area,
        total_px=width * height,
        generator_version="test",
    )


class GeometryTests(unittest.TestCase):
    def test_rect_fully_inside_is_clear(self):
        spec = make_spec()
        r = Rect(200, 400, 600, 500)
        frac, notch = danger_overlap(spec, r)
        self.assertAlmostEqual(frac, 0.0, places=6)
        self.assertFalse(notch)

    def test_rect_fully_outside_is_all_danger(self):
        spec = make_spec()
        r = Rect(0, 0, 100, 100)  # entirely in the danger margin
        frac, _ = danger_overlap(spec, r)
        self.assertAlmostEqual(frac, 1.0, places=6)

    def test_partial_intrusion_fraction(self):
        spec = make_spec()
        # Half the rect (x 0..100 danger, 100..200 safe) over a safe row band.
        r = Rect(0, 400, 200, 500)
        frac, _ = danger_overlap(spec, r)
        self.assertAlmostEqual(frac, 0.5, places=6)

    def test_notch_intrusion_detected(self):
        spec = make_spec()
        # A box sitting in the lower-right notch region.
        r = Rect(750, 1550, 850, 1650)
        frac, notch = danger_overlap(spec, r)
        self.assertTrue(notch)
        self.assertGreater(frac, 0.9)  # the notch is danger

    def test_safe_area_matches_manual(self):
        spec = make_spec()
        r = Rect(100, 200, 900, 300)  # fully safe band, 800x100
        self.assertAlmostEqual(safe_area_of_rect(spec, r), 800 * 100, places=3)

    def test_fractional_edges(self):
        spec = make_spec()
        r = Rect(100.0, 200.0, 100.5, 201.0)  # 0.5 x 1.0, fully safe
        self.assertAlmostEqual(safe_area_of_rect(spec, r), 0.5, places=6)


class RectTests(unittest.TestCase):
    def test_intersection(self):
        a = Rect(0, 0, 10, 10)
        b = Rect(5, 5, 20, 20)
        self.assertEqual(a.intersection(b), Rect(5, 5, 10, 10))
        self.assertEqual(a.intersection_area(b), 25)

    def test_no_intersection(self):
        a = Rect(0, 0, 10, 10)
        b = Rect(20, 20, 30, 30)
        self.assertIsNone(a.intersection(b))
        self.assertEqual(a.intersection_area(b), 0.0)

    def test_from_xywh(self):
        self.assertEqual(Rect.from_xywh(10, 20, 5, 7), Rect(10, 20, 15, 27))

    def test_degenerate_raises(self):
        with self.assertRaises(ValueError):
            Rect(10, 0, 5, 5)


class ValidateTests(unittest.TestCase):
    def test_clean_layout_passes(self):
        spec = make_spec()
        elements = [QCElement("caption", Rect(150, 1300, 850, 1400), label="cue")]
        report = validate(spec, elements)
        self.assertTrue(report.passed)
        self.assertTrue(report.clean)
        self.assertEqual(report.violations, [])

    def test_caption_over_notch_is_flagged(self):
        """The headline DoD: a deliberately-violating caption box is flagged."""
        spec = make_spec()
        # Lower-third caption spanning full safe width — its right end is in the notch.
        elements = [QCElement("caption", Rect(150, 1550, 880, 1650), label="bad cue", t=2.0, t_end=3.5)]
        report = validate(spec, elements)
        self.assertFalse(report.passed)
        self.assertEqual(len(report.violations), 1)
        v = report.violations[0]
        self.assertEqual(v.kind, "danger-intrusion")
        self.assertTrue(v.detail["hits_notch"])
        self.assertEqual(v.severity, "error")
        self.assertEqual(v.t, 2.0)

    def test_logo_outside_safe_flagged(self):
        spec = make_spec()
        elements = [QCElement("logo", Rect(20, 50, 120, 150), label="brand")]
        report = validate(spec, elements)
        self.assertFalse(report.passed)
        self.assertEqual(report.violations[0].element_kind, "logo")

    def test_intrusion_frac_tolerance(self):
        spec = make_spec()
        # 10% in danger; with a 15% tolerance it should pass.
        el = [QCElement("text", Rect(90, 400, 190, 500))]  # x 90..100 danger = 10%
        self.assertFalse(validate(spec, el, intrusion_frac=0.15).violations)
        self.assertTrue(validate(spec, el, intrusion_frac=0.0).violations)

    def test_caption_over_face(self):
        spec = make_spec()
        cap = QCElement("caption", Rect(150, 1300, 850, 1450), label="cue", t=1.0, t_end=2.0)
        face = QCElement("face", Rect(400, 1280, 700, 1480), t=1.5)
        report = validate(spec, [cap], faces=[face])
        kinds = [v.kind for v in report.violations]
        self.assertIn("caption-over-face", kinds)
        # caption itself is inside the safe zone -> no danger-intrusion
        self.assertNotIn("danger-intrusion", kinds)
        cof = next(v for v in report.violations if v.kind == "caption-over-face")
        self.assertEqual(cof.severity, "warning")
        self.assertGreater(cof.detail["overlap_frac"], 0.1)

    def test_caption_over_face_respects_time_windows(self):
        spec = make_spec()
        cap = QCElement("caption", Rect(150, 1300, 850, 1450), t=1.0, t_end=2.0)
        # Face only present much later -> no temporal overlap -> no flag.
        face = QCElement("face", Rect(400, 1280, 700, 1480), t=9.0)
        report = validate(spec, [cap], faces=[face])
        self.assertEqual(report.violations, [])

    def test_face_in_danger(self):
        spec = make_spec()
        # Face mostly under the notch (danger).
        face = QCElement("face", Rect(720, 1520, 880, 1700), t=4.0)
        report = validate(spec, [], faces=[face])
        kinds = [v.kind for v in report.violations]
        self.assertIn("face-in-danger", kinds)
        fid = next(v for v in report.violations if v.kind == "face-in-danger")
        self.assertTrue(fid.detail["hits_notch"])

    def test_face_in_safe_not_flagged(self):
        spec = make_spec()
        face = QCElement("face", Rect(400, 600, 600, 900), t=4.0)
        report = validate(spec, [], faces=[face])
        self.assertEqual(report.violations, [])

    def test_face_in_elements_raises(self):
        spec = make_spec()
        with self.assertRaises(ValueError):
            validate(spec, [QCElement("face", Rect(0, 0, 10, 10))])

    def test_unknown_kind_raises(self):
        spec = make_spec()
        with self.assertRaises(ValueError):
            validate(spec, [QCElement("watermark", Rect(150, 400, 200, 500))])


class ReportSerializationTests(unittest.TestCase):
    def test_report_json_roundtrips_shape(self):
        spec = make_spec()
        elements = [QCElement("caption", Rect(150, 1550, 880, 1650), label="bad")]
        report = validate(spec, elements, spec_name="reels-9x16.safezone.json")
        d = json.loads(report.to_json())
        self.assertEqual(d["profile"], "test-9x16")
        self.assertEqual(d["passed"], False)
        self.assertEqual(d["dimensions"], {"width": 1000, "height": 2000})
        self.assertEqual(len(d["violations"]), 1)
        self.assertIn("danger-intrusion", d["counts_by_kind"])

    def test_text_summary_pass_and_fail(self):
        spec = make_spec()
        ok = validate(spec, [QCElement("caption", Rect(150, 1300, 850, 1400))])
        self.assertIn("PASS", ok.to_text())
        bad = validate(spec, [QCElement("logo", Rect(0, 0, 100, 100))])
        text = bad.to_text()
        self.assertIn("FAIL", text)
        self.assertIn("danger-intrusion", text)

    def test_violation_describe(self):
        v = Violation(
            kind="danger-intrusion", element_kind="caption",
            rect=Rect(150, 1550, 880, 1650), severity="error", label="bad",
            t=2.0, t_end=3.5, detail={"danger_frac": 0.25, "hits_notch": True},
        )
        s = v.describe()
        self.assertIn("NOTCH", s)
        self.assertIn("2.00-3.50s", s)


class OverlayPngTests(unittest.TestCase):
    def test_overlay_png_dims_and_danger_pixels(self):
        from PIL import Image
        from video_pipeline.qc.overlay import render_overlay_png

        spec = make_spec()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "preview.png")
            render_overlay_png(spec, out, outline=False)
            img = Image.open(out).convert("RGBA")
            self.assertEqual(img.size, (1000, 2000))
            # A safe pixel is transparent; a danger-margin pixel is opaque-ish.
            self.assertEqual(img.getpixel((500, 1000))[3], 0)       # inside safe
            self.assertGreater(img.getpixel((10, 10))[3], 0)        # danger margin
            self.assertGreater(img.getpixel((800, 1650))[3], 0)     # notch = danger

    def test_overlay_writes_nested_dir(self):
        from video_pipeline.qc.overlay import render_overlay_png

        spec = make_spec()
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "sub", "deep", "preview.png")
            render_overlay_png(spec, out)
            self.assertTrue(os.path.exists(out))


class FfmpegArgvTests(unittest.TestCase):
    def test_preview_command(self):
        from video_pipeline.qc.overlay import build_preview_command

        cmd = build_preview_command("in.mp4", "ov.png", "preview.mp4")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-filter_complex", cmd)
        i = cmd.index("-filter_complex")
        self.assertIn("overlay=0:0", cmd[i + 1])
        self.assertEqual(cmd[-1], "preview.mp4")
        self.assertIn("ov.png", cmd)

    def test_clean_command_is_stream_copy(self):
        from video_pipeline.qc.overlay import build_clean_command

        cmd = build_clean_command("in.mp4", "clean.mp4")
        self.assertIn("-c", cmd)
        self.assertIn("copy", cmd)
        self.assertEqual(cmd[-1], "clean.mp4")

    def test_no_overwrite_flag(self):
        from video_pipeline.qc.overlay import build_clean_command

        self.assertNotIn("-y", build_clean_command("a", "b", overwrite=False))


class ManifestQcBlockTests(unittest.TestCase):
    def test_qc_block_parses(self):
        from video_pipeline.manifest import manifest_from_dict

        m = manifest_from_dict({
            "identity": "dyson-hope",
            "profile": "reels-9x16",
            "qc": {
                "occlusion_frac": 0.2,
                "face_danger_frac": 0.25,
                "check_caption_over_face": True,
                "elements": [
                    {"kind": "logo", "x": 40, "y": 60, "width": 120, "height": 120, "label": "brand"},
                ],
            },
        })
        cfg = m.qc_config()
        self.assertEqual(cfg["occlusion_frac"], 0.2)
        self.assertEqual(len(cfg["elements"]), 1)
        self.assertEqual(cfg["elements"][0].kind, "logo")
        self.assertEqual(cfg["elements"][0].rect, Rect(40, 60, 160, 180))

    def test_qc_block_defaults_when_absent(self):
        from video_pipeline.manifest import manifest_from_dict

        m = manifest_from_dict({"identity": "dyson-hope", "profile": "reels-9x16"})
        cfg = m.qc_config()
        self.assertEqual(cfg["elements"], [])
        self.assertIn("occlusion_frac", cfg)


class RunnerElementGatheringTests(unittest.TestCase):
    def test_caption_elements_from_props(self):
        from video_pipeline.qc.runner import caption_elements_from_props

        props = {
            "fps": 30,
            "safeBox": {"x": 100, "y": 1300, "width": 700, "height": 120},
            "cues": [
                {"text": "hello there", "from": 30, "durationInFrames": 60,
                 "startSeconds": 1.0, "endSeconds": 3.0},
                {"text": "world", "from": 90, "durationInFrames": 30},  # no seconds -> derive
            ],
        }
        els = caption_elements_from_props(props)
        self.assertEqual(len(els), 2)
        self.assertTrue(all(e.kind == "caption" for e in els))
        self.assertEqual(els[0].rect, Rect(100, 1300, 800, 1420))
        self.assertEqual(els[0].t, 1.0)
        self.assertEqual(els[0].t_end, 3.0)
        # derived from frames: 90/30=3.0 .. 120/30=4.0
        self.assertAlmostEqual(els[1].t, 3.0)
        self.assertAlmostEqual(els[1].t_end, 4.0)


if __name__ == "__main__":
    unittest.main()
