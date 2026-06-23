"""TDD for INI-091 Phase 5 — the Dynamic Composition Lock engine.

The set box fixes the subject's relative placement in the crop; a locked axis then
moves the crop to HOLD that placement as the subject moves (smoothed, velocity-capped,
clamped inside the source), while an unlocked axis stays parked at the box's pan. These
tests exercise the pure engine only — real tracking + ffmpeg render + real-footage
acceptance are the Mac-side seams (see :mod:`video_pipeline.reframe.probe`).
"""

import unittest

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)
from video_pipeline.reframe import FrameSubject, build_crop_plan, sample_x, sample_y
from video_pipeline.reframe.plan import window_y
from video_pipeline.reframe.crop import dynamic_filtergraph
from video_pipeline.reframe.decision import ReframeDef
from video_pipeline.reframe.model import FramingModel
from video_pipeline.reframe.pipeline import crop_plan_from_def
from video_pipeline.target_format import Target


WIDE = (1920, 1080)        # landscape source -> 9:16 crop has horizontal slack
# A source TALLER than 9:16 so the 9:16 crop has *vertical* slack (Y can actually move).
# 1080x2880 (3:8) -> crop 1080x1920 -> 960px of vertical travel.
TALL = (1080, 2880)
OUT = dict(out_w=1080, out_h=1920)


def _moving_x(start=400, step=180, n=8, cy=540, dt=0.2):
    return [FrameSubject(t=i * dt, cx=start + i * step, cy=cy, confidence=1.0)
            for i in range(n)]


def _moving_y(start=300, step=120, n=8, cx=540, dt=0.2):
    # portrait source: subject travels vertically (cx fixed), so a Y-lock can follow.
    return [FrameSubject(t=i * dt, cx=cx, cy=start + i * step, confidence=1.0)
            for i in range(n)]


class TestLockValidation(unittest.TestCase):
    def test_unknown_lock_raises(self):
        with self.assertRaises(ValueError):
            build_crop_plan(_moving_x(), *WIDE, mode="dynamic", duration=1.6, lock="z")

    def test_default_lock_is_none_legacy(self):
        # additive: omitting lock keeps the exact legacy dynamic (single static if panned)
        plan = build_crop_plan(_moving_x(), *WIDE, mode="dynamic", duration=1.4)
        self.assertEqual(plan.mode, "dynamic")
        # legacy follow produces multiple keyframes for a moving subject
        self.assertGreaterEqual(len(plan.windows), 2)


class TestLockX(unittest.TestCase):
    def _plan(self, **kw):
        return build_crop_plan(_moving_x(), *WIDE, mode="dynamic", duration=1.4,
                               lock="x", pan_x=0.5, pan_y=0.5, **kw)

    def test_mode_is_dynamic(self):
        self.assertEqual(self._plan().mode, "dynamic")

    def test_x_follows_subject(self):
        plan = self._plan()
        x0 = sample_x(plan, 0.0)
        x1 = sample_x(plan, 1.3)
        self.assertGreater(x1, x0)  # crop pans right as subject moves right

    def test_y_is_pinned_when_locked_x_only(self):
        plan = self._plan()
        ys = {w.y for w in plan.windows}
        self.assertEqual(len(ys), 1)  # Y unlocked -> a single constant top edge

    def test_keyframes_stay_in_frame(self):
        plan = self._plan()
        for w in plan.windows:
            self.assertGreaterEqual(w.x, 0)
            self.assertLessEqual(w.x + w.w, 1920)
            self.assertGreaterEqual(w.y, 0)
            self.assertLessEqual(w.y + w.h, 1080)

    def test_x_continuous_no_snap(self):
        plan = self._plan()
        dt = 0.02
        prev = sample_x(plan, 0.0)
        for i in range(1, int(1.4 / dt)):
            cur = sample_x(plan, i * dt)
            self.assertLess(abs(cur - prev), 30.0)
            prev = cur

    def test_velocity_bounded(self):
        frac = 0.10
        plan = self._plan(max_pan_frac_per_s=frac)
        max_v = frac * 1920
        dt = 0.05
        prev = sample_x(plan, 0.0)
        for i in range(1, int(1.4 / dt)):
            cur = sample_x(plan, i * dt)
            self.assertLessEqual(abs(cur - prev) / dt, max_v * 1.6)
            prev = cur


class TestRelativeAnchor(unittest.TestCase):
    """The headline behaviour: the locked axis holds the subject's relative placement."""

    def test_offset_box_holds_relative_placement(self):
        # Box panned so the subject sits OFF-centre in the crop; as the subject moves
        # (a gentle travel that never drives the crop into a frame edge) the crop should
        # hold that same relative offset (subject - crop_centre ~ const). A subject-
        # centred follow would instead drive that offset to ~0.
        # Gentle travel well inside the followable range (crop_w ~608, src 1920).
        subs = [FrameSubject(t=i * 0.25, cx=760 + i * 40, cy=540, confidence=1.0)
                for i in range(7)]  # cx 760..1000, median ~ 880
        plan = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.6,
                               lock="x", pan_x=0.55, pan_y=0.5)  # box centre 1056
        cw = plan.windows[0].w

        def crop_centre(t):
            return sample_x(plan, t) + cw / 2

        offsets = [s.cx - crop_centre(s.t) for s in subs]
        spread = max(offsets) - min(offsets)
        # held composition: the in-frame placement barely drifts across the clip.
        self.assertLess(spread, cw * 0.5)
        # and it is NOT subject-centred: the subject sits well off the crop centre.
        self.assertGreater(abs(sum(offsets) / len(offsets)), 50.0)


class TestLockY(unittest.TestCase):
    def _plan(self, **kw):
        # portrait source has vertical slack at scale=1.0 -> Y can actually move.
        return build_crop_plan(_moving_y(), *TALL, mode="dynamic", duration=1.4,
                               lock="y", pan_x=0.5, pan_y=0.5, **kw)

    def test_y_follows_subject(self):
        plan = self._plan()
        y0 = sample_y(plan, 0.0)
        y1 = sample_y(plan, 1.3)
        self.assertGreater(y1, y0)  # crop pans down as subject descends

    def test_x_is_pinned_when_locked_y_only(self):
        plan = self._plan()
        xs = {w.x for w in plan.windows}
        self.assertEqual(len(xs), 1)  # X unlocked -> a single constant left edge

    def test_keyframes_stay_in_frame(self):
        plan = self._plan()
        for w in plan.windows:
            self.assertGreaterEqual(w.y, 0)
            self.assertLessEqual(w.y + w.h, TALL[1])

    def test_y_continuous_no_snap(self):
        plan = self._plan()
        dt = 0.02
        prev = sample_y(plan, 0.0)
        for i in range(1, int(1.4 / dt)):
            cur = sample_y(plan, i * dt)
            self.assertLess(abs(cur - prev), 30.0)
            prev = cur


SQUARE = (2000, 2000)      # square source + scale -> slack on BOTH axes for lock=both


class TestLockBoth(unittest.TestCase):
    def _plan(self):
        # square source punched in (scale>1) so the 9:16 crop has slack on BOTH axes;
        # subject moves diagonally so each locked axis follows.
        subs = [FrameSubject(t=i * 0.2, cx=700 + i * 70, cy=700 + i * 70, confidence=1.0)
                for i in range(8)]
        return build_crop_plan(subs, *SQUARE, mode="dynamic", duration=1.4,
                               scale=1.5, lock="both", pan_x=0.5, pan_y=0.5)

    def test_both_axes_move(self):
        plan = self._plan()
        self.assertGreater(sample_x(plan, 1.3), sample_x(plan, 0.0))
        self.assertGreater(sample_y(plan, 1.3), sample_y(plan, 0.0))

    def test_keyframes_stay_in_frame(self):
        plan = self._plan()
        for w in plan.windows:
            self.assertGreaterEqual(w.x, 0)
            self.assertLessEqual(w.x + w.w, 2000)
            self.assertGreaterEqual(w.y, 0)
            self.assertLessEqual(w.y + w.h, 2000)

    def test_is_default_lock_for_composition(self):
        # spec default lock is "both"; engine accepts it as the standard case
        plan = self._plan()
        self.assertEqual(plan.mode, "dynamic")


class TestUnlockBothIsStaticLike(unittest.TestCase):
    def test_lock_none_with_pan_is_single_window(self):
        # lock="none" + explicit pan stays the legacy "pinned to static" rule
        plan = build_crop_plan(_moving_x(), *WIDE, mode="dynamic", duration=1.4,
                               lock="none", pan_x=0.5)
        self.assertEqual(len(plan.windows), 1)


class TestStationarySubjectUnderLock(unittest.TestCase):
    def test_constant_subject_collapses_to_one_window(self):
        subs = [FrameSubject(t=i * 0.2, cx=960, cy=540, confidence=1.0) for i in range(6)]
        plan = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.2,
                               lock="both", pan_x=0.5, pan_y=0.5)
        self.assertEqual(len(plan.windows), 1)

    def test_too_few_detections_single_window(self):
        subs = [FrameSubject(t=0.0, cx=500, cy=540, confidence=1.0)]
        plan = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.0,
                               lock="x", pan_x=0.5, pan_y=0.5)
        self.assertEqual(len(plan.windows), 1)


class TestStaticUnaffectedByLock(unittest.TestCase):
    def test_static_mode_ignores_lock(self):
        # lock only engages in dynamic mode; static stays a single window
        plan = build_crop_plan(_moving_x(), *WIDE, mode="static", duration=1.4,
                               lock="both", pan_x=0.5, pan_y=0.5)
        self.assertEqual(plan.mode, "static")
        self.assertEqual(len(plan.windows), 1)


class TestLegacyDynamicMapsToLockX(unittest.TestCase):
    """Today's dynamic (lock='none', no pan) == follow-X-with-fixed-Y. A lock='x' with
    the box centred on the subject reproduces the same following character (X moves, Y
    fixed) — confirming the mapping in the spec."""

    def test_lockx_centred_on_subject_keeps_y_fixed(self):
        subs = _moving_x()
        legacy = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.4)
        # legacy: Y constant across windows
        self.assertEqual(len({w.y for w in legacy.windows}), 1)
        lockx = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.4,
                                lock="x", pan_x=0.5, pan_y=0.5)
        self.assertEqual(len({w.y for w in lockx.windows}), 1)
        # both follow X (right-moving subject -> increasing crop x)
        self.assertGreater(sample_x(lockx, 1.3), sample_x(lockx, 0.0))


class TestDynamicFiltergraphYExpr(unittest.TestCase):
    def test_y_expression_when_y_moves(self):
        subs = _moving_y()
        plan = build_crop_plan(subs, *TALL, mode="dynamic", duration=1.4,
                               lock="y", pan_x=0.5, pan_y=0.5)
        fg = dynamic_filtergraph(plan)
        # y is now a piecewise expression (single-quoted), not a bare constant
        self.assertIn("y='", fg)
        self.assertIn("if(lt(t", fg)
        self.assertIn("clip(", fg)
        self.assertNotIn("\\,", fg)  # commas literal under single quotes, not escaped

    def test_y_constant_stays_bare(self):
        # legacy X-only follow: y must remain a bare integer (no regression)
        subs = _moving_x()
        plan = build_crop_plan(subs, *WIDE, mode="dynamic", duration=1.4)
        fg = dynamic_filtergraph(plan)
        w = plan.windows[0]
        self.assertIn(f"y={w.y}", fg)
        self.assertNotIn("y='", fg)


class TestWindowY(unittest.TestCase):
    def test_clamps_top(self):
        self.assertEqual(window_y(-100, 200, 1000), 0)

    def test_clamps_bottom(self):
        self.assertEqual(window_y(2000, 200, 1000), 800)

    def test_full_height_is_zero(self):
        self.assertEqual(window_y(500, 1000, 1000), 0)


class TestCropPlanFromDefHonoursLock(unittest.TestCase):
    def _subs(self):
        return [FrameSubject(t=i * 0.2, cx=540, cy=300 + i * 120, confidence=1.0)
                for i in range(8)]

    def test_dynamic_locked_def_produces_following_plan(self):
        rdef = ReframeDef(
            source="clip.mp4",
            target=Target(aspect="full-portrait", resolution="auto"),
            framing=FramingModel(scale=1.0, pan_x=0.5, pan_y=0.5),
            mode="dynamic",
            lock="y",
            duration=1.4,
        )
        plan = crop_plan_from_def(rdef, *TALL, 1080, 1920, subjects=self._subs())
        self.assertEqual(plan.mode, "dynamic")
        self.assertGreater(sample_y(plan, 1.3), sample_y(plan, 0.0))

    def test_static_def_still_single_window(self):
        rdef = ReframeDef(
            source="clip.mp4",
            target=Target(aspect="full-portrait", resolution="auto"),
            framing=FramingModel(scale=1.0, pan_x=0.5, pan_y=0.5),
            mode="static",
            lock="none",
            duration=1.4,
        )
        plan = crop_plan_from_def(rdef, *TALL, 1080, 1920, subjects=self._subs())
        self.assertEqual(plan.mode, "static")
        self.assertEqual(len(plan.windows), 1)


if __name__ == "__main__":
    unittest.main()
