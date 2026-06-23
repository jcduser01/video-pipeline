"""CLI + schema wiring for INI-091 safe-zone modes (none / generic / custom).

Exercises the ``safezone-gen --mode`` surface added in INI-091:
  * the parser makes the template positional optional + adds --mode/--aspect,
  * generic/none build a resolution-independent per-aspect spec (no PNG),
  * custom keeps the legacy PNG → polygon path unchanged,
  * the inferred default (template ⇒ custom, else generic) preserves back-compat,
  * the schema surfaces the mode dropdown + the conditional template control and
    the reference assembler emits --mode.

The pure normalized engine is covered by test_safezone_normalized.py; this is the
CLI/schema seam only.
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests._util import make_template_png  # noqa: F401  (also puts src/ on path)

from video_pipeline import schema as S
from video_pipeline.cli import build_parser
from video_pipeline.safezone import SafeZoneSpec


def _run(argv):
    """Parse + dispatch a CLI argv (returns the command's exit code)."""
    ns = build_parser().parse_args(argv)
    return ns.func(ns)


class TestSafezoneGenParser(unittest.TestCase):
    def setUp(self):
        self.p = build_parser()

    def test_template_is_now_optional(self):
        ns = self.p.parse_args(["safezone-gen", "--mode", "generic"])
        self.assertIsNone(ns.template)
        self.assertEqual(ns.mode, "generic")

    def test_template_still_accepted_positionally(self):
        ns = self.p.parse_args(["safezone-gen", "tmpl.png", "--key", "alpha"])
        self.assertEqual(ns.template, "tmpl.png")
        self.assertIsNone(ns.mode)  # inferred at run time

    def test_mode_choices_enforced(self):
        with self.assertRaises(SystemExit):
            self.p.parse_args(["safezone-gen", "--mode", "bogus"])

    def test_aspect_choices_enforced(self):
        with self.assertRaises(SystemExit):
            self.p.parse_args(["safezone-gen", "--mode", "generic", "--aspect", "imax"])


class TestSafezoneGenGenericNone(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _spec(self, name):
        return SafeZoneSpec.from_json(Path(self.dir, name).read_text(encoding="utf-8"))

    def test_generic_writes_spec_without_png(self):
        out = str(Path(self.dir, "sz.json"))
        rc = _run(["safezone-gen", "--mode", "generic",
                   "--aspect", "full-portrait", "-o", out])
        self.assertEqual(rc, 0)
        spec = self._spec("sz.json")
        # full-portrait labeled-default tier is 1080x1920.
        self.assertEqual((spec.image_width, spec.image_height), (1080, 1920))
        # generic = a conservative inset, so it is strictly smaller than full frame.
        self.assertLess(spec.safe_fraction, 1.0)
        self.assertGreater(spec.safe_fraction, 0.0)

    def test_none_is_full_frame(self):
        out = str(Path(self.dir, "none.json"))
        rc = _run(["safezone-gen", "--mode", "none", "--aspect", "square", "-o", out])
        self.assertEqual(rc, 0)
        spec = self._spec("none.json")
        # none = the whole frame is safe.
        self.assertAlmostEqual(spec.safe_fraction, 1.0, places=4)

    def test_generic_default_aspect_is_full_portrait(self):
        # No --aspect, no --project: falls back to the default aspect (full-portrait).
        out = str(Path(self.dir, "def.json"))
        rc = _run(["safezone-gen", "--mode", "generic", "-o", out])
        self.assertEqual(rc, 0)
        spec = self._spec("def.json")
        self.assertEqual((spec.image_width, spec.image_height), (1080, 1920))

    def test_generic_different_aspects_differ(self):
        a = str(Path(self.dir, "a.json"))
        b = str(Path(self.dir, "b.json"))
        _run(["safezone-gen", "--mode", "generic", "--aspect", "full-portrait", "-o", a])
        _run(["safezone-gen", "--mode", "generic", "--aspect", "widescreen", "-o", b])
        sa, sb = self._spec("a.json"), self._spec("b.json")
        self.assertNotEqual(
            (sa.image_width, sa.image_height), (sb.image_width, sb.image_height)
        )


class TestSafezoneGenCustomAndInference(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.png = str(Path(self.dir, "tmpl.png"))
        # A safe rect inside a 100x200 canvas (the PNG generator's contract).
        make_template_png(self.png, 100, 200, safe_rect=(10, 20, 90, 180))

    def test_custom_explicit_uses_png(self):
        out = str(Path(self.dir, "custom.json"))
        rc = _run(["safezone-gen", "--mode", "custom", self.png, "-o", out])
        self.assertEqual(rc, 0)
        spec = SafeZoneSpec.from_json(Path(out).read_text(encoding="utf-8"))
        self.assertEqual((spec.image_width, spec.image_height), (100, 200))

    def test_custom_without_template_errors(self):
        out = str(Path(self.dir, "x.json"))
        rc = _run(["safezone-gen", "--mode", "custom", "-o", out])
        self.assertEqual(rc, 2)  # clear error, no file
        self.assertFalse(Path(out).exists())

    def test_inferred_custom_when_template_present(self):
        # Legacy invocation: a bare template, no --mode → custom (PNG read, 100x200).
        out = str(Path(self.dir, "legacy.json"))
        rc = _run(["safezone-gen", self.png, "-o", out])
        self.assertEqual(rc, 0)
        spec = SafeZoneSpec.from_json(Path(out).read_text(encoding="utf-8"))
        self.assertEqual((spec.image_width, spec.image_height), (100, 200))

    def test_inferred_generic_when_no_template(self):
        # No --mode and no template → generic (resolution-independent, no PNG).
        out = str(Path(self.dir, "inf-generic.json"))
        rc = _run(["safezone-gen", "-o", out, "--aspect", "full-portrait"])
        self.assertEqual(rc, 0)
        spec = SafeZoneSpec.from_json(Path(out).read_text(encoding="utf-8"))
        self.assertEqual((spec.image_width, spec.image_height), (1080, 1920))


class TestSafezoneGenSchema(unittest.TestCase):
    def test_mode_param_surfaces_with_options_and_default(self):
        sch = S.build_schema()
        task = next(t for t in sch.tasks if t.id == "safezone.gen")
        mode = next(p for p in task.params if p.key == "mode")
        self.assertEqual(mode.flag, "--mode")
        self.assertEqual(mode.resolved_control(), "dropdown")
        self.assertEqual(set(mode.options), {"none", "generic", "custom"})
        self.assertEqual(mode.default, "generic")

    def test_template_is_conditional_on_custom(self):
        sch = S.build_schema()
        task = next(t for t in sch.tasks if t.id == "safezone.gen")
        tmpl = next(p for p in task.params if p.key == "template")
        self.assertFalse(tmpl.required)
        self.assertEqual(tmpl.ui.depends_on_key, "mode")
        self.assertEqual(tmpl.ui.depends_on_equals, "custom")

    def test_resolve_argv_emits_mode_generic(self):
        sch = S.build_schema()
        argv = S.resolve_argv(
            sch, "safezone.gen",
            form_values={"mode": "generic", "aspect": "full-portrait"},
            artifact_paths={"safezone.def": "work/safezone.json"},
        )
        self.assertIn("--mode", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "generic")
        # generic emits no template positional; the -o output still binds.
        self.assertNotIn("design/", " ".join(argv))
        self.assertEqual(argv[argv.index("-o") + 1], "work/safezone.json")

    def test_resolve_argv_custom_emits_template_positional(self):
        sch = S.build_schema()
        argv = S.resolve_argv(
            sch, "safezone.gen",
            form_values={"mode": "custom", "template": "design/tmpl.png"},
            artifact_paths={"safezone.def": "work/safezone.json"},
        )
        self.assertIn("--mode", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "custom")
        self.assertIn("design/tmpl.png", argv)


if __name__ == "__main__":
    unittest.main()
