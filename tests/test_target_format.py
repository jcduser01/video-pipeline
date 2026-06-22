"""Tests for the target-format model — aspect presets, resolution tiers, Auto resolver.

Pure logic; no native deps. Encodes the CEO-locked spec (2026-06-22, INI-090) as
executable assertions: the seven presets, the canonical resolution matrix, and the
Auto resolution rules (highest non-upscaling tier within a 5% tolerance; tier-down
fallback; largest-exact-fit when even 720p doesn't fit).
"""

import unittest
from fractions import Fraction

from video_pipeline.target_format import (
    ASPECT_PRESETS,
    DEFAULT_ASPECT,
    DEFAULT_TIER,
    RESOLUTION_MATRIX,
    TIERS,
    UPSCALE_TOLERANCE,
    aspect_preset,
    default_target,
    largest_exact_fit,
    resolution_target,
    resolve,
    resolve_auto,
)
from video_pipeline.reframe.plan import crop_dims


EXPECTED_PRESETS = {
    "cinematic": (7, 3),
    "widescreen": (16, 9),
    "full-portrait": (9, 16),
    "portrait": (2, 3),
    "wide-portrait": (4, 5),
    "square": (1, 1),
    "classic-tv": (4, 3),
}

# The canonical matrix, exactly as locked with the CEO (2026-06-22).
EXPECTED_MATRIX = {
    "cinematic":     {"4k": (5040, 2160), "1440p": (3360, 1440), "1080p": (2520, 1080), "720p": (1680, 720)},
    "widescreen":    {"4k": (3840, 2160), "1440p": (2560, 1440), "1080p": (1920, 1080), "720p": (1280, 720)},
    "full-portrait": {"4k": (2160, 3840), "1440p": (1440, 2560), "1080p": (1080, 1920), "720p": (720, 1280)},
    "portrait":      {"4k": (1440, 2160), "1440p": (1200, 1800), "1080p": (1000, 1500), "720p": (720, 1080)},
    "wide-portrait": {"4k": (1728, 2160), "1440p": (1152, 1440), "1080p": (1080, 1350), "720p": (576, 720)},
    "square":        {"4k": (2160, 2160), "1440p": (1440, 1440), "1080p": (1080, 1080), "720p": (720, 720)},
    "classic-tv":    {"4k": (2880, 2160), "1440p": (1920, 1440), "1080p": (1440, 1080), "720p": (960, 720)},
}

# Auto's "in practical terms" default landing per aspect (the 1080p-class target).
EXPECTED_DEFAULTS = {
    "cinematic": (2520, 1080),
    "widescreen": (1920, 1080),
    "full-portrait": (1080, 1920),
    "portrait": (1000, 1500),
    "wide-portrait": (1080, 1350),
    "square": (1080, 1080),
    "classic-tv": (1440, 1080),
}


class TestAspectPresets(unittest.TestCase):
    def test_seven_presets_with_locked_ratios(self):
        self.assertEqual(set(ASPECT_PRESETS), set(EXPECTED_PRESETS))
        for key, (w, h) in EXPECTED_PRESETS.items():
            p = aspect_preset(key)
            self.assertEqual((p.w, p.h), (w, h), key)

    def test_ratios_are_reduced_integers(self):
        for key, p in ASPECT_PRESETS.items():
            self.assertEqual(Fraction(p.w, p.h), Fraction(p.w, p.h).limit_denominator(),
                             f"{key} ratio not reduced")
            # reduced => gcd(w, h) == 1
            self.assertEqual(Fraction(p.w, p.h).numerator, p.w, key)

    def test_default_aspect_is_full_portrait(self):
        self.assertEqual(DEFAULT_ASPECT, "full-portrait")

    def test_every_preset_has_label_and_use(self):
        for key, p in ASPECT_PRESETS.items():
            self.assertTrue(p.label.strip(), key)
            self.assertTrue(p.use.strip(), key)


class TestResolutionMatrix(unittest.TestCase):
    def test_matrix_matches_locked_spec(self):
        self.assertEqual(set(RESOLUTION_MATRIX), set(EXPECTED_MATRIX))
        for ak, tiers in EXPECTED_MATRIX.items():
            for tier, (w, h) in tiers.items():
                t = resolution_target(ak, tier)
                self.assertEqual((t.width, t.height), (w, h), f"{ak}/{tier}")

    def test_every_target_is_exact_aspect_ratio(self):
        for ak, p in ASPECT_PRESETS.items():
            for tier in TIERS:
                t = resolution_target(ak, tier)
                self.assertEqual(Fraction(t.width, t.height), Fraction(p.w, p.h),
                                 f"{ak}/{tier} not exact {p.w}:{p.h}")

    def test_every_target_has_even_dimensions(self):
        # H.264 yuv420p requires even width/height.
        for ak in ASPECT_PRESETS:
            for tier in TIERS:
                t = resolution_target(ak, tier)
                self.assertEqual(t.width % 2, 0, f"{ak}/{tier} width odd")
                self.assertEqual(t.height % 2, 0, f"{ak}/{tier} height odd")

    def test_tiers_strictly_increase_in_area(self):
        # 720p < 1080p < 1440p < 4k by pixel area, for every aspect.
        order = ("720p", "1080p", "1440p", "4k")
        for ak in ASPECT_PRESETS:
            areas = [resolution_target(ak, t).width * resolution_target(ak, t).height for t in order]
            self.assertEqual(areas, sorted(areas), f"{ak} ladder not monotonic")
            self.assertEqual(len(set(areas)), len(areas), f"{ak} ladder has a tie")

    def test_default_tier_targets_match_practical_auto_list(self):
        for ak, dims in EXPECTED_DEFAULTS.items():
            t = default_target(ak)
            self.assertEqual((t.width, t.height), dims, ak)
            self.assertEqual(t.tier, DEFAULT_TIER, ak)


class TestLargestExactFit(unittest.TestCase):
    def test_height_bound_box(self):
        # 9:16 inside 600x1000 -> height binds; largest exact even box is 558x992.
        w, h = largest_exact_fit("full-portrait", 600, 1000)
        self.assertEqual((w, h), (558, 992))
        self.assertEqual(Fraction(w, h), Fraction(9, 16))

    def test_width_bound_box(self):
        # 16:9 inside 1000x800 -> width binds.
        w, h = largest_exact_fit("widescreen", 1000, 800)
        self.assertEqual(Fraction(w, h), Fraction(16, 9))
        self.assertLessEqual(w, 1000)
        self.assertLessEqual(h, 800)

    def test_result_is_even_and_inside(self):
        for ak in ASPECT_PRESETS:
            w, h = largest_exact_fit(ak, 733, 911)
            self.assertEqual(w % 2, 0, ak)
            self.assertEqual(h % 2, 0, ak)
            self.assertLessEqual(w, 733, ak)
            self.assertLessEqual(h, 911, ak)


class TestResolveAuto(unittest.TestCase):
    def test_portrait_from_4k_landscape_lands_at_1080p(self):
        # 4K landscape (3840x2160) reframed to 9:16 yields a ~1216x2160 crop:
        # 1440p portrait (1440 wide) doesn't fit, so Auto picks 1080x1920.
        cw, ch = crop_dims(3840, 2160, 9, 16)
        t = resolve_auto("full-portrait", cw, ch)
        self.assertEqual((t.width, t.height), (1080, 1920))
        self.assertEqual(t.tier, "1080p")

    def test_portrait_from_tall_source_can_climb_to_4k(self):
        # A native 2160x3840 portrait crop supports the 4K-class target.
        t = resolve_auto("full-portrait", 2160, 3840)
        self.assertEqual(t.tier, "4k")
        self.assertEqual((t.width, t.height), (2160, 3840))

    def test_widescreen_from_4k_lands_at_4k(self):
        cw, ch = crop_dims(3840, 2160, 16, 9)
        t = resolve_auto("widescreen", cw, ch)
        self.assertEqual(t.tier, "4k")

    def test_steps_down_to_720p_when_1080p_does_not_fit(self):
        t = resolve_auto("full-portrait", 900, 1600)  # below 1080x1920
        self.assertEqual(t.tier, "720p")
        self.assertEqual((t.width, t.height), (720, 1280))

    def test_five_percent_tolerance_keeps_the_higher_tier(self):
        # Crop 2% under the 1080p target still resolves to 1080p (<=5% upscale).
        t = resolve_auto("full-portrait", int(1080 * 0.98), int(1920 * 0.98))
        self.assertEqual(t.tier, "1080p")

    def test_beyond_tolerance_steps_down(self):
        # Crop 10% under 1080p exceeds the 5% tolerance -> step down to 720p.
        t = resolve_auto("full-portrait", int(1080 * 0.90), int(1920 * 0.90))
        self.assertEqual(t.tier, "720p")

    def test_crop_below_720p_returns_largest_exact_fit(self):
        t = resolve_auto("full-portrait", 600, 1000)
        self.assertEqual(t.tier, "exact-fit")
        self.assertEqual((t.width, t.height), (558, 992))
        self.assertEqual(Fraction(t.width, t.height), Fraction(9, 16))


class TestResolveEntryPoint(unittest.TestCase):
    def test_auto_selection_delegates_to_resolver(self):
        cw, ch = crop_dims(3840, 2160, 9, 16)
        t = resolve("full-portrait", "auto", cw, ch)
        self.assertEqual((t.width, t.height), (1080, 1920))

    def test_explicit_tier_is_honored_verbatim(self):
        # Explicit tier returns the canonical target even if it exceeds the crop
        # (the no-upscale guarantee is Auto's job; explicit is the user's call).
        t = resolve("full-portrait", "4k", 800, 1400)
        self.assertEqual((t.width, t.height), (2160, 3840))
        self.assertEqual(t.tier, "4k")

    def test_unknown_aspect_or_tier_raises(self):
        with self.assertRaises(ValueError):
            resolve("not-an-aspect", "auto", 100, 100)
        with self.assertRaises(ValueError):
            resolve("square", "8k", 100, 100)

    def test_constants_are_sane(self):
        self.assertEqual(DEFAULT_TIER, "1080p")
        self.assertAlmostEqual(UPSCALE_TOLERANCE, 0.05)
        self.assertEqual(TIERS, ("4k", "1440p", "1080p", "720p"))


if __name__ == "__main__":
    unittest.main()
