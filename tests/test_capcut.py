"""CapCut export — arranged-media bundle (planning pure; copy via tmp dir)."""

import tempfile
import unittest
from pathlib import Path

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline.capcut import (
    export_capcut,
    gather_layers,
    plan_copy,
    readme_text,
)


class GatherTests(unittest.TestCase):
    def test_orders_low_to_high_z(self):
        layers = gather_layers("base.mp4", "captions.mov", "composite.mp4")
        self.assertEqual([l.z_order for l in layers], [0, 30, 100])
        self.assertEqual([l.label for l in layers],
                         ["Base cut", "Captions", "Composite (reference)"])

    def test_only_provided_layers(self):
        self.assertEqual(len(gather_layers("base.mp4")), 1)
        self.assertEqual(len(gather_layers("base.mp4", composite="c.mp4")), 2)

    def test_plan_copies_into_media_preserving_names(self):
        layers = gather_layers("/a/base.mp4", "/b/captions.mov")
        plan = plan_copy("/out/exports/capcut", layers)
        self.assertEqual(
            plan,
            [("/a/base.mp4", "/out/exports/capcut/media/base.mp4"),
             ("/b/captions.mov", "/out/exports/capcut/media/captions.mov")],
        )

    def test_readme_lists_stack_and_explains_no_import(self):
        txt = readme_text(gather_layers("base.mp4", "captions.mov", "composite.mp4"))
        self.assertIn("CapCut cannot import", txt)
        self.assertIn("base.mp4", txt)
        self.assertIn("z=0", txt)
        self.assertIn("z=100", txt)


class ExportTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "exports" / "capcut"
            res = export_capcut(str(out), base="/x/base.mp4", dry_run=True)
            self.assertEqual(res["layers"], 1)
            self.assertFalse(out.exists())  # nothing written

    def test_real_run_copies_media_and_writes_readme(self):
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "src"
            src.mkdir()
            base = src / "base.mp4"; base.write_bytes(b"v")
            caps = src / "captions.mov"; caps.write_bytes(b"c")
            comp = src / "composite.mp4"; comp.write_bytes(b"k")
            out = Path(d) / "exports" / "capcut"
            res = export_capcut(str(out), base=str(base), captions=str(caps),
                                composite=str(comp))
            self.assertEqual(res["layers"], 3)
            self.assertTrue((out / "media" / "base.mp4").exists())
            self.assertTrue((out / "media" / "captions.mov").exists())
            self.assertTrue((out / "media" / "composite.mp4").exists())
            self.assertTrue((out / "README.txt").exists())
            self.assertIn("bottom-to-top", (out / "README.txt").read_text())

    def test_no_base_raises(self):
        with self.assertRaises(ValueError):
            export_capcut("/tmp/x", base="")


if __name__ == "__main__":
    unittest.main()
