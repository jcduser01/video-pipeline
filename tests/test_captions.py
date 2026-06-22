"""TDD for the captions phase (INI-085 Phase 3).

All the pure logic is tested here: glossary-aware chunking (timing layer), the
caption-file round trip, SRT export, safe-zone-aware placement, layered style
config, and the Remotion props contract. mlx-whisper transcription and the
Remotion render are the daily-driver steps and are intentionally out of the
sandbox suite (only the pure argv builder for Remotion is checked).
"""

import json
import tempfile
import unittest
from pathlib import Path

from tests._util import REPO_ROOT  # noqa: F401  (ensures src/ on path)

from video_pipeline.captions import (
    BG_RADIUS_MAX,
    FONT_ALLOWLIST,
    FONT_SIZE_MAX,
    FONT_SIZE_MIN,
    STROKE_WIDTH_MAX,
    CaptionStyle,
    CaptionTrack,
    Cue,
    apply_glossary_to_words,
    build_props_from_safezone,
    caption_box,
    chunk_transcript,
    cues_to_srt,
    frame_extract_command,
    load_caption_style,
    preview_frame_times,
    remotion_render_command,
    track_to_remotion_props,
)
from video_pipeline.captions.export import _srt_timestamp, seconds_to_frame
from video_pipeline.glossary import Glossary, load_glossary
from video_pipeline.roughcut.transcript import Transcript, Word
from video_pipeline.safezone import generate_spec
from video_pipeline.safezone.spec import SafeZoneSpec

CONFIG_ROOT = REPO_ROOT / "config"


def W(text, start, end):
    return Word(text=text, start=start, end=end)


def words_to_transcript(specs, language="en"):
    """specs: list of (text, start, end)."""
    return Transcript(tuple(W(t, s, e) for t, s, e in specs), language=language)


# ── style config layering ─────────────────────────────────────────────────────

class StyleLayeringTests(unittest.TestCase):
    def test_defaults_are_legible_reels_sized(self):
        s = CaptionStyle()
        self.assertEqual(s.position, "lower-third")
        self.assertGreaterEqual(s.stroke_width, 1)
        self.assertGreaterEqual(s.max_words, s.min_words)

    def test_global_layer_loads(self):
        s = load_caption_style(CONFIG_ROOT, identity="does-not-exist")
        # falls back to global.yml values (uppercase true, emphasis gold)
        self.assertTrue(s.uppercase)
        self.assertEqual(s.max_words, 4)

    def test_identity_overrides_global(self):
        s = load_caption_style(CONFIG_ROOT, identity="dyson-hope")
        self.assertEqual(s.emphasis_color, "#9C97F4")  # identity wins

    def test_jcd_identity_disables_uppercase(self):
        s = load_caption_style(CONFIG_ROOT, identity="jason-cook-design")
        self.assertFalse(s.uppercase)
        self.assertEqual(s.max_chars, 30)

    def test_project_overrides_win(self):
        s = load_caption_style(
            CONFIG_ROOT, identity="dyson-hope",
            overrides={"position": "center", "max_words": 3},
        )
        self.assertEqual(s.position, "center")
        self.assertEqual(s.max_words, 3)

    def test_unknown_keys_ignored(self):
        s = load_caption_style(CONFIG_ROOT, identity="dyson-hope",
                               overrides={"bogus": 123})
        self.assertIsInstance(s, CaptionStyle)

    def test_bad_position_rejected(self):
        with self.assertRaises(ValueError):
            CaptionStyle(position="sideways")

    def test_h_offset_default_and_validation(self):
        self.assertEqual(CaptionStyle().h_offset, "clear-notch")
        self.assertEqual(
            load_caption_style(CONFIG_ROOT, "dyson-hope",
                               overrides={"h_offset": "center"}).h_offset,
            "center",
        )
        with self.assertRaises(ValueError):
            CaptionStyle(h_offset="diagonal")

    def test_bad_word_bounds_rejected(self):
        with self.assertRaises(ValueError):
            CaptionStyle(min_words=5, max_words=2)

    # ── INI-088: per-run style knobs, caps + font allowlist ──
    def test_style_overrides_apply_visual_knobs(self):
        s = load_caption_style(
            CONFIG_ROOT, identity="dyson-hope",
            overrides={"font_size": 120, "fill_color": "#FF0000",
                       "stroke_color": "#101010", "stroke_width": 12},
        )
        self.assertEqual(s.font_size, 120)
        self.assertEqual(s.fill_color, "#FF0000")
        self.assertEqual(s.stroke_color, "#101010")
        self.assertEqual(s.stroke_width, 12)

    def test_brand_and_system_fonts_in_allowlist(self):
        # the identity configs reference these; loading must not raise
        for ident in ("dyson-hope", "jason-cook-design", "sigil-zero"):
            self.assertIsInstance(load_caption_style(CONFIG_ROOT, ident), CaptionStyle)
        self.assertIn("Helvetica", FONT_ALLOWLIST)
        self.assertIn("Archivo", FONT_ALLOWLIST)

    def test_bad_font_rejected(self):
        with self.assertRaises(ValueError):
            CaptionStyle(font_family="Comic Sans MS")

    def test_font_family_match_is_case_insensitive(self):
        self.assertEqual(CaptionStyle(font_family="helvetica").font_family, "helvetica")

    def test_font_size_caps_enforced(self):
        with self.assertRaises(ValueError):
            CaptionStyle(font_size=FONT_SIZE_MIN - 1)
        with self.assertRaises(ValueError):
            CaptionStyle(font_size=FONT_SIZE_MAX + 1)

    def test_stroke_width_caps_enforced(self):
        CaptionStyle(stroke_width=0)  # 0 = no stroke, valid
        with self.assertRaises(ValueError):
            CaptionStyle(stroke_width=STROKE_WIDTH_MAX + 1)
        with self.assertRaises(ValueError):
            CaptionStyle(stroke_width=-1)

    # ── INI-088 Phase 2: background plate ──
    def test_bg_defaults_off(self):
        s = CaptionStyle()
        self.assertFalse(s.bg_enabled)
        self.assertEqual(s.bg_color, "#000000")
        self.assertEqual(s.bg_radius, 0)

    def test_bg_to_dict_carries_plate(self):
        d = CaptionStyle(bg_enabled=True, bg_color="#112233", bg_radius=24).to_dict()
        self.assertEqual(
            (d["bg_enabled"], d["bg_color"], d["bg_radius"]), (True, "#112233", 24)
        )

    def test_bg_overrides_apply_and_carry_to_props(self):
        s = load_caption_style(
            CONFIG_ROOT, identity="dyson-hope",
            overrides={"bg_enabled": True, "bg_color": "#0A0A0A", "bg_radius": 30},
        )
        self.assertTrue(s.bg_enabled)
        self.assertEqual(s.bg_radius, 30)

    def test_bg_radius_caps_enforced(self):
        CaptionStyle(bg_radius=0)
        with self.assertRaises(ValueError):
            CaptionStyle(bg_radius=-1)
        with self.assertRaises(ValueError):
            CaptionStyle(bg_radius=BG_RADIUS_MAX + 1)


# ── glossary correction (timing layer, applied to words) ──────────────────────

class GlossaryCorrectionTests(unittest.TestCase):
    def test_single_word_correction_keeps_timing(self):
        words = [W("remotion", 0.0, 0.5), W("rocks", 0.5, 1.0)]
        g = Glossary(corrections={"remotion": "Remotion"})
        out = apply_glossary_to_words(words, g)
        self.assertEqual(out[0].text, "Remotion")
        self.assertEqual((out[0].start, out[0].end), (0.0, 0.5))

    def test_multiword_correction_collapses_and_spans(self):
        words = [W("sigil", 1.0, 1.4), W("zero", 1.4, 1.9), W("drops", 1.9, 2.3)]
        g = Glossary(corrections={"sigil zero": "SIGIL.ZERO"})
        out = apply_glossary_to_words(words, g)
        self.assertEqual([w.text for w in out], ["SIGIL.ZERO", "drops"])
        self.assertEqual((out[0].start, out[0].end), (1.0, 1.9))  # spans both

    def test_longest_key_wins(self):
        words = [W("sigil", 0.0, 0.3), W("dot", 0.3, 0.6), W("zero", 0.6, 0.9)]
        g = Glossary(corrections={"sigil": "Sigil", "sigil dot zero": "SIGIL.ZERO"})
        out = apply_glossary_to_words(words, g)
        self.assertEqual([w.text for w in out], ["SIGIL.ZERO"])

    def test_no_glossary_is_passthrough(self):
        words = [W("hi", 0.0, 0.2)]
        self.assertEqual(apply_glossary_to_words(words, None), words)

    def test_real_dyson_hope_glossary_corrects_first_pass(self):
        g = load_glossary(CONFIG_ROOT, "dyson-hope")
        words = [W("dish", 0.0, 0.3), W("and", 0.3, 0.5), W("beats", 0.5, 0.8)]
        out = apply_glossary_to_words(words, g)
        self.assertEqual(out[0].text, "Dish n' BEATS")


# ── chunking (timing layer) ───────────────────────────────────────────────────

def even_words(tokens, dur=0.35, gap=0.05):
    """Build a transcript of evenly-spaced words (no big gaps/punctuation)."""
    specs = []
    t = 0.0
    for w in tokens:
        specs.append((w, round(t, 3), round(t + dur, 3)))
        t += dur + gap
    return words_to_transcript(specs)


class ChunkingTests(unittest.TestCase):
    def test_groups_balanced_within_range(self):
        cues = chunk_transcript(
            even_words(["one", "two", "three", "four", "five", "six"]),
            CaptionStyle(max_words=4, min_words=2, max_chars=100, max_gap_s=5),
        )
        self.assertTrue(all(2 <= len(c.words) <= 4 for c in cues))
        # 6 words: the balanced cut is 3 + 3, not the greedy 4 + 2.
        self.assertEqual([len(c.words) for c in cues], [3, 3])

    def test_ceo_example_phrase_aware_no_widow(self):
        # "I still have the first record I ever bought." — the motivating case.
        cues = chunk_transcript(
            even_words(["I", "still", "have", "the", "first",
                        "record", "I", "ever", "bought."]),
            CaptionStyle(max_words=4, min_words=2, max_chars=100, max_gap_s=5),
        )
        self.assertEqual(
            [c.text for c in cues],
            ["I still have", "the first record", "I ever bought."],
        )

    def test_no_trailing_widow(self):
        # 5 words would greedily give 4 + 1 (a widow); balanced gives no 1-word cue.
        cues = chunk_transcript(
            even_words(["alpha", "bravo", "charlie", "delta", "echo"]),
            CaptionStyle(max_words=4, min_words=2, max_chars=100, max_gap_s=5),
        )
        self.assertTrue(all(len(c.words) >= 2 for c in cues))

    def test_breaks_before_function_word_not_after(self):
        # prefer "... wall" | "and we ran" over "... wall and" | "we ran"
        cues = chunk_transcript(
            even_words(["we", "hit", "the", "wall", "and", "we", "ran"]),
            CaptionStyle(max_words=4, min_words=2, max_chars=100, max_gap_s=5),
        )
        # no cue should END on the conjunction "and"
        self.assertFalse(any(c.words[-1].lower() == "and" for c in cues))
        # and "and" should START a cue (break happened before it)
        self.assertTrue(any(c.words[0].lower() == "and" for c in cues))

    def test_single_word_mode_via_range(self):
        cues = chunk_transcript(
            even_words(["one", "two", "three", "four", "five"]),
            CaptionStyle(min_words=1, max_words=1, max_chars=100, max_gap_s=5),
        )
        self.assertEqual([len(c.words) for c in cues], [1, 1, 1, 1, 1])
        self.assertEqual([c.text for c in cues], ["one", "two", "three", "four", "five"])

    def test_range_one_to_two_allows_singles_and_pairs(self):
        cues = chunk_transcript(
            even_words(["one", "two", "three"]),
            CaptionStyle(min_words=1, max_words=2, max_chars=100, max_gap_s=5),
        )
        self.assertTrue(all(1 <= len(c.words) <= 2 for c in cues))
        self.assertEqual(sum(len(c.words) for c in cues), 3)

    def test_cue_timing_spans_first_to_last_word(self):
        t = words_to_transcript([("a", 0.0, 0.3), ("b", 0.3, 0.7)])
        cues = chunk_transcript(t, CaptionStyle(max_words=2, min_words=2, max_gap_s=5, max_chars=100))
        self.assertEqual((cues[0].start, cues[0].end), (0.0, 0.7))

    def test_sentence_punctuation_forces_break(self):
        t = words_to_transcript([("Hi.", 0.0, 0.3), ("Now", 0.35, 0.6), ("go", 0.65, 0.9)])
        cues = chunk_transcript(t, CaptionStyle(max_words=4, min_words=2, max_gap_s=5, max_chars=100))
        self.assertEqual(cues[0].text, "Hi.")  # hard break even though below min_words
        self.assertEqual(cues[1].text, "Now go")

    def test_long_gap_forces_break(self):
        t = words_to_transcript([("hello", 0.0, 0.3), ("there", 2.0, 2.3)])  # 1.7s gap
        cues = chunk_transcript(t, CaptionStyle(max_words=4, min_words=2, max_gap_s=0.6, max_chars=100))
        self.assertEqual(len(cues), 2)

    def test_max_chars_caps_cue(self):
        t = words_to_transcript(
            [("alpha", 0.0, 0.3), ("bravo", 0.35, 0.6), ("charlie", 0.65, 0.9)]
        )  # "alpha bravo" = 11 chars, +" charlie" = 19
        cues = chunk_transcript(t, CaptionStyle(max_words=9, min_words=1, max_gap_s=5, max_chars=12))
        self.assertTrue(all(len(c.text) <= 12 for c in cues))
        self.assertEqual(cues[-1].text, "charlie")

    def test_target_words_must_be_in_range(self):
        with self.assertRaises(ValueError):
            CaptionStyle(min_words=2, max_words=4, target_words=5)

    def test_glossary_applied_before_chunking(self):
        t = words_to_transcript([("sigil", 0.0, 0.3), ("zero", 0.3, 0.6), ("live", 0.65, 0.9)])
        g = Glossary(terms=["SIGIL.ZERO"], corrections={"sigil zero": "SIGIL.ZERO"})
        cues = chunk_transcript(t, CaptionStyle(max_words=4, min_words=1, max_gap_s=5, max_chars=100,
                                                emphasize_glossary_terms=True), glossary=g)
        all_words = [w for c in cues for w in c.words]
        self.assertIn("SIGIL.ZERO", all_words)
        # the cue containing the term flags it as emphasis
        cue = next(c for c in cues if "SIGIL.ZERO" in c.words)
        self.assertIn(cue.words.index("SIGIL.ZERO"), cue.emphasis)

    def test_emphasis_off_when_disabled(self):
        t = words_to_transcript([("SIGIL.ZERO", 0.0, 0.4), ("now", 0.45, 0.7)])
        g = Glossary(terms=["SIGIL.ZERO"])
        cues = chunk_transcript(
            t, CaptionStyle(max_words=4, min_words=1, max_gap_s=5, max_chars=100,
                            emphasize_glossary_terms=False), glossary=g)
        self.assertTrue(all(c.emphasis == [] for c in cues))

    def test_empty_transcript_yields_no_cues(self):
        self.assertEqual(chunk_transcript(Transcript(()), CaptionStyle()), [])


# ── caption file round trip (the product) ─────────────────────────────────────

class CaptionFileTests(unittest.TestCase):
    def _sample(self):
        return CaptionTrack(
            source="clip.mp4",
            identity="dyson-hope",
            profile="reels-9x16",
            style_ref="caption-styles/dyson-hope",
            language="en",
            cues=[
                Cue(0, 0.4, 1.12, ["I", "used", "to"], emphasis=[]),
                Cue(1, 1.12, 2.04, ["make", "fun", "of", "SIGIL.ZERO"], emphasis=[3]),
            ],
        )

    def test_round_trip_lossless(self):
        track = self._sample()
        back = CaptionTrack.from_yaml(track.to_yaml())
        self.assertEqual(len(back.cues), 2)
        self.assertEqual(back.cues[1].words, ["make", "fun", "of", "SIGIL.ZERO"])
        self.assertEqual(back.cues[1].emphasis, [3])
        self.assertEqual(back.identity, "dyson-hope")
        self.assertEqual(back.cues[0].start, 0.4)

    def test_hand_edit_of_text_reparses_words(self):
        track = self._sample()
        text = track.to_yaml().replace("make fun of SIGIL.ZERO", "make fun")
        back = CaptionTrack.from_yaml(text)
        self.assertEqual(back.cues[1].words, ["make", "fun"])
        # emphasis index 3 no longer valid -> dropped on construction
        self.assertEqual(back.cues[1].emphasis, [])

    def test_keep_flag_filters_kept(self):
        track = self._sample()
        track.cues[0].keep = False
        back = CaptionTrack.from_yaml(track.to_yaml())
        self.assertEqual(len(back.kept()), 1)
        self.assertEqual(back.kept()[0].index, 1)

    def test_write_read_file(self):
        track = self._sample()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "captions.yml"
            track.write(p)
            back = CaptionTrack.read(p)
            self.assertEqual(back.to_yaml(), track.to_yaml())

    def test_reindex_sorts_and_renumbers(self):
        track = CaptionTrack(source="x", cues=[
            Cue(5, 2.0, 2.5, ["b"]), Cue(2, 0.0, 0.5, ["a"]),
        ])
        track.reindex()
        self.assertEqual([c.index for c in track.cues], [0, 1])
        self.assertEqual(track.cues[0].text, "a")


# ── SRT export ────────────────────────────────────────────────────────────────

class SrtTests(unittest.TestCase):
    def test_timestamp_format(self):
        self.assertEqual(_srt_timestamp(0), "00:00:00,000")
        self.assertEqual(_srt_timestamp(3661.5), "01:01:01,500")

    def test_srt_renumbers_over_kept(self):
        track = CaptionTrack(source="x", cues=[
            Cue(0, 0.0, 0.5, ["a"], keep=False),
            Cue(1, 0.5, 1.0, ["b"]),
            Cue(2, 1.0, 1.5, ["c"]),
        ])
        srt = cues_to_srt(track)
        self.assertIn("1\n00:00:00,500 --> 00:00:01,000\nb", srt)
        self.assertIn("2\n00:00:01,000 --> 00:00:01,500\nc", srt)
        self.assertNotIn("\na\n", srt)  # dropped cue absent

    def test_uppercase_option(self):
        track = CaptionTrack(source="x", cues=[Cue(0, 0.0, 0.5, ["hi"])])
        self.assertIn("HI", cues_to_srt(track, uppercase=True))


# ── placement inside the safe zone ────────────────────────────────────────────

class PlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec_path = REPO_ROOT / "config" / "safezone" / "reels-9x16.safezone.json"
        cls.spec = SafeZoneSpec.from_json(spec_path.read_text(encoding="utf-8"))

    def test_lower_third_box_clears_notch(self):
        box = caption_box(self.spec, position="lower-third")
        # the real proof: the whole box lies inside the safe polygon (notch incl.)
        self.assertTrue(
            self.spec.rect_clear(box.x, box.y, box.x1, box.y1),
            f"box {box.to_dict()} intrudes on the danger region",
        )
        self.assertGreater(box.width, 0)
        self.assertGreater(box.height, 0)

    def test_all_anchors_clear_and_ordered(self):
        ys = []
        for pos in ("upper-third", "center", "lower-third"):
            box = caption_box(self.spec, position=pos)
            self.assertTrue(self.spec.rect_clear(box.x, box.y, box.x1, box.y1), pos)
            ys.append(box.y)
        self.assertTrue(ys[0] < ys[1] < ys[2])  # upper above center above lower

    def test_lower_third_narrower_than_upper_due_to_notch(self):
        upper = caption_box(self.spec, position="upper-third")
        lower = caption_box(self.spec, position="lower-third")
        self.assertLess(lower.width, upper.width)

    def test_synthetic_notch_box_clears(self):
        # a generated spec with a known lower-right notch
        with tempfile.TemporaryDirectory() as d:
            from tests._util import make_template_png
            png = Path(d) / "t.png"
            make_template_png(png, 1080, 1920, safe_rect=(40, 200, 1040, 1700),
                              notch_rect=(820, 1300, 1040, 1700))
            spec = generate_spec(str(png), profile="reels-9x16")
            box = caption_box(spec, position="lower-third")
            self.assertTrue(spec.rect_clear(box.x, box.y, box.x1, box.y1))
            self.assertLessEqual(box.x1, 820)  # cleared the notch x-start

    def test_bad_position_rejected(self):
        with self.assertRaises(ValueError):
            caption_box(self.spec, position="nope")

    # ── INI-088 Phase 3: explicit horizontal offset ──
    def test_bad_h_offset_rejected(self):
        with self.assertRaises(ValueError):
            caption_box(self.spec, h_offset="nope")

    def test_center_offset_is_frame_centered_and_clear(self):
        # center mode: box centered about the safe-area center and still clear.
        bx0, _, bx1, _ = self.spec.bounding_box
        cx = (bx0 + bx1) / 2.0
        box = caption_box(self.spec, position="lower-third", h_offset="center")
        self.assertTrue(self.spec.rect_clear(box.x, box.y, box.x1, box.y1))
        self.assertAlmostEqual(box.cx, cx, delta=2.0)  # symmetric about center

    def test_center_offset_clears_synthetic_notch(self):
        with tempfile.TemporaryDirectory() as d:
            from tests._util import make_template_png
            png = Path(d) / "t.png"
            make_template_png(png, 1080, 1920, safe_rect=(40, 200, 1040, 1700),
                              notch_rect=(820, 1300, 1040, 1700))
            spec = generate_spec(str(png), profile="reels-9x16")
            clear = caption_box(spec, position="lower-third", h_offset="clear-notch")
            center = caption_box(spec, position="lower-third", h_offset="center")
            # both clear the danger region
            self.assertTrue(spec.rect_clear(center.x, center.y, center.x1, center.y1))
            self.assertLessEqual(center.x1, 820)
            # clear-notch is the wider (or equal) of the two at a notched band
            self.assertGreaterEqual(clear.width, center.width)

    def test_upper_third_offsets_identical_no_notch(self):
        # the notch doesn't touch the upper band -> both modes agree there
        a = caption_box(self.spec, position="upper-third", h_offset="clear-notch")
        b = caption_box(self.spec, position="upper-third", h_offset="center")
        self.assertEqual(a.to_dict(), b.to_dict())


# ── Remotion props contract ───────────────────────────────────────────────────

class RemotionPropsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec_path = REPO_ROOT / "config" / "safezone" / "reels-9x16.safezone.json"
        cls.spec = SafeZoneSpec.from_json(spec_path.read_text(encoding="utf-8"))

    def _track(self):
        return CaptionTrack(source="clip.mp4", identity="dyson-hope", profile="reels-9x16",
                            cues=[Cue(0, 0.0, 1.0, ["hi", "there"], emphasis=[1]),
                                  Cue(1, 1.0, 2.0, ["bye"], keep=False)])

    def test_frames_from_seconds(self):
        self.assertEqual(seconds_to_frame(1.0, 30), 30)
        self.assertEqual(seconds_to_frame(0.5, 30), 15)

    def test_props_only_kept_cues_with_frames(self):
        style = CaptionStyle(uppercase=False)
        props = build_props_from_safezone(self._track(), style, self.spec, fps=30)
        self.assertEqual(props["schemaVersion"], 3)  # INI-089 caption-dodge (per-cue box)
        self.assertEqual(len(props["cues"]), 1)  # dropped cue excluded
        c = props["cues"][0]
        self.assertEqual(c["from"], 0)
        self.assertEqual(c["durationInFrames"], 30)
        self.assertEqual(c["emphasis"], [1])
        self.assertEqual(c["text"], "hi there")

    def test_props_uppercase_applies_to_text_and_words(self):
        style = CaptionStyle(uppercase=True)
        props = build_props_from_safezone(self._track(), style, self.spec, fps=30)
        self.assertEqual(props["cues"][0]["text"], "HI THERE")
        self.assertEqual(props["cues"][0]["words"], ["HI", "THERE"])

    def test_props_safebox_inside_safezone(self):
        props = build_props_from_safezone(self._track(), CaptionStyle(), self.spec, fps=30)
        b = props["safeBox"]
        self.assertTrue(self.spec.rect_clear(b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]))
        self.assertEqual(props["dimensions"]["width"], self.spec.image_width)

    def test_min_one_frame_duration(self):
        track = CaptionTrack(source="x", cues=[Cue(0, 0.0, 0.001, ["x"])])
        props = track_to_remotion_props(track, CaptionStyle(),
                                        caption_box(self.spec), 1080, 1920, fps=30)
        self.assertEqual(props["cues"][0]["durationInFrames"], 1)

    def test_props_json_serialisable(self):
        props = build_props_from_safezone(self._track(), CaptionStyle(), self.spec, fps=30)
        json.dumps(props)  # must not raise


# ── Remotion render command (pure argv) ───────────────────────────────────────

class KaraokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec_path = REPO_ROOT / "config" / "safezone" / "reels-9x16.safezone.json"
        cls.spec = SafeZoneSpec.from_json(spec_path.read_text(encoding="utf-8"))

    def test_chunker_captures_per_word_timings(self):
        t = words_to_transcript([("hi", 0.0, 0.4), ("there", 0.45, 0.9)])
        cues = chunk_transcript(t, CaptionStyle(min_words=2, max_words=4, max_gap_s=5, max_chars=100))
        self.assertEqual(cues[0].word_times, [(0.0, 0.4), (0.45, 0.9)])

    def test_word_times_dropped_on_count_mismatch(self):
        cue = Cue(0, 0.0, 1.0, ["a", "b", "c"], word_times=[(0.0, 0.3), (0.3, 0.6)])
        self.assertEqual(cue.word_times, [])  # 2 timings, 3 words -> dropped

    def test_karaoke_round_trip_persists_wt(self):
        track = CaptionTrack(
            source="x", karaoke=True,
            cues=[Cue(0, 0.0, 0.9, ["hi", "there"], word_times=[(0.0, 0.4), (0.45, 0.9)])],
        )
        text = track.to_yaml()
        self.assertIn("wt:", text)
        back = CaptionTrack.from_yaml(text)
        self.assertTrue(back.karaoke)
        self.assertEqual(back.cues[0].word_times, [(0.0, 0.4), (0.45, 0.9)])

    def test_non_karaoke_file_omits_wt(self):
        track = CaptionTrack(
            source="x", karaoke=False,
            cues=[Cue(0, 0.0, 0.9, ["hi", "there"], word_times=[(0.0, 0.4), (0.45, 0.9)])],
        )
        self.assertNotIn("wt:", track.to_yaml())

    def test_props_carry_karaoke_flag_and_word_timings(self):
        track = CaptionTrack(
            source="x", identity="dyson-hope", profile="reels-9x16", karaoke=True,
            cues=[Cue(0, 0.0, 1.0, ["hi", "there"], word_times=[(0.0, 0.5), (0.5, 1.0)])],
        )
        props = build_props_from_safezone(track, CaptionStyle(karaoke=True), self.spec, fps=30)
        self.assertTrue(props["karaoke"])
        wt = props["cues"][0]["wordTimings"]
        self.assertEqual(len(wt), 2)  # parallel to words
        self.assertEqual(wt[0]["from"], 0)        # relative to cue start
        self.assertEqual(wt[1]["from"], 15)       # 0.5s * 30fps

    def test_word_timings_even_split_fallback(self):
        # cue with NO captured per-word timings -> exporter even-splits
        track = CaptionTrack(source="x", cues=[Cue(0, 0.0, 1.0, ["a", "b"])])
        props = build_props_from_safezone(track, CaptionStyle(), self.spec, fps=30)
        wt = props["cues"][0]["wordTimings"]
        self.assertEqual(len(wt), 2)
        self.assertEqual(wt[0]["from"], 0)
        self.assertEqual(wt[1]["from"], 15)  # half of 1.0s at 30fps

    def test_karaoke_default_off_in_props(self):
        track = CaptionTrack(source="x", cues=[Cue(0, 0.0, 1.0, ["a", "b"])])
        props = build_props_from_safezone(track, CaptionStyle(), self.spec, fps=30)
        self.assertFalse(props["karaoke"])

    def test_style_override_enables_karaoke(self):
        s = load_caption_style(CONFIG_ROOT, identity="dyson-hope", overrides={"karaoke": True})
        self.assertTrue(s.karaoke)


class RemotionCommandTests(unittest.TestCase):
    def test_render_command_shape(self):
        cmd = remotion_render_command("work/props.json", "out/captions.mov")
        self.assertEqual(cmd[:3], ["npx", "remotion", "render"])
        self.assertTrue(any(a.startswith("--props=") for a in cmd))
        self.assertTrue(any("captions.mov" in a for a in cmd))
        self.assertIn("--codec=prores", cmd)


# ── INI-088: preview-frame verification seam (pure parts) ─────────────────────

class PreviewFrameTests(unittest.TestCase):
    @staticmethod
    def _props(n_cues):
        return {
            "dimensions": {"width": 1080, "height": 1920},
            "cues": [
                {"index": i, "startSeconds": float(i), "endSeconds": i + 1.0}
                for i in range(n_cues)
            ],
        }

    def test_empty_or_nonpositive_yields_none(self):
        self.assertEqual(preview_frame_times(self._props(0), 3), [])
        self.assertEqual(preview_frame_times(self._props(5), 0), [])

    def test_single_frame_is_a_midpoint(self):
        times = preview_frame_times(self._props(5), 1)
        self.assertEqual(len(times), 1)
        # midpoint of some cue -> ends in .5
        self.assertAlmostEqual(times[0] % 1.0, 0.5, places=3)

    def test_clamped_to_cue_count_and_spans_range(self):
        times = preview_frame_times(self._props(3), 10)
        self.assertEqual(len(times), 3)             # clamped to 3 cues
        self.assertEqual(times[0], 0.5)             # first cue midpoint
        self.assertEqual(times[-1], 2.5)            # last cue midpoint

    def test_evenly_spaced_across_cues(self):
        times = preview_frame_times(self._props(9), 3)
        self.assertEqual(times, [0.5, 4.5, 8.5])    # first / middle / last

    def test_sorted_and_deduplicated(self):
        times = preview_frame_times(self._props(6), 4)
        self.assertEqual(times, sorted(times))
        self.assertEqual(len(times), len(set(times)))

    def test_frame_extract_command_shape(self):
        cmd = frame_extract_command("layers/c.mov", 1.25, "out/p.png", 1080, 1920,
                                    background="#808080")
        self.assertEqual(cmd[0], "ffmpeg")
        self.assertIn("-ss", cmd)
        self.assertIn("1.250", cmd)                 # seeked timestamp
        self.assertIn("layers/c.mov", cmd)
        self.assertTrue(any("color=c=#808080:s=1080x1920" in a for a in cmd))
        self.assertEqual(cmd[-1], "out/p.png")
        # color (bg, input 1) under overlay (input 0)
        self.assertTrue(any("[1:v][0:v]overlay" in a for a in cmd))


if __name__ == "__main__":
    unittest.main()
