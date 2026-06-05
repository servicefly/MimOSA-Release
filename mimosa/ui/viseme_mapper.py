"""Phoneme -> viseme mapping for MimOSA lip-sync (M3.2).

A *viseme* is the visual equivalent of a phoneme: the mouth shape produced
while articulating a sound. Many phonemes share the same mouth shape (e.g.
``p``, ``b`` and ``m`` are all closed-lip "bilabials"), so a compact set of
~10 visemes is enough to drive convincing lip-sync.

This module is **pure** -- it has no GTK/Cairo/audio dependencies and is safe to
import on a headless machine. It owns:

* :class:`Viseme` -- the canonical viseme set.
* :data:`DEFAULT_PHONEME_TO_VISEME` -- an IPA / eSpeak phoneme -> viseme table
  (Piper emits eSpeak-ng IPA phonemes).
* :func:`phoneme_to_viseme` -- robust single-phoneme lookup.
* :class:`VisemeMapper` -- a configurable mapper wrapping a (possibly custom)
  table.

Design notes
------------
* The table is keyed by the *base* phoneme symbol. Diacritics, stress marks and
  length markers (``ˈ ˌ ː ʰ`` ...) are stripped before lookup so we don't have
  to enumerate every decorated variant.
* Lookups never raise: an unknown phoneme falls back to a sensible neutral
  viseme so the animation degrades gracefully.
"""

from __future__ import annotations

import enum
import unicodedata
from typing import Dict, Iterable, Mapping, Optional


class Viseme(enum.Enum):
    """Canonical mouth shapes used by the renderer.

    Ten shapes (plus silence) cover English speech well while staying cheap to
    animate. Each value is a short stable string so timelines serialize nicely.
    """

    SILENCE = "silence"        # rest / closed neutral (pauses, end of speech)
    CLOSED = "closed"          # bilabial p, b, m -- lips pressed together
    LABIODENTAL = "labiodental"  # f, v -- lower lip to upper teeth
    DENTAL = "dental"          # th -- tongue between teeth
    ALVEOLAR = "alveolar"      # t, d, n, l, s, z -- tongue tip, slight open
    VELAR = "velar"            # k, g, ng -- back of tongue, mid open
    AFFRICATE = "affricate"    # ch, j, sh, zh -- rounded/puckered
    WIDE = "wide"              # ee, i, e -- spread lips (smile)
    OPEN = "open"              # ah, aa -- jaw dropped wide open
    ROUNDED = "rounded"        # oo, o, w -- rounded protruding lips
    MID = "mid"                # uh, schwa, r -- relaxed mid-open

    @property
    def openness(self) -> float:
        """Rough 0..1 jaw-openness hint (used by the fallback animator)."""
        return _OPENNESS.get(self, 0.3)


#: Approximate openness per viseme (0 = closed, 1 = fully open). Pure data.
_OPENNESS: Dict[Viseme, float] = {
    Viseme.SILENCE: 0.0,
    Viseme.CLOSED: 0.02,
    Viseme.LABIODENTAL: 0.12,
    Viseme.DENTAL: 0.2,
    Viseme.ALVEOLAR: 0.28,
    Viseme.VELAR: 0.4,
    Viseme.AFFRICATE: 0.32,
    Viseme.WIDE: 0.45,
    Viseme.OPEN: 0.95,
    Viseme.ROUNDED: 0.55,
    Viseme.MID: 0.5,
}


def _strip_decorations(phoneme: str) -> str:
    """Remove stress/length/tie marks and combining diacritics from a phoneme.

    eSpeak/Piper phonemes carry primary/secondary stress (``ˈ ˌ``), length
    (``ː``), aspiration (``ʰ``), syllabic/tie marks, and combining accents. We
    drop these so the base symbol matches the table.
    """
    if not phoneme:
        return ""
    # Decompose so combining marks become separate code points we can drop.
    decomposed = unicodedata.normalize("NFD", phoneme)
    drop = {
        "ˈ", "ˌ", "ː", "ˑ", "ʰ", "ʲ", "ʷ", "ˠ", "ˤ", "̃", "̩", "̯",
        "‿", "͡", "͜", ".", "ˀ", "ʼ", " ", "\t",
    }
    out = []
    for ch in decomposed:
        if ch in drop:
            continue
        if unicodedata.combining(ch):
            continue
        out.append(ch)
    return "".join(out)


#: IPA / eSpeak-ng phoneme -> viseme. Covers the English phoneme inventory plus
#: common ASCII fallbacks (so plain-letter phoneme streams also map sensibly).
DEFAULT_PHONEME_TO_VISEME: Dict[str, Viseme] = {
    # --- silence / boundaries ---
    "": Viseme.SILENCE,
    "_": Viseme.SILENCE,
    "#": Viseme.SILENCE,
    "sp": Viseme.SILENCE,
    "sil": Viseme.SILENCE,

    # --- bilabials (closed lips) ---
    "p": Viseme.CLOSED,
    "b": Viseme.CLOSED,
    "m": Viseme.CLOSED,

    # --- labiodentals ---
    "f": Viseme.LABIODENTAL,
    "v": Viseme.LABIODENTAL,

    # --- dentals ---
    "θ": Viseme.DENTAL,   # think
    "ð": Viseme.DENTAL,   # this

    # --- alveolars / sibilants (tongue tip) ---
    "t": Viseme.ALVEOLAR,
    "d": Viseme.ALVEOLAR,
    "n": Viseme.ALVEOLAR,
    "l": Viseme.ALVEOLAR,
    "s": Viseme.ALVEOLAR,
    "z": Viseme.ALVEOLAR,
    "ɾ": Viseme.ALVEOLAR,  # flap

    # --- velars ---
    "k": Viseme.VELAR,
    "g": Viseme.VELAR,
    "ɡ": Viseme.VELAR,    # IPA script g
    "ŋ": Viseme.VELAR,    # sing
    "h": Viseme.VELAR,

    # --- post-alveolar affricates/fricatives (rounded) ---
    "ʃ": Viseme.AFFRICATE,  # ship
    "ʒ": Viseme.AFFRICATE,  # measure
    "tʃ": Viseme.AFFRICATE,  # church
    "dʒ": Viseme.AFFRICATE,  # judge
    "j": Viseme.WIDE,        # yes (palatal glide)

    # --- approximants ---
    "ɹ": Viseme.MID,   # red (English r)
    "r": Viseme.MID,
    "w": Viseme.ROUNDED,

    # --- close/front vowels (wide/spread) ---
    "i": Viseme.WIDE,
    "ɪ": Viseme.WIDE,
    "iː": Viseme.WIDE,
    "e": Viseme.WIDE,
    "ɛ": Viseme.WIDE,
    "eɪ": Viseme.WIDE,
    "æ": Viseme.WIDE,

    # --- open vowels ---
    "a": Viseme.OPEN,
    "ɑ": Viseme.OPEN,
    "ɑː": Viseme.OPEN,
    "ʌ": Viseme.OPEN,
    "aɪ": Viseme.OPEN,
    "aʊ": Viseme.OPEN,

    # --- rounded / back vowels ---
    "u": Viseme.ROUNDED,
    "uː": Viseme.ROUNDED,
    "ʊ": Viseme.ROUNDED,
    "o": Viseme.ROUNDED,
    "oʊ": Viseme.ROUNDED,
    "ɔ": Viseme.ROUNDED,
    "ɔː": Viseme.ROUNDED,
    "ɔɪ": Viseme.ROUNDED,

    # --- mid / central ---
    "ə": Viseme.MID,   # schwa
    "ɚ": Viseme.MID,
    "ɜ": Viseme.MID,
    "ɝ": Viseme.MID,
}

#: ASCII letter fallbacks, used only when an unknown symbol is a single ASCII
#: letter (e.g. a degraded phoneme stream). Keeps lip-sync plausible.
_ASCII_VOWELS = {"a": Viseme.OPEN, "e": Viseme.WIDE, "i": Viseme.WIDE,
                 "o": Viseme.ROUNDED, "u": Viseme.ROUNDED, "y": Viseme.WIDE}


def phoneme_to_viseme(
    phoneme: str,
    table: Optional[Mapping[str, Viseme]] = None,
    default: Viseme = Viseme.MID,
) -> Viseme:
    """Map a single ``phoneme`` to a :class:`Viseme`, never raising.

    Lookup order:

    1. Exact match on the raw symbol.
    2. Match after stripping stress/length/diacritic decorations.
    3. First-character match on the stripped symbol (handles diphthongs/clusters
       not in the table, e.g. ``"aɪ"`` -> ``"a"``).
    4. ASCII vowel/consonant fallback.
    5. ``default``.
    """
    tbl = table if table is not None else DEFAULT_PHONEME_TO_VISEME
    if phoneme is None:
        return Viseme.SILENCE
    raw = str(phoneme)
    if raw in tbl:
        return tbl[raw]

    stripped = _strip_decorations(raw)
    if stripped == "":
        return Viseme.SILENCE
    if stripped in tbl:
        return tbl[stripped]

    # Try the leading base character (diphthongs / unlisted clusters).
    first = stripped[0]
    if first in tbl:
        return tbl[first]

    low = first.lower()
    if low in _ASCII_VOWELS:
        return _ASCII_VOWELS[low]
    if low.isalpha():
        # An unmapped consonant: a neutral alveolar reads better than "open".
        return Viseme.ALVEOLAR
    return default


class VisemeMapper:
    """Configurable phoneme->viseme mapper.

    Args:
        table: Optional custom mapping (merged over the default table). Lets a
            user or voice supply overrides without losing the built-ins.
        default: Viseme returned when nothing matches.
    """

    def __init__(
        self,
        table: Optional[Mapping[str, Viseme]] = None,
        default: Viseme = Viseme.MID,
    ) -> None:
        merged: Dict[str, Viseme] = dict(DEFAULT_PHONEME_TO_VISEME)
        if table:
            merged.update(table)
        self.table = merged
        self.default = default

    def map_one(self, phoneme: str) -> Viseme:
        """Map a single phoneme (see :func:`phoneme_to_viseme`)."""
        return phoneme_to_viseme(phoneme, self.table, self.default)

    def map_many(self, phonemes: Iterable[str]) -> list:
        """Map an iterable of phonemes to a list of :class:`Viseme`."""
        return [self.map_one(p) for p in phonemes]

    @property
    def visemes(self) -> tuple:
        """The full set of available visemes."""
        return tuple(Viseme)
