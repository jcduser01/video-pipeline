"""TDD for the INI-091 Phase 3 framing model + reframe.def + propose/render split.

Covers the headline DoD: the canonical framing model ``{scale, pan_x, pan_y}``
round-trips exactly to/from a pixel crop window; Propose writes a correct
``reframe.def``; constructing the crop from that def reproduces the model's crop
exactly; max-zoom hard-stops; manual pan overrides the subject-derived centre.
"""

import os
import tempfile
import unittest

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline.target_format import Target, aspect_preset
from video_pipeline.reframe.tracker import FrameSubject
from video_pipeline.reframe.plan import build_crop_plan, crop_dims
from video_pipeline.reframe.model import (
    FramingModel,
    CropGeometry,
    model_to_window,
    window_to_model,
    native_crop_dims,
    scaled_crop_dims,
    max_zoom,
    clamp_scale,
    resolution_readout,
    propose_framing,
)
from video_pipeline.reframe.decision import (
    ReframeDef,
    MODES,
    LOCKS,
    DEFAULT_REFRAME_MODE,
)
from video_pipeline.reframe.track_io import write_track, read_track, track_to_dict, track_from_dict
from video_pipeline.reframe.pipeline import (
    propose,
    propose_from_subjects,
    geometry_from_def,
    crop_plan_from_def,
    render_inputs_from_def,
)
from video_pipeline.reframe.framing import framing_intent


WIDE = (1920, 1080)
PORTRAIT_ASPECT = aspect_preset("full-portrait")
WIDE_ASPECT = aspect_preset("widescreen")


# ── the framing model + canonical transforms ──────────────────────────────────────

class TestFramingModel(unittest.TestCase):
    def test_defaults_are_centred_native(self):
        m = FramingModel()
        self.assertEqual((m.scale, m.pan_x, m.pan_y), (1.0, 0.5, 0.5))

    def test_scale_below_one_clamps_to_native(self):
        # no fill: scale < 1.0 is clamped up to 1.0 (native is the widest framing)
        self.assertEqual(FramingModel(scale=0.4).scale, 1.0)

    def test_pan_clamped_to_unit_range(self):
        m = FramingModel(pan_x=-0.3, pan_y=1.8)
        self.assertEqual((m.pan_x, m.pan_y), (0.0, 1.0))

    def test_dict_round_trip(self):
        m = FramingModel(scale=1.5, pan_x=0.42, pan_y=0.31)
        self.assertEqual(FramingModel.from_dict(m.to_dict()), m)


class TestCanonicalTransforms(unittest.TestCase):
    def test_native_dims_match_plan_primitive(self):
        self.assertEqual(
            native_crop_dims(*WIDE, WIDE_ASPECT),
            crop_dims(*WIDE, WIDE_ASPECT.w, WIDE_ASPECT.h),
        )

    def test_scaled_dims_match_build_crop_plan(self):
        # the model's crop size must equal the plan's at the same scale
        for scale in (1.0, 1.5, 2.0, 3.0):
            cw, ch = scaled_crop_dims(*WIDE, PORTRAIT_ASPECT, scale)
            plan = build_crop_plan([], *WIDE, out_w=PORTRAIT_ASPECT.w * 10,
                                   out_h=PORTRAIT_ASPECT.h * 10, scale=scale, duration=1.0)
            w = plan.windows[0]
            self.assertEqual((cw, ch), (w.w, w.h), f"scale={scale}")

    def test_centre_convention_centres_the_crop(self):
        # pan (0.5, 0.5) -> crop centred in the source
        g = model_to_window(FramingModel(pan_x=0.5, pan_y=0.5), *WIDE, PORTRAIT_ASPECT)
        self.assertEqual(g.x, (1920 - g.w) // 2)
        self.assertEqual(g.y, (1080 - g.h) // 2)

    def test_crop_clamped_inside_frame_no_fill(self):
        # extreme pan parks against the edge; never exceeds the footage
        g = model_to_window(FramingModel(pan_x=0.0, pan_y=0.0), *WIDE, PORTRAIT_ASPECT)
        self.assertEqual((g.x, g.y), (0, 0))
        g2 = model_to_window(FramingModel(pan_x=1.0, pan_y=1.0), *WIDE, PORTRAIT_ASPECT)
        self.assertEqual(g2.x, 1920 - g2.w)
        self.assertLessEqual(g2.x + g2.w, 1920)
        self.assertLessEqual(g2.y + g2.h, 1080)

    def test_round_trip_model_to_window_to_model_exact(self):
        # the headline: model -> window -> model -> window reproduces the window exactly
        for scale in (1.0, 1.5, 2.0):
            for px in (0.2, 0.5, 0.73):
                for py in (0.1, 0.5, 0.88):
                    m = FramingModel(scale=scale, pan_x=px, pan_y=py)
                    g = model_to_window(m, 1920, 1080, PORTRAIT_ASPECT)
                    m2 = window_to_model(g, 1920, 1080, PORTRAIT_ASPECT)
                    g2 = model_to_window(m2, 1920, 1080, PORTRAIT_ASPECT)
                    self.assertEqual((g.x, g.y, g.w, g.h), (g2.x, g2.y, g2.w, g2.h),
                                     f"scale={scale} pan=({px},{py})")

    def test_window_to_model_recovers_scale(self):
        m = FramingModel(scale=2.0, pan_x=0.5, pan_y=0.5)
        g = model_to_window(m, 1920, 1080, WIDE_ASPECT)
        back = window_to_model(g, 1920, 1080, WIDE_ASPECT)
        self.assertAlmostEqual(back.scale, 2.0, delta=0.02)


# ── max-zoom + advanced upscale + readout ─────────────────────────────────────────

class TestMaxZoom(unittest.TestCase):
    def test_max_zoom_at_least_one(self):
        # a target larger than the native crop can't even afford native -> 1.0
        mz = max_zoom(*WIDE, PORTRAIT_ASPECT, 1080, 1920)
        self.assertEqual(mz, 1.0)

    def test_max_zoom_allows_punch_in_when_crop_is_large(self):
        # 4k landscape source, modest target -> room to punch in past native
        mz = max_zoom(3840, 2160, WIDE_ASPECT, 1280, 720)
        self.assertGreater(mz, 1.0)

    def test_clamp_hard_stops_by_default(self):
        c = clamp_scale(5.0, *WIDE, PORTRAIT_ASPECT, 1080, 1920)
        self.assertTrue(c.clamped)
        self.assertEqual(c.scale, c.max_zoom)
        self.assertEqual(c.requested, 5.0)

    def test_clamp_does_not_bite_when_within_budget(self):
        c = clamp_scale(1.2, 3840, 2160, WIDE_ASPECT, 1280, 720)
        self.assertFalse(c.clamped)
        self.assertEqual(c.scale, 1.2)

    def test_allow_upscale_opt_in_bypasses_hard_stop(self):
        c = clamp_scale(5.0, *WIDE, PORTRAIT_ASPECT, 1080, 1920, allow_upscale=True)
        self.assertFalse(c.clamped)
        self.assertEqual(c.scale, 5.0)

    def test_clamp_never_below_one(self):
        c = clamp_scale(0.3, *WIDE, PORTRAIT_ASPECT, 1080, 1920)
        self.assertGreaterEqual(c.scale, 1.0)


class TestResolutionReadout(unittest.TestCase):
    def test_readout_reports_upscale_when_crop_small(self):
        r = resolution_readout(1.0, *WIDE, PORTRAIT_ASPECT, 1080, 1920)
        self.assertGreater(r.upscale_factor, 1.0)
        self.assertFalse(r.within_tolerance)

    def test_readout_within_tolerance_when_crop_ample(self):
        r = resolution_readout(1.0, 3840, 2160, WIDE_ASPECT, 1280, 720)
        self.assertLessEqual(r.upscale_factor, 1.0)
        self.assertTrue(r.within_tolerance)


# ── ML proposal seeding (subject-derived -> model) ────────────────────────────────

class TestProposeFraming(unittest.TestCase):
    def test_no_subject_is_centred(self):
        m = propose_framing(*WIDE, PORTRAIT_ASPECT)
        self.assertEqual((m.pan_x, m.pan_y), (0.5, 0.5))

    def test_subject_centre_becomes_pan_x(self):
        m = propose_framing(*WIDE, PORTRAIT_ASPECT, subject_cx=480)
        self.assertAlmostEqual(m.pan_x, 480 / 1920, places=5)

    def test_seed_reproduces_legacy_crop_placement(self):
        # propose_framing -> model_to_window must match build_crop_plan's legacy crop
        subs = [FrameSubject(t=i * 0.2, cx=620, cy=400, confidence=1.0) for i in range(6)]
        scale, yf = 1.3, 0.35
        out_w, out_h = PORTRAIT_ASPECT.w * 10, PORTRAIT_ASPECT.h * 10
        legacy = build_crop_plan(subs, *WIDE, out_w=out_w, out_h=out_h,
                                 mode="static", scale=scale, subject_y_frac=yf,
                                 duration=1.0).windows[0]
        m = propose_framing(*WIDE, PORTRAIT_ASPECT, subject_cx=620, subject_cy=400,
                            scale=scale, subject_y_frac=yf)
        g = model_to_window(m, *WIDE, PORTRAIT_ASPECT)
        self.assertEqual((g.x, g.y, g.w, g.h), (legacy.x, legacy.y, legacy.w, legacy.h))


# ── manual pan override in build_crop_plan ────────────────────────────────────────

class TestManualPan(unittest.TestCase):
    def test_pan_x_overrides_subject_centre(self):
        # subject far left, but a manual pan_x to the right wins
        subs = [FrameSubject(t=i * 0.2, cx=100, cy=540, confidence=1.0) for i in range(6)]
        plan = build_crop_plan(subs, *WIDE, mode="static", duration=1.0, pan_x=0.8)
        w = plan.windows[0]
        centre = w.x + w.w / 2
        self.assertGreater(centre, 1000)  # pulled to the right, not at the subject

    def test_pan_x_clamped_inside_frame(self):
        subs = [FrameSubject(t=0.0, cx=960, cy=540, confidence=1.0)]
        plan = build_crop_plan(subs, *WIDE, mode="static", duration=1.0, pan_x=1.0)
        w = plan.windows[0]
        self.assertEqual(w.x, 1920 - w.w)
        self.assertLessEqual(w.x + w.w, 1920)

    def test_pan_y_overrides_subject_y(self):
        subs = [FrameSubject(t=i * 0.2, cx=960, cy=200, confidence=1.0) for i in range(4)]
        # punch in so there is vertical slack, then pan to the bottom
        plan = build_crop_plan(subs, *WIDE, out_w=1080, out_h=1920, mode="static",
                               scale=2.0, duration=1.0, pan_y=1.0)
        w = plan.windows[0]
        self.assertEqual(w.y, 1080 - w.h)

    def test_pan_pins_dynamic_to_static(self):
        # an explicit horizontal pan is a fixed framing -> a single static window
        subs = [FrameSubject(t=i * 0.2, cx=300 + i * 200, cy=540, confidence=1.0)
                for i in range(6)]
        plan = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.2, pan_x=0.5)
        self.assertEqual(len(plan.windows), 1)

    def test_no_pan_keeps_subject_centring(self):
        # additive: omitting pan_* keeps the legacy subject-derived behaviour
        subs = [FrameSubject(t=i * 0.2, cx=400, cy=540, confidence=1.0) for i in range(6)]
        plan = build_crop_plan(subs, *WIDE, mode="static", duration=1.0)
        w = plan.windows[0]
        self.assertLess(w.x + w.w / 2, 700)  # near the subject at 400


# ── subject-track persistence ─────────────────────────────────────────────────────

class TestTrackIO(unittest.TestCase):
    def _subs(self):
        return [
            FrameSubject(t=0.0, cx=700.5, cy=400.25, bbox=(640, 300, 760, 500), confidence=1.0),
            FrameSubject(t=0.2, cx=710.0, cy=405.0, bbox=None, confidence=0.0),
        ]

    def test_dict_round_trip(self):
        subs = self._subs()
        back = track_from_dict(track_to_dict(subs, src_w=1920, src_h=1080))
        self.assertEqual(len(back), len(subs))
        for a, b in zip(subs, back):
            self.assertAlmostEqual(a.t, b.t)
            self.assertAlmostEqual(a.cx, b.cx)
            self.assertAlmostEqual(a.cy, b.cy)
            self.assertEqual(a.bbox, b.bbox)
            self.assertEqual(a.confidence, b.confidence)

    def test_file_round_trip(self):
        subs = self._subs()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "clip.track.json")
            write_track(p, subs, src_w=1920, src_h=1080, tracker_name="opencv")
            back = read_track(p)
        self.assertEqual(len(back), 2)
        self.assertEqual(back[0].bbox, (640.0, 300.0, 760.0, 500.0))


# ── reframe.def round-trip + editing semantics ────────────────────────────────────

class TestReframeDef(unittest.TestCase):
    def _def(self):
        return ReframeDef(
            source="clip.mp4",
            target=Target(aspect="widescreen", resolution="1080p"),
            framing=FramingModel(scale=1.5, pan_x=0.4, pan_y=0.35),
            mode="static",
            lock="none",
            framing_intent="performer",
            safe_zone_mode="generic",
            subject_track="work/clip.track.json",
            duration=12.5,
        )

    def test_yaml_round_trip_lossless(self):
        d = self._def()
        d2 = ReframeDef.from_yaml(d.to_yaml())
        self.assertEqual(d2.source, d.source)
        self.assertEqual(d2.target, d.target)
        self.assertEqual(d2.framing, d.framing)
        self.assertEqual(d2.mode, d.mode)
        self.assertEqual(d2.lock, d.lock)
        self.assertEqual(d2.framing_intent, d.framing_intent)
        self.assertEqual(d2.safe_zone_mode, d.safe_zone_mode)
        self.assertEqual(d2.subject_track, d.subject_track)
        self.assertEqual(d2.duration, d.duration)
        self.assertEqual(d2.custom, d.custom)

    def test_serialize_is_stable(self):
        d = ReframeDef.from_yaml(self._def().to_yaml())
        self.assertEqual(d.to_yaml(), ReframeDef.from_yaml(d.to_yaml()).to_yaml())

    def test_file_round_trip(self):
        d = self._def()
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "reframe.def")
            d.write(p)
            d2 = ReframeDef.read(p)
        self.assertEqual(d2.framing, d.framing)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            ReframeDef(source="c.mp4", mode="orbit")

    def test_invalid_lock_rejected(self):
        with self.assertRaises(ValueError):
            ReframeDef(source="c.mp4", lock="diagonal")

    def test_invalid_safezone_mode_rejected(self):
        with self.assertRaises(ValueError):
            ReframeDef(source="c.mp4", safe_zone_mode="bananas")

    def test_phase5_fields_carried(self):
        # mode/lock vocabularies exist and default sanely (Phase 5 just consumes them)
        self.assertIn(DEFAULT_REFRAME_MODE, MODES)
        self.assertIn("both", LOCKS)

    def test_detach_to_custom(self):
        d = self._def()
        self.assertFalse(d.custom)
        d.detach(FramingModel(scale=2.0, pan_x=0.1, pan_y=0.9))
        self.assertTrue(d.custom)
        self.assertEqual(d.framing.scale, 2.0)
        # proposal preserved
        self.assertEqual(d.proposal, FramingModel(scale=1.5, pan_x=0.4, pan_y=0.35))

    def test_reset_to_proposal(self):
        d = self._def()
        original = d.framing
        d.detach(FramingModel(scale=3.0, pan_x=0.0, pan_y=1.0))
        d.reset_to_proposal()
        self.assertFalse(d.custom)
        self.assertEqual(d.framing, original)


# ── propose -> render split (the DoD) ─────────────────────────────────────────────

class TestProposeRender(unittest.TestCase):
    def _subs(self):
        return [FrameSubject(t=i * 0.2, cx=700, cy=400, bbox=(640, 300, 760, 500),
                             confidence=1.0) for i in range(8)]

    def test_propose_writes_correct_def_and_track(self):
        with tempfile.TemporaryDirectory() as d:
            defp = os.path.join(d, "reframe.def")
            trkp = os.path.join(d, "clip.track.json")
            rdef = propose(
                "clip.mp4", self._subs(), 1920, 1080,
                Target(aspect="widescreen", resolution="auto"),
                out_w=1920, out_h=1080, def_path=defp, track_path=trkp,
                framing=framing_intent("performer"),
            )
            self.assertTrue(os.path.exists(defp))
            self.assertTrue(os.path.exists(trkp))
            self.assertEqual(rdef.subject_track, trkp)
            self.assertEqual(rdef.framing_intent, "performer")
            self.assertFalse(rdef.custom)
            # the track persisted is readable back
            self.assertEqual(len(read_track(trkp)), 8)

    def test_render_reproduces_model_crop_exactly(self):
        with tempfile.TemporaryDirectory() as d:
            defp = os.path.join(d, "reframe.def")
            trkp = os.path.join(d, "clip.track.json")
            propose(
                "clip.mp4", self._subs(), 1920, 1080,
                Target(aspect="full-portrait", resolution="auto"),
                out_w=1080, out_h=1920, def_path=defp, track_path=trkp,
                framing=framing_intent("talking-head"),
            )
            rdef, subs, plan = render_inputs_from_def(defp, 1920, 1080, 1080, 1920)
            g = geometry_from_def(rdef, 1920, 1080)
            w = plan.windows[0]
            self.assertEqual((g.x, g.y, g.w, g.h), (w.x, w.y, w.w, w.h))
            self.assertEqual(len(subs), 8)  # track replayed, not re-tracked

    def test_propose_max_zoom_hard_stops(self):
        # a talking-head punch-in against a tiny native crop is clamped to max-zoom
        subs = self._subs()
        rdef = propose_from_subjects(
            "clip.mp4", subs, 1920, 1080,
            Target(aspect="full-portrait", resolution="auto"),
            out_w=1080, out_h=1920, framing=framing_intent("talking-head"),
        )
        mz = max_zoom(1920, 1080, PORTRAIT_ASPECT, 1080, 1920)
        self.assertLessEqual(rdef.framing.scale, mz + 1e-9)

    def test_propose_allow_upscale_keeps_requested_scale(self):
        subs = self._subs()
        rdef = propose_from_subjects(
            "clip.mp4", subs, 1920, 1080,
            Target(aspect="full-portrait", resolution="auto"),
            out_w=1080, out_h=1920, framing=framing_intent("talking-head"),
            allow_upscale=True,
        )
        self.assertAlmostEqual(rdef.framing.scale, 1.30, places=2)

    def test_crop_plan_from_def_matches_geometry(self):
        d = ReframeDef(
            source="c.mp4",
            target=Target(aspect="full-portrait", resolution="auto"),
            framing=FramingModel(scale=1.0, pan_x=0.3, pan_y=0.6),
        )
        g = geometry_from_def(d, 1920, 1080)
        w = crop_plan_from_def(d, 1920, 1080, 1080, 1920).windows[0]
        self.assertEqual((g.x, g.y, g.w, g.h), (w.x, w.y, w.w, w.h))


if __name__ == "__main__":
    unittest.main()
