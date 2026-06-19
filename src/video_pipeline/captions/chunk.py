"""Caption chunking — the timing layer. Pure, fully unit-tested.

Turns a word-level :class:`~video_pipeline.roughcut.transcript.Transcript` into a
list of :class:`~video_pipeline.captions.cue.Cue` — short on-screen captions timed
from the word timestamps. No styling, no I/O, no native deps.

Two-stage breaking:

  1. **Hard breaks** end a cue unconditionally: sentence-final punctuation
     (``. ! ?``) on a word, or an inter-word gap ``>= max_gap_s`` (a real pause).
     These split the stream into *spans*.

  2. **Within each span**, a small dynamic program chooses where to break so the
     captions read the way a human would cut them — instead of greedily filling to
     ``max_words`` and leaving a one-word widow. It minimises a cost that trades
     off four things:

       - **fewer cues** (a per-cue cost, so it does not over-fragment);
       - **balance** — cue lengths near a target (``target_words``, default the
         midpoint of the ``min_words``–``max_words`` range), so widths are even;
       - **no widows** — a penalty for a cue below ``min_words`` (heaviest at one
         word). Disabled when ``min_words == 1`` (then single-word cues are wanted);
       - **phrase-aware breaks** — a bonus for starting a cue *before* a function
         word (article / preposition / conjunction / clause-pronoun: "the", "and",
         "I"…) and a penalty for breaking *after* one (which strands it). A comma
         on the previous word is a bonus break point.

     ``max_words`` and ``max_chars`` are hard limits; the rest are soft costs.

Setting ``min_words == max_words == 1`` collapses this to word-by-word captions:
every word is its own cue, and the balance/widow/phrase terms have nothing to act
on. The same range therefore expresses single-word, phrase (2–4), and anything in
between — there is no separate "mode".

Glossary correction runs first (proper nouns land right on the first pass), and
glossary canonical terms are flagged as emphasis words.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..roughcut.transcript import Transcript, Word
from .cue import Cue
from .style import CaptionStyle

_SENTENCE_END = re.compile(r"[.!?]+[\")\]]?$")

# Function words that begin a phrase: good to break *before* one, bad to break
# *after* one. English seed; override per identity via the ``break_words`` config
# key if needed. Stored normalised (lowercase, punctuation-stripped).
DEFAULT_BREAK_WORDS = frozenset({
    # articles
    "a", "an", "the",
    # conjunctions (coordinating + common subordinating)
    "and", "but", "or", "nor", "so", "yet",
    "because", "although", "though", "while", "whereas", "if", "unless",
    "that", "which", "who", "whom", "whose", "when", "where", "as",
    # prepositions
    "of", "to", "in", "on", "at", "for", "with", "from", "by", "about",
    "into", "onto", "over", "under", "after", "before", "between", "through",
    "during", "without", "within", "against", "toward", "towards", "up", "out",
    # clause-starting pronouns
    "i", "you", "he", "she", "we", "they", "it",
})

# Cost weights. Tuned so balanced, phrase-aligned, widow-free cuts win; exposed
# here (not in per-project config) to keep the user-facing surface to the range.
_CUE_COST = 2.0            # per-cue: discourages over-fragmenting
_BALANCE_W = 1.0           # weight on squared deviation from target length
_WIDOW_W = 3.0             # per word below min_words (min_words > 1 only)
_BREAK_BEFORE_BONUS = 1.5  # starting a cue before a function word
_BREAK_AFTER_PENALTY = 2.0 # breaking right after a function word (strands it)
_COMMA_BONUS = 1.0         # breaking after a comma/semicolon/colon


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def _norm_term(text: str) -> str:
    """Lowercase, trim surrounding punctuation — for glossary/function matching."""
    return text.strip().strip(".,!?;:\"')([]").lower()


# ── glossary correction ───────────────────────────────────────────────────────

def apply_glossary_to_words(words: List[Word], glossary=None) -> List[Word]:
    """Apply glossary mishear->canonical corrections across the word stream.

    Single-word corrections rewrite a word's text (timing unchanged). Multi-word
    corrections (the key has spaces) collapse the matched run into one word
    spanning the run's start..end. Whole-word, case-insensitive, longest-key
    first. Returns a new list; inputs are not mutated.
    """
    if glossary is None or not getattr(glossary, "corrections", None):
        return list(words)

    items = sorted(
        glossary.corrections.items(),
        key=lambda kv: (len(kv[0].split()), len(kv[0])),
        reverse=True,
    )

    out: List[Word] = list(words)
    for wrong, right in items:
        tokens = wrong.lower().split()
        n = len(tokens)
        if n == 0:
            continue
        i = 0
        merged: List[Word] = []
        while i < len(out):
            window = out[i : i + n]
            if len(window) == n and all(
                window[j].normalized() == tokens[j] for j in range(n)
            ):
                merged.append(
                    Word(text=right, start=window[0].start, end=window[-1].end)
                )
                i += n
            else:
                merged.append(out[i])
                i += 1
        out = merged
    return out


# ── span splitting (hard breaks) ──────────────────────────────────────────────

def _split_spans(words: List[Word], max_gap_s: float) -> List[List[Word]]:
    """Split on sentence-final punctuation and pauses >= max_gap_s."""
    spans: List[List[Word]] = []
    cur: List[Word] = []
    for i, w in enumerate(words):
        cur.append(w)
        hard = _ends_sentence(w.text)
        if not hard and i + 1 < len(words):
            if words[i + 1].start - w.end >= max_gap_s:
                hard = True
        if hard:
            spans.append(cur)
            cur = []
    if cur:
        spans.append(cur)
    return spans


# ── within-span breaking (DP) ─────────────────────────────────────────────────

def _partition_span(
    span: List[Word], style: CaptionStyle, breakset, target: int
) -> List[Tuple[int, int]]:
    """Optimal (min-cost) partition of one span into cues. Returns (start, end)
    word-index ranges. ``max_words`` and ``max_chars`` are hard; the rest soft."""
    n = len(span)
    INF = float("inf")
    dp = [INF] * (n + 1)
    back = [-1] * (n + 1)
    dp[0] = 0.0

    for i in range(1, n + 1):
        jmin = max(0, i - style.max_words)
        for j in range(i - 1, jmin - 1, -1):
            length = i - j
            text_len = sum(len(span[k].text) for k in range(j, i)) + (length - 1)
            # max_chars is hard — but a single word longer than the cap must still
            # be placed somewhere (cannot split a word).
            if text_len > style.max_chars and length > 1:
                continue

            cost = dp[j] + _CUE_COST
            cost += _BALANCE_W * (length - target) ** 2
            if style.min_words > 1 and length < style.min_words:
                cost += _WIDOW_W * (style.min_words - length)

            # phrase cost at the cue's start boundary (interior only)
            if j > 0:
                first = span[j].normalized()
                prev = span[j - 1].normalized()
                if first in breakset:
                    cost -= _BREAK_BEFORE_BONUS
                if prev in breakset:
                    cost += _BREAK_AFTER_PENALTY
                if span[j - 1].text.rstrip().endswith((",", ";", ":")):
                    cost -= _COMMA_BONUS

            if cost < dp[i]:
                dp[i] = cost
                back[i] = j

    cuts: List[Tuple[int, int]] = []
    i = n
    while i > 0:
        j = back[i]
        cuts.append((j, i))
        i = j
    cuts.reverse()
    return cuts


def _target_words(style: CaptionStyle) -> int:
    if style.target_words and style.target_words > 0:
        return style.target_words
    return max(1, round((style.min_words + style.max_words) / 2))


# ── public API ────────────────────────────────────────────────────────────────

def chunk_transcript(
    transcript: Transcript,
    style: Optional[CaptionStyle] = None,
    glossary=None,
) -> List[Cue]:
    """Group a word-level transcript into caption cues. Pure."""
    style = style or CaptionStyle()
    words = apply_glossary_to_words(list(transcript.words), glossary)
    if not words:
        return []

    breakset = (
        frozenset(_norm_term(w) for w in style.break_words)
        if style.break_words
        else DEFAULT_BREAK_WORDS
    )
    terms_lower = frozenset(
        _norm_term(t) for t in (getattr(glossary, "terms", None) or [])
    )
    do_emphasis = style.emphasize_glossary_terms and bool(terms_lower)
    target = _target_words(style)

    cues: List[Cue] = []
    for span in _split_spans(words, style.max_gap_s):
        for a, b in _partition_span(span, style, breakset, target):
            seg = span[a:b]
            texts = [w.text for w in seg]
            emphasis = (
                [k for k, t in enumerate(texts) if _norm_term(t) in terms_lower]
                if do_emphasis
                else []
            )
            cues.append(
                Cue(
                    index=len(cues),
                    start=seg[0].start,
                    end=seg[-1].end,
                    words=texts,
                    emphasis=emphasis,
                    keep=True,
                )
            )
    return cues
