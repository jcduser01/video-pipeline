"""Tests for the project-level Target + the downstream reset cascade (INI-091).

The cascade is the testable heart of Phase 1: aspect change vs resolution-only
change vs no change, the resolution-resets-to-Auto-on-aspect-change rule, and the
back-compat profile -> Target mapping. Pure logic; no native deps.
"""

import unittest

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)
from video_pipeline.target_format import (
    DEFAULT_ASPECT,
    DOWNSTREAM_ARTIFACTS,
    PROFILE_TO_ASPECT,
    RESOLUTION_SELECTIONS,
    Target,
    apply_reset,
    reset_downstream,
)


class TestTargetValueObject(unittest.TestCase):
    def test_defaults_to_full_portrait_auto(self):
        t = Target()
        self.assertEqual(t.aspect, "full-portrait")
        self.assertEqual(t.aspect, DEFAULT_ASPECT)
        self.assertEqual(t.resolution, "auto")

    def test_validates_aspect(self):
        with self.assertRaises(ValueError):
            Target(aspect="not-an-aspect")

    def test_validates_resolution(self):
        with self.assertRaises(ValueError):
            Target(aspect="square", resolution="8k")

    def test_resolution_selections_are_auto_plus_tiers(self):
        self.assertEqual(RESOLUTION_SELECTIONS, ("auto", "4k", "1440p", "1080p", "720p"))

    def test_preset_exposes_the_aspect_preset(self):
        self.assertEqual(Target(aspect="widescreen").preset.w, 16)

    def test_is_frozen(self):
        t = Target()
        with self.assertRaises(Exception):
            t.aspect = "square"  # type: ignore[misc]

    def test_resolve_delegates_to_resolver(self):
        # full-portrait crop big enough for 1080p -> 1080p target.
        t = Target(aspect="full-portrait", resolution="auto")
        rt = t.resolve(1080, 1920)
        self.assertEqual((rt.width, rt.height), (1080, 1920))

    def test_explicit_tier_resolves_verbatim(self):
        t = Target(aspect="full-portrait", resolution="4k")
        rt = t.resolve(800, 1400)
        self.assertEqual(rt.tier, "4k")


class TestFromProfile(unittest.TestCase):
    def test_known_profiles_map_to_aspects(self):
        self.assertEqual(Target.from_profile("reels-9x16").aspect, "full-portrait")
        self.assertEqual(Target.from_profile("story-9x16").aspect, "full-portrait")
        self.assertEqual(Target.from_profile("feed-portrait-4x5").aspect, "wide-portrait")
        self.assertEqual(Target.from_profile("feed-square-1x1").aspect, "square")
        self.assertEqual(Target.from_profile("feed-landscape-16x9").aspect, "widescreen")

    def test_all_mapped_profiles_round_trip(self):
        for profile, aspect in PROFILE_TO_ASPECT.items():
            self.assertEqual(Target.from_profile(profile).aspect, aspect, profile)

    def test_unknown_profile_falls_back_to_default(self):
        # Tolerant: never raises on an unknown/absent profile.
        self.assertEqual(Target.from_profile("bogus").aspect, DEFAULT_ASPECT)
        self.assertEqual(Target.from_profile(None).aspect, DEFAULT_ASPECT)

    def test_from_profile_resolution_is_auto(self):
        self.assertEqual(Target.from_profile("feed-square-1x1").resolution, "auto")


class TestResetDownstreamCascade(unittest.TestCase):
    """The heart of Phase 1."""

    def test_no_change_invalidates_nothing(self):
        t = Target(aspect="full-portrait", resolution="1080p")
        r = reset_downstream(t, t)
        self.assertEqual(r.invalidated, ())
        self.assertFalse(r.aspect_changed)
        self.assertFalse(r.resolution_changed)
        self.assertFalse(r.resolution_reset_to_auto)
        self.assertFalse(bool(r))

    def test_aspect_change_resets_everything_downstream(self):
        old = Target(aspect="full-portrait", resolution="1080p")
        new = Target(aspect="widescreen", resolution="1080p")
        r = reset_downstream(old, new)
        # framing + reframe + safezone + captions + qc — all of it.
        self.assertEqual(set(r.invalidated), set(DOWNSTREAM_ARTIFACTS))
        self.assertIn("framing", r)
        self.assertIn("safezone", r)
        self.assertIn("captions", r)
        self.assertIn("qc", r)
        self.assertTrue(r.aspect_changed)

    def test_aspect_change_resets_resolution_to_auto(self):
        old = Target(aspect="full-portrait", resolution="1080p")
        new = Target(aspect="square", resolution="4k")  # non-auto tier carried over
        r = reset_downstream(old, new)
        self.assertTrue(r.resolution_reset_to_auto)

    def test_aspect_change_with_new_resolution_already_auto_does_not_flag_reset(self):
        old = Target(aspect="full-portrait", resolution="1080p")
        new = Target(aspect="square", resolution="auto")
        r = reset_downstream(old, new)
        self.assertTrue(r.aspect_changed)
        self.assertFalse(r.resolution_reset_to_auto)  # already auto, nothing to reset

    def test_resolution_only_change_invalidates_pixel_downstream_not_framing(self):
        old = Target(aspect="full-portrait", resolution="1080p")
        new = Target(aspect="full-portrait", resolution="4k")
        r = reset_downstream(old, new)
        self.assertIn("safezone", r)
        self.assertIn("captions", r)
        self.assertIn("qc", r)
        # framing + reframe survive a resolution-only change.
        self.assertNotIn("framing", r)
        self.assertNotIn("reframe", r)
        self.assertTrue(r.resolution_changed)
        self.assertFalse(r.aspect_changed)
        self.assertFalse(r.resolution_reset_to_auto)

    def test_resolution_change_to_auto_still_invalidates_pixel_downstream(self):
        old = Target(aspect="square", resolution="4k")
        new = Target(aspect="square", resolution="auto")
        r = reset_downstream(old, new)
        self.assertIn("safezone", r)
        self.assertNotIn("framing", r)

    def test_invalidated_is_in_pipeline_order(self):
        old = Target(aspect="full-portrait", resolution="1080p")
        new = Target(aspect="widescreen", resolution="auto")
        r = reset_downstream(old, new)
        # ordered subset of DOWNSTREAM_ARTIFACTS
        idx = [DOWNSTREAM_ARTIFACTS.index(a) for a in r.invalidated]
        self.assertEqual(idx, sorted(idx))


class TestApplyReset(unittest.TestCase):
    def test_aspect_change_forces_resolution_auto(self):
        old = Target(aspect="full-portrait", resolution="1080p")
        new = Target(aspect="widescreen", resolution="4k")
        effective, result = apply_reset(old, new)
        self.assertEqual(effective.aspect, "widescreen")
        self.assertEqual(effective.resolution, "auto")  # snapped back
        self.assertTrue(result.aspect_changed)

    def test_resolution_only_change_passes_through(self):
        old = Target(aspect="square", resolution="1080p")
        new = Target(aspect="square", resolution="720p")
        effective, result = apply_reset(old, new)
        self.assertEqual(effective.resolution, "720p")  # honored
        self.assertFalse(result.aspect_changed)

    def test_no_change_passes_through(self):
        t = Target(aspect="portrait", resolution="1440p")
        effective, result = apply_reset(t, t)
        self.assertEqual(effective, t)
        self.assertFalse(bool(result))


if __name__ == "__main__":
    unittest.main()
