"""Caption chunking — the timing layer. Pure, fully unit-tested.

Turns a word-level :class:`~video_pipeline.roughcut.transcript.Transcript` into a
list of :class:`~video_pipeline.captions.cue.Cue` — short on-screen captions of
2–4 words (configurable), timed from the word timestamps. No styling, no I/O,
no native deps: timestamps + text in, cues out.

Grouping rules (in priority order), all from the :class:`CaptionStyle`:

  1. **Glossary correction first.** Each word is run through the merged glossary
     (global + identity) so proper nouns land correctly on the first pass — the
     DoD item. Multi-word corrections (``"sigil zero" -> "SIGIL.ZERO"``) collapse
     to a single token whose timing spans the originals.
  2. **Hard breaks** end a cue regardless of word count: sentence-final
     punctuation (``. ! ?``) on the current word, or an inter-word gap to the
     next word ``>= max_gap_s`` (a natural pause).
  3. **Soft cap.** A cue closes when it reaches ``max_words`` or adding the next
     word would exceed ``max_chars``.
  4. **Min words.** Below ``min_words`` a cue only closes on a hard break or the
     end of the transcript — short trailing fragments are tolerated, never
     forced mid-phrase.

Emphasis: when ``emphasize_glossary_terms`` is set, any cue word equal (case-
insensitively) to a glossary canonical ``term`` is flagged for accenting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from ..roughcut.transcript import Transcript, Word
from .cue import Cue
from .style import CaptionStyle

_SENTENCE_END = re.compile(r"[.!?]+[\")\]]?$")
_WORD = re.compile(r"\b[\w'-]+\b", re.UNICODE)


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search(text.strip()))


def apply_glossary_to_words(
    words: List[Word], glossary=None
) -> List[Word]:
    """Apply glossary mishear->canonical corrections across the word stream.

    Single-word corrections rewrite a word's text in place (timing unchanged).
    Multi-word corrections (the key has spaces) collapse the matched run into one
    word spanning the run's start..end. Whole-word, case-insensitive, longest-key
    first so multi-word fixes win. Returns a new list; inputs are not mutated.
    """
    if glossary is None or not getattr(glossary, "corrections", None):
        return list(words)

    # Order corrections longest-first (by token count, then char length) so
    # "sigil dot zero" wins over "sigil".
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


@dataclass
class _Builder:
    style: CaptionStyle
    terms_lower: frozenset

    def emphasis_for(self, words: List[str]) -> List[int]:
        if not self.style.emphasize_glossary_terms or not self.terms_lower:
            return []
        return [
            i for i, w in enumerate(words)
            if _norm_term(w) in self.terms_lower
        ]


def _norm_term(text: str) -> str:
    """Lowercase, trim surrounding punctuation — for glossary-term matching."""
    return text.strip().strip(".,!?;:\"')([]").lower()


def chunk_transcript(
    transcript: Transcript,
    style: Optional[CaptionStyle] = None,
    glossary=None,
) -> List[Cue]:
    """Group a word-level transcript into caption cues. Pure."""
    style = style or CaptionStyle()
    words = apply_glossary_to_words(list(transcript.words), glossary)

    terms_lower = frozenset(
        _norm_term(t) for t in (getattr(glossary, "terms", None) or [])
    )
    builder = _Builder(style=style, terms_lower=terms_lower)

    cues: List[Cue] = []
    cur: List[Word] = []

    def char_len(ws: List[Word]) -> int:
        return len(" ".join(w.text for w in ws))

    def flush():
        if not cur:
            return
        texts = [w.text for w in cur]
        cues.append(
            Cue(
                index=len(cues),
                start=cur[0].start,
                end=cur[-1].end,
                words=texts,
                emphasis=builder.emphasis_for(texts),
                keep=True,
            )
        )
        cur.clear()

    for i, w in enumerate(words):
        # Would adding this word overflow the char cap? Close first (unless empty
        # — a single over-long word must still go somewhere).
        if cur and char_len(cur + [w]) > style.max_chars:
            flush()

        cur.append(w)

        hard_break = _ends_sentence(w.text)
        if not hard_break and i + 1 < len(words):
            gap = words[i + 1].start - w.end
            if gap >= style.max_gap_s:
                hard_break = True

        at_cap = len(cur) >= style.max_words
        enough = len(cur) >= style.min_words

        if hard_break or (at_cap and enough):
            flush()

    flush()
    return cues
