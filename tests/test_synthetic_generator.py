"""Tests for the synthetic positive-sample generator (Milestone 2).

A stub ``synthesize_fn`` is injected so no Piper/TTS engine ever runs.
"""

from __future__ import annotations

import os

import pytest

from mimosa.training.synthetic_generator import (
    GenerationResult,
    SyntheticGenerator,
)
from tests.conftest import make_wav_bytes


def _stub_synth_factory(calls):
    def _synth(text, voice, speed):
        calls.append((text, voice, speed))
        return make_wav_bytes()
    return _synth


def test_generate_produces_samples(tmp_path):
    calls = []
    gen = SyntheticGenerator(synthesize_fn=_stub_synth_factory(calls))
    result = gen.generate(
        "Jarvis", output_dir=str(tmp_path), target_samples=6
    )
    assert isinstance(result, GenerationResult)
    assert result.ok is True
    assert result.error == "" or result.error is None
    assert len(result.sample_paths) > 0
    for path in result.sample_paths:
        assert os.path.exists(path)
    assert calls  # the stub synth was actually used


def test_generate_records_voices_used(tmp_path):
    gen = SyntheticGenerator(synthesize_fn=_stub_synth_factory([]))
    result = gen.generate(
        "Jarvis", gender="female", output_dir=str(tmp_path), target_samples=6
    )
    assert result.voices_used


def test_generate_with_failing_synth_is_not_ok(tmp_path):
    def _boom(text, voice, speed):
        raise RuntimeError("no TTS available")

    gen = SyntheticGenerator(synthesize_fn=_boom)
    result = gen.generate(
        "Jarvis", output_dir=str(tmp_path), target_samples=4
    )
    assert result.ok is False
    assert result.error  # explains the failure
    assert result.sample_paths == []


def test_generate_supports_cancellation(tmp_path):
    gen = SyntheticGenerator(synthesize_fn=_stub_synth_factory([]))
    result = gen.generate(
        "Jarvis",
        output_dir=str(tmp_path),
        target_samples=50,
        should_cancel=lambda: True,
    )
    # Cancelling immediately yields no (or very few) samples but never raises.
    assert isinstance(result, GenerationResult)


def test_generate_reports_progress(tmp_path):
    seen = []

    def _on_progress(done, total, msg):
        seen.append((done, total, msg))

    gen = SyntheticGenerator(synthesize_fn=_stub_synth_factory([]))
    gen.generate(
        "Jarvis",
        output_dir=str(tmp_path),
        target_samples=6,
        on_progress=_on_progress,
    )
    assert seen  # progress callback fired
