"""Tests for the audio data augmenter (Milestone 2).

We pass ``enforce_minimum=False`` so tiny stub datasets are accepted, and inject
a stub ``synthesize_fn`` so no TTS engine runs.
"""

from __future__ import annotations

import os

import pytest

from mimosa.training.data_augmenter import AugmentationResult, DataAugmenter
from tests.conftest import make_wav_bytes


def _write_positives(tmp_path, count=3):
    paths = []
    for i in range(count):
        p = tmp_path / f"pos_{i}.wav"
        p.write_bytes(make_wav_bytes())
        paths.append(str(p))
    return paths


def _augmenter():
    return DataAugmenter(synthesize_fn=lambda t, v, s: make_wav_bytes(), seed=1)


def test_augment_produces_positive_and_negative_sets(tmp_path):
    positives = _write_positives(tmp_path)
    aug = _augmenter()
    result = aug.augment(
        positives,
        output_dir=str(tmp_path / "out"),
        target_total=20,
        enforce_minimum=False,
    )
    assert isinstance(result, AugmentationResult)
    assert result.ok is True
    assert result.positive_paths
    assert result.negative_paths
    assert os.path.isdir(result.positives_dir)
    assert os.path.isdir(result.negatives_dir)


def test_augment_outputs_exist_on_disk(tmp_path):
    positives = _write_positives(tmp_path)
    aug = _augmenter()
    result = aug.augment(
        positives,
        output_dir=str(tmp_path / "out"),
        target_total=16,
        enforce_minimum=False,
    )
    for path in result.positive_paths + result.negative_paths:
        assert os.path.exists(path)


def test_augment_empty_positives_is_not_ok(tmp_path):
    aug = _augmenter()
    result = aug.augment(
        [],
        output_dir=str(tmp_path / "out"),
        target_total=10,
        enforce_minimum=False,
    )
    assert result.ok is False
    assert result.error


def test_augment_is_deterministic_with_seed(tmp_path):
    positives = _write_positives(tmp_path)
    a = DataAugmenter(synthesize_fn=lambda t, v, s: make_wav_bytes(), seed=42)
    b = DataAugmenter(synthesize_fn=lambda t, v, s: make_wav_bytes(), seed=42)
    ra = a.augment(positives, output_dir=str(tmp_path / "a"),
                   target_total=16, enforce_minimum=False)
    rb = b.augment(positives, output_dir=str(tmp_path / "b"),
                   target_total=16, enforce_minimum=False)
    assert len(ra.positive_paths) == len(rb.positive_paths)
    assert len(ra.negative_paths) == len(rb.negative_paths)


def test_augment_reports_progress(tmp_path):
    positives = _write_positives(tmp_path)
    seen = []
    aug = _augmenter()
    aug.augment(
        positives,
        output_dir=str(tmp_path / "out"),
        target_total=16,
        enforce_minimum=False,
        on_progress=lambda done, total, msg: seen.append((done, total)),
    )
    assert seen


def test_augment_supports_cancellation(tmp_path):
    positives = _write_positives(tmp_path)
    aug = _augmenter()
    result = aug.augment(
        positives,
        output_dir=str(tmp_path / "out"),
        target_total=200,
        enforce_minimum=False,
        should_cancel=lambda: True,
    )
    # Must not raise; result is well-formed regardless of cancellation timing.
    assert isinstance(result, AugmentationResult)
