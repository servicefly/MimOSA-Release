"""Custom wake-word training subsystem for MimOSA (Milestone 2).

This package lets a user train a personalised wake word (e.g. "Jarvis") that
runs **entirely on-device** -- no audio is ever uploaded and nothing is sent to
a cloud service. Training is strictly *opt-in* and happens **after** the
first-run setup wizard; the default "Hey MimOSA" experience always keeps
working and is the guaranteed fallback if training is declined or fails.

The pipeline has four cooperating stages, each in its own module so they can be
unit-tested in isolation and the heavy ML pieces can be stubbed/mocked:

* :mod:`mimosa.training.name_analyzer` -- pure-logic analysis of how trainable a
  chosen wake word is (syllables, distinctiveness, false-trigger risk, time
  estimates). No heavy dependencies; safe to import anywhere.
* :mod:`mimosa.training.dependencies` -- checks whether the heavy training stack
  (PyTorch, TensorFlow, openWakeWord training extras) is installed and, if not,
  reports the download size so the UI can ask the user before pulling ~2.5 GB.
* :mod:`mimosa.training.synthetic_generator` -- uses the bundled Piper TTS to
  synthesize many spoken variations of the wake word (multiple voices, speeds,
  pitches) as training positives.
* :mod:`mimosa.training.data_augmenter` -- mixes background noise, reverb and
  far-field effects into the positives and assembles negative samples to make
  the model robust.
* :mod:`mimosa.training.trainer` -- orchestrates the above, drives the
  openWakeWord training routine, and exports a ``.onnx`` model. Emits progress
  events for the training UI and degrades gracefully on any failure.

Everything here follows MimOSA's core principles: **privacy-first/local-first**,
**graceful degradation** (never crash; always fall back to "Mimosa"), and a
**warm, friend-like** tone in any user-facing strings.
"""

from __future__ import annotations

from mimosa.training.name_analyzer import (
    NameAnalysis,
    analyze_wake_word,
)
from mimosa.training.dependencies import (
    DependencyReport,
    check_dependencies,
    dependencies_satisfied,
    install_dependencies,
)
from mimosa.training.synthetic_generator import (
    GenerationResult,
    SyntheticGenerator,
    default_samples_dir,
    slugify,
)
from mimosa.training.data_augmenter import (
    AugmentationResult,
    DataAugmenter,
)
from mimosa.training.trainer import (
    TrainingController,
    TrainingProgress,
    TrainingResult,
    WakeWordTrainer,
    default_model_path,
    default_models_dir,
)

__all__ = [
    # name analysis
    "NameAnalysis",
    "analyze_wake_word",
    # dependencies
    "DependencyReport",
    "check_dependencies",
    "dependencies_satisfied",
    "install_dependencies",
    # synthetic generation
    "GenerationResult",
    "SyntheticGenerator",
    "default_samples_dir",
    "slugify",
    # augmentation
    "AugmentationResult",
    "DataAugmenter",
    # training
    "TrainingController",
    "TrainingProgress",
    "TrainingResult",
    "WakeWordTrainer",
    "default_model_path",
    "default_models_dir",
]
