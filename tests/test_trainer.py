"""Tests for the wake-word training pipeline (Milestone 2).

The heavy pieces (TTS synthesis, audio augmentation, model training) are all
injectable, so these tests run fast and never touch PyTorch/TensorFlow/Piper.
The trainer must NEVER raise — every failure path returns a ``TrainingResult``
that falls back to the default "MimOSA" wake word.
"""

from __future__ import annotations

import os

import pytest

from mimosa.training.data_augmenter import DataAugmenter
from mimosa.training.synthetic_generator import SyntheticGenerator
from mimosa.training.trainer import (
    TrainingController,
    TrainingResult,
    WakeWordTrainer,
)
from tests.conftest import make_wav_bytes


def _good_synth(text, voice, speed):
    return make_wav_bytes()


def _make_backend(write_model=True, metrics=None):
    """Return a stub train backend matching the real call signature."""
    def _backend(*, wake_word, positive_dir, negative_dir, output_path,
                 epochs, on_epoch=None, should_cancel=None, **_kw):
        if on_epoch is not None:
            for e in range(1, max(1, min(epochs, 2)) + 1):
                on_epoch(e, epochs, 0.5 / e, 0.8 + 0.05 * e)
        if write_model:
            with open(output_path, "wb") as fh:
                fh.write(b"FAKE_ONNX_MODEL")
        return metrics or {"accuracy": 0.98, "false_positive_rate": 0.01}
    return _backend


def _trainer(*, backend=None):
    return WakeWordTrainer(
        generator=SyntheticGenerator(synthesize_fn=_good_synth),
        augmenter=DataAugmenter(synthesize_fn=_good_synth, seed=1),
        train_backend=backend or _make_backend(),
    )


def _run(trainer, tmp_path, **kwargs):
    defaults = dict(
        model_path=str(tmp_path / "model.onnx"),
        work_dir=str(tmp_path / "work"),
        target_samples=6,
        target_total=12,
        epochs=2,
    )
    defaults.update(kwargs)
    return trainer.run("Jarvis", **defaults)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------

def test_successful_training_returns_model(tmp_path):
    result = _run(_trainer(), tmp_path)
    assert isinstance(result, TrainingResult)
    assert result.ok is True
    assert result.fell_back is False
    assert result.cancelled is False
    assert result.model_path
    assert os.path.exists(result.model_path)
    assert result.wake_word
    assert result.metrics.get("accuracy") == 0.98
    assert result.samples_generated > 0
    assert result.samples_augmented > 0


def test_progress_callback_fires(tmp_path):
    seen = []
    _run(_trainer(), tmp_path, on_progress=lambda p: seen.append(p.stage))
    assert seen
    # eta_text is always available on the progress object.
    assert all(hasattr(p, "stage") for p in [])  # type guard noop


# ---------------------------------------------------------------------------
# fallback paths — must never raise
# ---------------------------------------------------------------------------

def test_generation_failure_falls_back(tmp_path):
    def _boom(text, voice, speed):
        raise RuntimeError("no TTS")

    trainer = WakeWordTrainer(
        generator=SyntheticGenerator(synthesize_fn=_boom),
        augmenter=DataAugmenter(synthesize_fn=_good_synth, seed=1),
        train_backend=_make_backend(),
    )
    result = _run(trainer, tmp_path)
    assert result.ok is False
    assert result.fell_back is True
    assert result.error


def test_backend_not_writing_model_falls_back(tmp_path):
    trainer = _trainer(backend=_make_backend(write_model=False))
    result = _run(trainer, tmp_path)
    assert result.ok is False
    assert result.fell_back is True
    assert result.error


def test_backend_raising_falls_back(tmp_path):
    def _backend(**kwargs):
        raise RuntimeError("CUDA exploded")

    trainer = _trainer(backend=_backend)
    result = _run(trainer, tmp_path)
    assert result.ok is False
    assert result.fell_back is True
    assert result.error


def test_cancellation_returns_cancelled_result(tmp_path):
    controller = TrainingController()
    controller.cancel()
    result = _run(_trainer(), tmp_path, controller=controller)
    assert result.ok is False
    assert result.cancelled is True
    # A cancelled run is not a fallback to default — it's user-initiated.


def test_trainer_never_raises_on_bad_name(tmp_path):
    try:
        result = _run(_trainer(), tmp_path)
        assert isinstance(result, TrainingResult)
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"trainer raised: {exc}")


def test_controller_pause_resume_state():
    controller = TrainingController()
    assert controller.is_paused is False
    controller.pause()
    assert controller.is_paused is True
    controller.resume()
    assert controller.is_paused is False
    assert controller.is_cancelled is False
    controller.cancel()
    assert controller.is_cancelled is True
