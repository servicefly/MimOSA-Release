"""Tests for mimosa.ui.viseme_mapper -- phoneme -> viseme mapping (M3.2).

Pure data/logic; no GTK, audio, or display required.
"""

import pytest

from mimosa.ui.viseme_mapper import (
    DEFAULT_PHONEME_TO_VISEME,
    Viseme,
    VisemeMapper,
    _strip_decorations,
    phoneme_to_viseme,
)


class TestVisemeEnum:
    def test_values_are_stable_strings(self):
        assert Viseme.OPEN.value == "open"
        assert Viseme.CLOSED.value == "closed"
        assert Viseme.SILENCE.value == "silence"

    def test_openness_monotonic_extremes(self):
        assert Viseme.SILENCE.openness == 0.0
        assert Viseme.OPEN.openness > Viseme.CLOSED.openness
        assert Viseme.OPEN.openness == pytest.approx(0.95)

    def test_ten_plus_one_visemes(self):
        # 10 mouth shapes + silence.
        assert len(list(Viseme)) == 11


class TestStripDecorations:
    def test_strips_stress_and_length(self):
        assert _strip_decorations("ˈɑː") == "ɑ"
        assert _strip_decorations("ˌeɪ") == "eɪ"

    def test_strips_combining_marks(self):
        # nasalized vowel -> base vowel
        assert _strip_decorations("ã") == "a"

    def test_empty(self):
        assert _strip_decorations("") == ""
        assert _strip_decorations("ˈ") == ""


class TestPhonemeToViseme:
    @pytest.mark.parametrize("ph,expected", [
        ("p", Viseme.CLOSED),
        ("b", Viseme.CLOSED),
        ("m", Viseme.CLOSED),
        ("f", Viseme.LABIODENTAL),
        ("v", Viseme.LABIODENTAL),
        ("θ", Viseme.DENTAL),
        ("ð", Viseme.DENTAL),
        ("t", Viseme.ALVEOLAR),
        ("s", Viseme.ALVEOLAR),
        ("z", Viseme.ALVEOLAR),
        ("k", Viseme.VELAR),
        ("g", Viseme.VELAR),
        ("ŋ", Viseme.VELAR),
        ("ʃ", Viseme.AFFRICATE),
        ("i", Viseme.WIDE),
        ("ɪ", Viseme.WIDE),
        ("æ", Viseme.WIDE),
        ("ɑ", Viseme.OPEN),
        ("ʌ", Viseme.OPEN),
        ("u", Viseme.ROUNDED),
        ("ʊ", Viseme.ROUNDED),
        ("ɔ", Viseme.ROUNDED),
        ("w", Viseme.ROUNDED),
        ("ə", Viseme.MID),
    ])
    def test_known_phonemes(self, ph, expected):
        assert phoneme_to_viseme(ph) == expected

    def test_diphthong_falls_to_first_char(self):
        assert phoneme_to_viseme("aɪ") == Viseme.OPEN
        assert phoneme_to_viseme("oʊ") == Viseme.ROUNDED

    def test_decorated_phoneme(self):
        assert phoneme_to_viseme("ˈɑː") == Viseme.OPEN
        assert phoneme_to_viseme("iː") == Viseme.WIDE

    def test_silence_symbols(self):
        for s in ("", "_", "#", "sp", "sil"):
            assert phoneme_to_viseme(s) == Viseme.SILENCE

    def test_none_is_silence(self):
        assert phoneme_to_viseme(None) == Viseme.SILENCE

    def test_unknown_ascii_vowel_fallback(self):
        assert phoneme_to_viseme("A") == Viseme.OPEN  # case-insensitive
        assert phoneme_to_viseme("E") == Viseme.WIDE

    def test_unknown_consonant_fallback_alveolar(self):
        # An unmapped letter consonant reads better as a neutral alveolar.
        assert phoneme_to_viseme(" q") == Viseme.ALVEOLAR or \
            phoneme_to_viseme("q") == Viseme.ALVEOLAR

    def test_unknown_symbol_default(self):
        assert phoneme_to_viseme("@@@", default=Viseme.MID) == Viseme.MID

    def test_custom_table_override(self):
        custom = {"p": Viseme.OPEN}
        assert phoneme_to_viseme("p", table=custom) == Viseme.OPEN


class TestVisemeMapper:
    def test_default_table_merged(self):
        m = VisemeMapper()
        assert m.map_one("p") == Viseme.CLOSED

    def test_custom_overrides_default(self):
        m = VisemeMapper(table={"p": Viseme.OPEN})
        assert m.map_one("p") == Viseme.OPEN
        # built-ins still present
        assert m.map_one("m") == Viseme.CLOSED

    def test_map_many(self):
        m = VisemeMapper()
        out = m.map_many(["h", "ə", "l", "oʊ"])
        assert out == [Viseme.VELAR, Viseme.MID, Viseme.ALVEOLAR, Viseme.ROUNDED]

    def test_visemes_property(self):
        m = VisemeMapper()
        assert Viseme.OPEN in m.visemes
        assert len(m.visemes) == 11

    def test_default_table_is_not_mutated(self):
        before = dict(DEFAULT_PHONEME_TO_VISEME)
        VisemeMapper(table={"zzz": Viseme.OPEN})
        assert DEFAULT_PHONEME_TO_VISEME == before
