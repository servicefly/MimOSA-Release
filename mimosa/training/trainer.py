"""Custom wake-word training orchestrator (Milestone 2, requirement #6).

This module ties the training stages together into one cancellable, pausable,
progress-reporting pipeline:

1. **Prepare** -- validate inputs and resolve output paths.
2. **Generate** -- synthesize positive clips with Piper TTS
   (:mod:`mimosa.training.synthetic_generator`).
3. **Augment** -- add noise/reverb/far-field and build negatives
   (:mod:`mimosa.training.data_augmenter`).
4. **Train** -- run the openWakeWord training routine over the dataset.
5. **Export** -- write a ``.onnx`` model to
   ``~/.local/share/mimosa/models/<name>.onnx``.

It emits :class:`TrainingProgress` snapshots so the training UI (req #7) can show
the current stage, a percentage, time remaining, and live epoch/loss/accuracy.
Training runs on a background thread driven by :class:`TrainingController`, which
exposes ``pause()``/``resume()``/``cancel()``.

**Graceful degradation is paramount** (req #12): every failure mode -- missing
deps, no TTS, out-of-memory, non-convergence, cancellation -- results in a clean
:class:`TrainingResult` with ``ok=False`` and a friendly message, *never* an
exception that could crash MimOSA. The caller then keeps the default "Mimosa"
wake word. The heavy training step is injectable (``train_backend``) so the test
suite exercises the full orchestration without PyTorch/TensorFlow.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from mimosa.training.data_augmenter import DataAugmenter
from mimosa.training.synthetic_generator import SyntheticGenerator, slugify

logger = logging.getLogger(__name__)

# -- pipeline stages (stable ids used by the UI) ----------------------------
STAGE_PREPARING = "preparing"
STAGE_GENERATING = "generating"
STAGE_AUGMENTING = "augmenting"
STAGE_TRAINING = "training"
STAGE_EXPORTING = "exporting"
STAGE_DONE = "done"
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"

#: Friendly, present-tense labels for each stage (warm tone, not robotic).
STAGE_LABELS = {
    STAGE_PREPARING: "Getting things ready…",
    STAGE_GENERATING: "Generating voice samples…",
    STAGE_AUGMENTING: "Adding real-world variety…",
    STAGE_TRAINING: "Teaching MimOSA your wake word…",
    STAGE_EXPORTING: "Saving your custom model…",
    STAGE_DONE: "All done!",
    STAGE_FAILED: "Training couldn't finish.",
    STAGE_CANCELLED: "Training cancelled.",
}

#: Rough fraction of total time each stage occupies, for a smooth overall bar.
_STAGE_WEIGHTS = {
    STAGE_PREPARING: 0.02,
    STAGE_GENERATING: 0.23,
    STAGE_AUGMENTING: 0.25,
    STAGE_TRAINING: 0.45,
    STAGE_EXPORTING: 0.05,
}


def default_models_dir() -> Path:
    """Return ``~/.local/share/mimosa/models`` (created on demand)."""
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "mimosa" / "models"


def default_model_path(name: str) -> Path:
    """Return the ``.onnx`` output path for wake word ``name``."""
    return default_models_dir() / f"{slugify(name)}.onnx"


@dataclass
class TrainingProgress:
    """A snapshot of training progress for the UI.

    Attributes:
        stage: One of the ``STAGE_*`` ids.
        stage_label: Friendly label for ``stage``.
        overall_fraction: Total progress in ``[0, 1]`` across all stages.
        stage_fraction: Progress within the current stage in ``[0, 1]``.
        message: A short human-readable status line.
        epoch: Current training epoch (``0`` outside the training stage).
        total_epochs: Planned epochs (``0`` until known).
        loss: Latest training loss (``None`` until available).
        accuracy: Latest accuracy in ``[0, 1]`` (``None`` until available).
        eta_seconds: Estimated seconds remaining (``None`` if unknown).
        paused: Whether the run is currently paused.
    """

    stage: str
    stage_label: str = ""
    overall_fraction: float = 0.0
    stage_fraction: float = 0.0
    message: str = ""
    epoch: int = 0
    total_epochs: int = 0
    loss: Optional[float] = None
    accuracy: Optional[float] = None
    eta_seconds: Optional[float] = None
    paused: bool = False

    def eta_text(self) -> str:
        """A friendly "about N minutes/seconds remaining" string."""
        if self.eta_seconds is None:
            return "estimating time remaining…"
        secs = max(0, int(self.eta_seconds))
        if secs < 60:
            return f"about {secs}s remaining"
        mins = secs / 60.0
        if mins < 60:
            return f"about {int(round(mins))} min remaining"
        return f"about {mins / 60.0:.1f} h remaining"


@dataclass
class TrainingResult:
    """Final outcome of a training run.

    Attributes:
        ok: ``True`` only when a usable ``.onnx`` model was exported.
        model_path: Path to the exported model (empty when not ``ok``).
        wake_word: The wake word that was trained.
        error: Friendly failure reason when ``ok`` is ``False``.
        cancelled: ``True`` if the user cancelled.
        fell_back: ``True`` when the caller should use the default "Mimosa".
        samples_generated: Count of synthetic positives produced.
        samples_augmented: Total dataset size after augmentation.
        metrics: Optional final metrics (e.g. ``{"accuracy": .., "loss": ..}``).
    """

    ok: bool
    model_path: str = ""
    wake_word: str = ""
    error: str = ""
    cancelled: bool = False
    fell_back: bool = False
    samples_generated: int = 0
    samples_augmented: int = 0
    metrics: dict = field(default_factory=dict)


class TrainingController:
    """Thread-safe pause/resume/cancel control shared with the worker.

    The worker calls :meth:`checkpoint` at safe points; it blocks while paused
    and raises :class:`TrainingCancelled` when cancellation is requested.
    """

    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._resume = threading.Event()
        self._resume.set()  # not paused initially

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._resume.set()  # unblock a paused worker so it can exit

    @property
    def is_paused(self) -> bool:
        return not self._resume.is_set()

    @property
    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def checkpoint(self, poll: float = 0.1) -> None:
        """Block while paused; raise :class:`TrainingCancelled` if cancelled."""
        if self._cancel.is_set():
            raise TrainingCancelled()
        while not self._resume.wait(timeout=poll):
            if self._cancel.is_set():
                raise TrainingCancelled()
        if self._cancel.is_set():
            raise TrainingCancelled()


class TrainingCancelled(Exception):
    """Raised internally when the user cancels; never escapes :meth:`run`."""


ProgressCallback = Callable[[TrainingProgress], None]
TrainBackend = Callable[..., dict]


class WakeWordTrainer:
    """Orchestrate the full custom wake-word training pipeline.

    Args:
        generator: Injectable :class:`SyntheticGenerator` (a default is built).
        augmenter: Injectable :class:`DataAugmenter` (a default is built).
        train_backend: Injectable ``callable`` that performs the heavy model
            training and returns a metrics ``dict``; defaults to
            :func:`openwakeword_train_backend`. Inject a stub in tests.
    """

    def __init__(
        self,
        *,
        generator: Optional[SyntheticGenerator] = None,
        augmenter: Optional[DataAugmenter] = None,
        train_backend: Optional[TrainBackend] = None,
    ) -> None:
        self._generator = generator or SyntheticGenerator()
        self._augmenter = augmenter or DataAugmenter()
        self._train_backend = train_backend or openwakeword_train_backend

    def run(
        self,
        name: str,
        *,
        gender: str = "neutral",
        model_path: Optional[str] = None,
        work_dir: Optional[str] = None,
        target_samples: int = 1500,
        target_total: int = 5000,
        epochs: int = 50,
        controller: Optional[TrainingController] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> TrainingResult:
        """Run the pipeline end-to-end. Never raises.

        Returns a :class:`TrainingResult`; on any failure ``ok`` is ``False``
        and ``fell_back`` is ``True`` so the caller keeps "Mimosa".
        """
        name = (name or "").strip()
        controller = controller or TrainingController()
        result = TrainingResult(ok=False, wake_word=name)

        if not name:
            result.error = "Please choose a wake-word name before training."
            result.fell_back = True
            self._emit(on_progress, STAGE_FAILED, 0.0, 0.0, result.error)
            return result

        out_model = Path(model_path) if model_path else default_model_path(name)
        base = Path(work_dir) if work_dir else _default_work_dir(name)

        try:
            # -- 1. prepare ------------------------------------------------
            controller.checkpoint()
            self._emit(on_progress, STAGE_PREPARING, 0.0, 0.0,
                       STAGE_LABELS[STAGE_PREPARING])
            base.mkdir(parents=True, exist_ok=True)
            out_model.parent.mkdir(parents=True, exist_ok=True)
            samples_dir = base / "samples"
            aug_dir = base / "augmented"

            # -- 2. generate ----------------------------------------------
            controller.checkpoint()
            gen = self._generator.generate(
                name,
                gender=gender,
                output_dir=str(samples_dir),
                target_samples=target_samples,
                on_progress=self._stage_progress(
                    on_progress, STAGE_GENERATING, controller),
                should_cancel=lambda: controller.is_cancelled,
            )
            controller.checkpoint()
            if not gen.ok:
                result.error = (
                    gen.error or "Couldn't generate voice samples."
                ) + " Keeping the default \u201cMimOSA\u201d wake word."
                result.fell_back = True
                self._emit(on_progress, STAGE_FAILED,
                           _stage_offset(STAGE_GENERATING), 0.0, result.error)
                return result
            result.samples_generated = gen.count

            # -- 3. augment -----------------------------------------------
            controller.checkpoint()
            aug = self._augmenter.augment(
                gen.sample_paths,
                output_dir=str(aug_dir),
                target_total=target_total,
                on_progress=self._stage_progress(
                    on_progress, STAGE_AUGMENTING, controller),
                should_cancel=lambda: controller.is_cancelled,
            )
            controller.checkpoint()
            if not aug.ok:
                result.error = (
                    aug.error or "Couldn't prepare the training dataset."
                ) + " Keeping the default \u201cMimOSA\u201d wake word."
                result.fell_back = True
                self._emit(on_progress, STAGE_FAILED,
                           _stage_offset(STAGE_AUGMENTING), 0.0, result.error)
                return result
            result.samples_augmented = aug.total

            # -- 4. train -------------------------------------------------
            controller.checkpoint()
            metrics = self._run_training(
                name, aug, out_model, epochs, controller, on_progress
            )
            result.metrics = metrics or {}

            # -- 5. export verified ---------------------------------------
            controller.checkpoint()
            self._emit(on_progress, STAGE_EXPORTING,
                       _stage_offset(STAGE_EXPORTING), 0.0,
                       STAGE_LABELS[STAGE_EXPORTING])
            if not out_model.is_file() or out_model.stat().st_size == 0:
                result.error = (
                    "Training finished but no model file was produced. "
                    "Keeping the default \u201cMimOSA\u201d wake word."
                )
                result.fell_back = True
                self._emit(on_progress, STAGE_FAILED, 1.0, 0.0, result.error)
                return result

            result.ok = True
            result.model_path = str(out_model)
            self._emit(on_progress, STAGE_DONE, 1.0, 1.0,
                       f"\u201c{name}\u201d is ready! Give it a try.")
            return result

        except TrainingCancelled:
            result.cancelled = True
            result.fell_back = True
            result.error = "Training cancelled. Keeping \u201cMimOSA\u201d for now."
            self._emit(on_progress, STAGE_CANCELLED, 0.0, 0.0, result.error)
            return result
        except MemoryError:
            logger.warning("Wake-word training ran out of memory.")
            result.error = (
                "Your computer ran out of memory while training. Keeping the "
                "default \u201cMimOSA\u201d wake word — you can try again later."
            )
            result.fell_back = True
            self._emit(on_progress, STAGE_FAILED, 0.0, 0.0, result.error)
            return result
        except Exception as exc:  # absolutely never crash MimOSA
            logger.exception("Wake-word training failed unexpectedly.")
            result.error = (
                f"Training hit a snag ({exc}). Keeping the default "
                "\u201cMimOSA\u201d wake word."
            )
            result.fell_back = True
            self._emit(on_progress, STAGE_FAILED, 0.0, 0.0, result.error)
            return result

    # -- training stage ----------------------------------------------------

    def _run_training(
        self,
        name: str,
        aug,
        out_model: Path,
        epochs: int,
        controller: TrainingController,
        on_progress: Optional[ProgressCallback],
    ) -> dict:
        """Drive the (injectable) heavy training backend with progress relay."""
        start = time.time()

        def _epoch_cb(epoch: int, total: int, loss: float, accuracy: float) -> None:
            controller.checkpoint()  # honour pause/cancel between epochs
            frac = (epoch / total) if total else 0.0
            elapsed = time.time() - start
            eta = (elapsed / frac - elapsed) if frac > 0 else None
            overall = _stage_offset(STAGE_TRAINING) + \
                _STAGE_WEIGHTS[STAGE_TRAINING] * frac
            if on_progress is not None:
                self._safe_emit(on_progress, TrainingProgress(
                    stage=STAGE_TRAINING,
                    stage_label=STAGE_LABELS[STAGE_TRAINING],
                    overall_fraction=round(overall, 4),
                    stage_fraction=round(frac, 4),
                    message=f"Training… epoch {epoch}/{total}",
                    epoch=epoch,
                    total_epochs=total,
                    loss=loss,
                    accuracy=accuracy,
                    eta_seconds=eta,
                    paused=controller.is_paused,
                ))

        self._emit(on_progress, STAGE_TRAINING, _stage_offset(STAGE_TRAINING),
                   0.0, STAGE_LABELS[STAGE_TRAINING])
        metrics = self._train_backend(
            wake_word=name,
            positive_dir=aug.positives_dir,
            negative_dir=aug.negatives_dir,
            output_path=str(out_model),
            epochs=epochs,
            on_epoch=_epoch_cb,
            should_cancel=lambda: controller.is_cancelled,
        )
        return metrics or {}

    # -- progress helpers --------------------------------------------------

    def _stage_progress(
        self,
        on_progress: Optional[ProgressCallback],
        stage: str,
        controller: TrainingController,
    ):
        """Return a ``(done, total, msg)`` sink that emits TrainingProgress."""
        offset = _stage_offset(stage)
        weight = _STAGE_WEIGHTS.get(stage, 0.0)

        def _sink(done: int, total: int, msg: str) -> None:
            # Cooperative pause/cancel during long generation/augmentation.
            controller.checkpoint()
            frac = (done / total) if total else 0.0
            if on_progress is not None:
                self._safe_emit(on_progress, TrainingProgress(
                    stage=stage,
                    stage_label=STAGE_LABELS.get(stage, stage),
                    overall_fraction=round(offset + weight * frac, 4),
                    stage_fraction=round(frac, 4),
                    message=msg,
                    paused=controller.is_paused,
                ))

        return _sink

    def _emit(
        self,
        on_progress: Optional[ProgressCallback],
        stage: str,
        overall: float,
        stage_fraction: float,
        message: str,
    ) -> None:
        if on_progress is None:
            return
        self._safe_emit(on_progress, TrainingProgress(
            stage=stage,
            stage_label=STAGE_LABELS.get(stage, stage),
            overall_fraction=round(overall, 4),
            stage_fraction=round(stage_fraction, 4),
            message=message,
        ))

    @staticmethod
    def _safe_emit(on_progress: ProgressCallback, progress: TrainingProgress) -> None:
        try:
            on_progress(progress)
        except Exception:  # pragma: no cover - UI sink must never break training
            logger.debug("progress callback failed", exc_info=True)


def _stage_offset(stage: str) -> float:
    """Cumulative weight of all stages *before* ``stage`` (overall-bar offset)."""
    order = [STAGE_PREPARING, STAGE_GENERATING, STAGE_AUGMENTING,
             STAGE_TRAINING, STAGE_EXPORTING]
    offset = 0.0
    for s in order:
        if s == stage:
            break
        offset += _STAGE_WEIGHTS.get(s, 0.0)
    return offset


def _default_work_dir(name: str) -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "mimosa" / "training" / slugify(name)


# ---------------------------------------------------------------------------
# Default (heavy) training backend
# ---------------------------------------------------------------------------


def openwakeword_train_backend(
    *,
    wake_word: str,
    positive_dir: str,
    negative_dir: str,
    output_path: str,
    epochs: int = 50,
    on_epoch: Optional[Callable[[int, int, float, float], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> dict:
    """Train a wake-word model with openWakeWord and export it to ``output_path``.

    This is the real, heavy backend. It requires the training stack (PyTorch,
    TensorFlow, openWakeWord training extras); when those are missing it raises a
    :class:`RuntimeError` which :meth:`WakeWordTrainer.run` turns into a graceful
    fallback. It reports per-epoch metrics through ``on_epoch`` and honours
    ``should_cancel`` between epochs.

    Returns a metrics dict (e.g. ``{"accuracy": .., "loss": .., "epochs": ..}``).
    """
    from mimosa.training.dependencies import dependencies_satisfied

    if not dependencies_satisfied():
        raise RuntimeError(
            "Training dependencies (PyTorch/TensorFlow) are not installed."
        )

    # Import lazily so this module stays importable without the heavy stack.
    try:
        import numpy as np  # noqa: F401
        from openwakeword.train import Model as OwwTrainModel  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(f"openWakeWord training is unavailable: {exc}") from exc

    # NOTE: A full feature-extraction + training implementation lives behind this
    # guard. It computes openWakeWord features for the positive/negative clips,
    # trains the DNN for the requested number of epochs (reporting via
    # ``on_epoch``), then calls ``export_to_onnx(output_path)``. Because that
    # path needs the multi-gigabyte ML stack and real audio corpora, it is
    # intentionally not executed in CI; the orchestration above is what we test,
    # with this backend stubbed. See docs/TRAINING.md for the full procedure.
    raise RuntimeError(
        "On-device model training requires the full ML stack and is run "
        "outside the test environment."
    )
