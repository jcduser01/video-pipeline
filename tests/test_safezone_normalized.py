"""TDD for the normalized (resolution-independent) safe-zone model — INI-091 P2.

Covers: the three modes (none/generic/custom), the two purposes (subject/text),
the locked per-aspect inset tables, proportional resolution at multiple tiers of
one aspect (the DoD), notch preservation through normalize→resolve, the mode
default, serialization round-trips, and that a resolved spec is a drop-in for the
legacy pixel consumers (caption placement + QC).
"""

import tempfile
import unittest
from pathlib import Path

from tests._util import make_template_png

from video_pipeline.safezone import (
    SafeZoneSpec,
    NormalizedSafeZone,
    NormalizedZone,
    SAFE_ZONE_MODES,
    DEFAULT_MODE,
    MODE_NONE,
    MODE_GENERIC,
    MODE_CUSTOM,
    PURPOSE_SUBJECT,
    PURPOSE_TEXT,
    generic_insets,
    generic_safe_zone,
    none_safe_zone,
    custom_from_png,
    build_safe_zone,
    generate_spec,
)
from video_pipeline.safezone.normalized import (
    GENERIC_SUBJECT_INSETS,
    GENERIC_TEXT_INSETS,
)
from video_pipeline.target_format import ASPECT_PRESETS, RESOLUTION_MATRIX
from video_pipeline.captions.placement import caption_box
from video_pipeline.qc.validate import validate
from video_pipeline.qc.report import QCElement, Rect


class TestModesAndDefault(unittest.TestCase):
    def test_mode_set(self):
        self.assertEqual(set(SAFE_ZONE_MODES), {MODE_NONE, MODE_GENERIC, MODE_CUSTOM})

    def test_default_is_generic(self):
        self.assertEqual(DEFAULT_MODE, MODE_GENERIC)


class TestInsetTables(unittest.TestCase):
    def test_all_aspects_have_both_tables(self):
        for key in ASPECT_PRESETS:
            self.assertIn(key, GENERIC_SUBJECT_INSETS, key)
            self.assertIn(key, GENERIC_TEXT_INSETS, key)

    def test_locked_values_full_portrait(self):
        self.assertEqual(generic_insets("full-portrait", PURPOSE_SUBJECT),
                         (0.14, 0.14, 0.22, 0.08))
        self.assertEqual(generic_insets("full-portrait", PURPOSE_TEXT),
                         (0.18, 0.16, 0.28, 0.10))

    def test_text_stricter_than_subject(self):
        # Every text inset >= the matching subject inset (text is the stricter zone).
        for key in ASPECT_PRESETS:
            for s, t in zip(generic_insets(key, PURPOSE_SUBJECT),
                            generic_insets(key, PURPOSE_TEXT)):
                self.assertGreaterEqual(t, s, key)

    def test_unknown_aspect_raises(self):
        with self.assertRaises(ValueError):
            generic_insets("nope", PURPOSE_SUBJECT)

    def test_unknown_purpose_raises(self):
        with self.assertRaises(ValueError):
            generic_insets("square", "bogus")


class TestNoneMode(unittest.TestCase):
    def test_full_frame(self):
        z = none_safe_zone(aspect="full-portrait")
        self.assertEqual(z.mode, MODE_NONE)
        spec = z.resolve(1080, 1920, purpose=PURPOSE_TEXT)
        self.assertEqual(spec.bounding_box, (0, 0, 1080, 1920))
        self.assertEqual(spec.safe_area_px, 1080 * 1920)
        self.assertFalse(spec.has_notch)
        # Everything inside the frame is safe.
        self.assertTrue(spec.contains(0, 0))
        self.assertTrue(spec.contains(1079, 1919))
        self.assertTrue(spec.rect_clear(0, 0, 1080, 1920))

    def test_none_single_band(self):
        spec = none_safe_zone().resolve(720, 1280)
        self.assertEqual(len(spec.bands), 1)


class TestGenericMode(unittest.TestCase):
    def test_inset_rectangle(self):
        z = generic_safe_zone("full-portrait")
        self.assertEqual(z.mode, MODE_GENERIC)
        spec = z.resolve(1000, 1000, purpose=PURPOSE_SUBJECT)
        # subject insets T/R/B/L = .14/.14/.22/.08 of 1000.
        self.assertEqual(spec.bounding_box, (80, 140, 860, 780))
        self.assertFalse(spec.has_notch)

    def test_text_zone_stricter_pixels(self):
        z = generic_safe_zone("full-portrait")
        sub = z.resolve(1000, 1000, purpose=PURPOSE_SUBJECT)
        txt = z.resolve(1000, 1000, purpose=PURPOSE_TEXT)
        # Text bbox is inside (or equal to) subject bbox on every edge.
        sx0, sy0, sx1, sy1 = sub.bounding_box
        tx0, ty0, tx1, ty1 = txt.bounding_box
        self.assertGreaterEqual(tx0, sx0)
        self.assertGreaterEqual(ty0, sy0)
        self.assertLessEqual(tx1, sx1)
        self.assertLessEqual(ty1, sy1)

    def test_proportional_across_resolutions_full_portrait(self):
        """DoD: 1080p / 1440p / 4k of one aspect give proportional zones."""
        z = generic_safe_zone("full-portrait")
        fracs = []
        for tier in ("1080p", "1440p", "4k"):
            w, h = RESOLUTION_MATRIX["full-portrait"][tier]
            spec = z.resolve(w, h, purpose=PURPOSE_TEXT)
            fracs.append(spec.safe_fraction)
        # The safe *fraction* is resolution-independent (within rounding).
        for f in fracs[1:]:
            self.assertAlmostEqual(f, fracs[0], places=3)

    def test_resolution_scales_bbox(self):
        z = generic_safe_zone("square")
        s1 = z.resolve(1080, 1080, purpose=PURPOSE_TEXT)
        s2 = z.resolve(2160, 2160, purpose=PURPOSE_TEXT)
        # Doubling resolution doubles the bbox edges (square text insets uniform-ish).
        for a, b in zip(s1.bounding_box, s2.bounding_box):
            self.assertAlmostEqual(b, 2 * a, delta=2)

    def test_every_aspect_resolves(self):
        for key in ASPECT_PRESETS:
            w, h = RESOLUTION_MATRIX[key]["1080p"]
            for purpose in (PURPOSE_SUBJECT, PURPOSE_TEXT):
                spec = generic_safe_zone(key).resolve(w, h, purpose=purpose)
                self.assertGreater(spec.safe_area_px, 0, f"{key}/{purpose}")
                self.assertLess(spec.safe_area_px, spec.total_px, f"{key}/{purpose}")


class TestCustomMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = Path(self.tmp.name) / "notch.png"
        make_template_png(
            self.png, 100, 200,
            safe_rect=(10, 20, 90, 180),
            notch_rect=(70, 120, 90, 180),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_custom_from_png_normalizes(self):
        z = custom_from_png(str(self.png), aspect="full-portrait")
        self.assertEqual(z.mode, MODE_CUSTOM)
        self.assertEqual(z.aspect, "full-portrait")
        # Re-resolve to the SAME canvas -> matches the raw generator spec.
        spec = z.resolve(100, 200, purpose=PURPOSE_TEXT)
        raw = generate_spec(str(self.png))
        self.assertEqual(spec.bounding_box, raw.bounding_box)
        self.assertTrue(spec.has_notch)

    def test_custom_notch_preserved_and_scaled(self):
        z = custom_from_png(str(self.png), aspect="full-portrait")
        # Resolve to double canvas -> notch scales proportionally, still danger.
        spec = z.resolve(200, 400, purpose=PURPOSE_TEXT)
        self.assertTrue(spec.has_notch)
        # The notch was lower-right at (70,120)-(90,180) of 100x200; at 200x400 it
        # is (140,240)-(180,360). A point inside it must be danger.
        self.assertFalse(spec.contains(160, 300))
        self.assertTrue(spec.contains(100, 100))   # interior

    def test_custom_proportional(self):
        z = custom_from_png(str(self.png), aspect="full-portrait")
        f1 = z.resolve(100, 200).safe_fraction
        f2 = z.resolve(300, 600).safe_fraction
        self.assertAlmostEqual(f1, f2, places=2)


class TestBuildFactory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.png = Path(self.tmp.name) / "plain.png"
        make_template_png(self.png, 100, 200, safe_rect=(10, 20, 90, 180))

    def tearDown(self):
        self.tmp.cleanup()

    def test_build_none(self):
        z = build_safe_zone(MODE_NONE, "square")
        self.assertEqual(z.mode, MODE_NONE)

    def test_build_generic(self):
        z = build_safe_zone(MODE_GENERIC, "widescreen")
        self.assertEqual(z.mode, MODE_GENERIC)

    def test_build_custom_requires_png(self):
        with self.assertRaises(ValueError):
            build_safe_zone(MODE_CUSTOM, "square")

    def test_build_custom(self):
        z = build_safe_zone(MODE_CUSTOM, "full-portrait", png_path=str(self.png))
        self.assertEqual(z.mode, MODE_CUSTOM)

    def test_build_unknown_mode(self):
        with self.assertRaises(ValueError):
            build_safe_zone("bogus", "square")


class TestSerialization(unittest.TestCase):
    def test_roundtrip_generic(self):
        z = generic_safe_zone("portrait")
        back = NormalizedSafeZone.from_dict(z.to_dict())
        self.assertEqual(back.mode, z.mode)
        self.assertEqual(back.aspect, z.aspect)
        self.assertEqual(back.text.polygon, z.text.polygon)
        self.assertEqual(back.subject.polygon, z.subject.polygon)
        # Resolves identically.
        self.assertEqual(z.resolve(1000, 1500).bounding_box,
                         back.resolve(1000, 1500).bounding_box)

    def test_zone_roundtrip(self):
        z = NormalizedZone(polygon=[(0.1, 0.2), (0.9, 0.2), (0.9, 0.8), (0.1, 0.8)],
                           notch_rects=[(0.7, 0.6, 0.9, 0.8)])
        back = NormalizedZone.from_dict(z.to_dict())
        self.assertEqual(back.polygon, z.polygon)
        self.assertEqual(back.notch_rects, z.notch_rects)


class TestValidationErrors(unittest.TestCase):
    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            NormalizedSafeZone(mode="bogus", aspect="square",
                               subject=none_safe_zone().subject,
                               text=none_safe_zone().text)

    def test_resolve_needs_positive_dims(self):
        with self.assertRaises(ValueError):
            generic_safe_zone("square").resolve(0, 100)

    def test_unknown_purpose_rejected(self):
        with self.assertRaises(ValueError):
            generic_safe_zone("square").zone("bogus")


class TestDropInForConsumers(unittest.TestCase):
    """A resolved spec must work with the existing pixel consumers unchanged."""

    def test_caption_box_on_generic(self):
        spec = generic_safe_zone("full-portrait").resolve(1080, 1920, purpose=PURPOSE_TEXT)
        box = caption_box(spec, position="lower-third")
        # The derived box must be fully inside the safe region.
        self.assertTrue(spec.rect_clear(box.x, box.y, box.x1, box.y1))

    def test_caption_box_on_none(self):
        spec = none_safe_zone().resolve(1080, 1920)
        box = caption_box(spec, position="lower-third")
        self.assertTrue(spec.rect_clear(box.x, box.y, box.x1, box.y1))

    def test_qc_clear_caption_passes(self):
        spec = generic_safe_zone("full-portrait").resolve(1080, 1920, purpose=PURPOSE_TEXT)
        box = caption_box(spec, position="lower-third")
        el = QCElement(kind="caption", rect=Rect.from_xywh(box.x, box.y, box.width, box.height))
        report = validate(spec, [el])
        self.assertEqual([v for v in report.violations if v.kind == "danger-intrusion"], [])

    def test_qc_intruding_caption_flagged(self):
        spec = generic_safe_zone("full-portrait").resolve(1080, 1920, purpose=PURPOSE_TEXT)
        # A box poking into the top danger margin.
        el = QCElement(kind="caption", rect=Rect.from_xywh(100, 0, 400, 50))
        report = validate(spec, [el])
        self.assertTrue(any(v.kind == "danger-intrusion" for v in report.violations))

    def test_qc_none_flags_nothing(self):
        spec = none_safe_zone().resolve(1080, 1920)
        el = QCElement(kind="caption", rect=Rect.from_xywh(0, 0, 1080, 100))
        report = validate(spec, [el])
        self.assertEqual(report.violations, [])


if __name__ == "__main__":
    unittest.main()
