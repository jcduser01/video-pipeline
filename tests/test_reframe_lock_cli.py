"""CLI + schema wiring for INI-091 Phase 5 reframe controls (pan + composition lock).

The reframe parser already carried --scale (INI-090); INI-091 adds --pan-x / --pan-y
/ --lock and threads them into the reframe call. The pure lock engine is covered by
test_reframe_lock.py; this is the CLI surface + schema-param + reference-assembler
seam only (no tracking / ffmpeg).
"""

import unittest

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline import schema as S
from video_pipeline.cli import build_parser


class TestReframeLockParser(unittest.TestCase):
    def setUp(self):
        self.p = build_parser()

    def test_pan_and_lock_flags_parse(self):
        ns = self.p.parse_args([
            "reframe", "in.mp4", "-o", "out.mp4",
            "--scale", "1.3", "--pan-x", "0.25", "--pan-y", "0.6", "--lock", "both",
        ])
        self.assertEqual(ns.scale, 1.3)
        self.assertEqual(ns.pan_x, 0.25)
        self.assertEqual(ns.pan_y, 0.6)
        self.assertEqual(ns.lock, "both")

    def test_lock_default_is_none(self):
        ns = self.p.parse_args(["reframe", "in.mp4", "-o", "out.mp4"])
        self.assertEqual(ns.lock, "none")
        self.assertIsNone(ns.pan_x)
        self.assertIsNone(ns.pan_y)

    def test_invalid_lock_rejected(self):
        with self.assertRaises(SystemExit):
            self.p.parse_args(["reframe", "in.mp4", "-o", "o.mp4", "--lock", "diagonal"])


class TestReframeLockSchema(unittest.TestCase):
    def setUp(self):
        self.sch = S.build_schema()
        self.task = next(t for t in self.sch.tasks if t.id == "reframe")

    def _param(self, key):
        return next(p for p in self.task.params if p.key == key)

    def test_pan_lock_params_present(self):
        for key in ("pan_x", "pan_y", "lock"):
            self._param(key)  # raises StopIteration if missing
        self.assertEqual(self._param("pan_x").flag, "--pan-x")
        self.assertEqual(self._param("pan_y").flag, "--pan-y")
        self.assertEqual(self._param("lock").flag, "--lock")

    def test_lock_is_dropdown_with_axes(self):
        lock = self._param("lock")
        self.assertEqual(lock.resolved_control(), "dropdown")
        self.assertEqual(set(lock.options), {"none", "x", "y", "both"})
        self.assertEqual(lock.default, "none")

    def test_pan_are_bounded_sliders(self):
        for key in ("pan_x", "pan_y"):
            p = self._param(key)
            self.assertEqual(p.resolved_control(), "slider")
            self.assertEqual((p.min, p.max), (0.0, 1.0))

    def test_resolve_argv_threads_pan_lock(self):
        argv = S.resolve_argv(
            self.sch, "reframe",
            form_values={"scale": 1.3, "pan_x": 0.25, "pan_y": 0.6, "lock": "both"},
            artifact_paths={"base": "work/base.mp4", "reframed": "work/reframed.mp4",
                            "subject.occupancy": "work/reframe.occupancy.json"},
        )
        self.assertEqual(argv[argv.index("--scale") + 1], "1.3")
        self.assertEqual(argv[argv.index("--pan-x") + 1], "0.25")
        self.assertEqual(argv[argv.index("--pan-y") + 1], "0.6")
        self.assertEqual(argv[argv.index("--lock") + 1], "both")

    def test_resolve_argv_lock_default_emits_none(self):
        # lock has a non-None default, so the GUI emits it explicitly; pan unset
        # stays absent (no default).
        argv = S.resolve_argv(
            self.sch, "reframe", form_values={},
            artifact_paths={"base": "b.mp4", "reframed": "r.mp4",
                            "subject.occupancy": "o.json"},
        )
        self.assertEqual(argv[argv.index("--lock") + 1], "none")
        self.assertNotIn("--pan-x", argv)
        self.assertNotIn("--pan-y", argv)


if __name__ == "__main__":
    unittest.main()
