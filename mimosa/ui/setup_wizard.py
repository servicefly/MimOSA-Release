"""Pure (GTK-free) controller for the first-run setup wizard (M4.2).

Phase 3 noted the settings infrastructure was "ready" for a first-run wizard;
this module provides it. :class:`SetupWizardController` walks the user through a
short series of steps (welcome -> voice -> privacy -> system -> finish), edits a
**working copy** of the unified config, and on completion commits it via the
:class:`~mimosa.utils.config.AppConfigManager` and records that the wizard has
run (so it never reappears).

As with the Settings dialog (M3.3), all behaviour lives in this pure controller
so it is fully unit-testable on a headless machine; the eventual GTK view is a
thin shell that renders the declarative step/field descriptors and forwards
button presses.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from mimosa.utils.config import (
    AppConfig,
    AppConfigManager,
    LLM_PROVIDERS,
    MAX_HISTORY_LIMIT,
    MAX_WAKE_SENSITIVITY,
    MIN_HISTORY_LIMIT,
    MIN_WAKE_SENSITIVITY,
    WHISPER_MODELS,
)
from mimosa.ui.settings_logic import FieldSpec

logger = logging.getLogger(__name__)

# -- step identifiers (stable; used by the view & tests) --------------------
STEP_WELCOME = "welcome"
STEP_VOICE = "voice"
STEP_PRIVACY = "privacy"
STEP_SYSTEM = "system"
STEP_FINISH = "finish"


@dataclass(frozen=True)
class WizardStep:
    """One screen of the wizard: an id, a title, body text, and its fields."""

    step_id: str
    title: str
    body: str
    fields: Tuple[FieldSpec, ...] = ()


def build_wizard_steps() -> Tuple[WizardStep, ...]:
    """Return the ordered wizard steps (declarative; rendered by the view)."""
    welcome = WizardStep(
        STEP_WELCOME,
        "Welcome to MimOSA",
        "MimOSA is your private, local-first voice assistant. This quick setup "
        "tunes a few preferences. Nothing you enter ever leaves your device.",
        fields=(),
    )
    voice = WizardStep(
        STEP_VOICE,
        "Voice",
        "Choose how MimOSA listens. You can change all of this later in Settings.",
        fields=(
            FieldSpec("voice", "wake_word", "Wake word", "text",
                      help="Phrase that activates listening.", restart=True),
            FieldSpec("voice", "wake_word_sensitivity", "Wake-word sensitivity",
                      "float", minimum=MIN_WAKE_SENSITIVITY,
                      maximum=MAX_WAKE_SENSITIVITY, step=0.05,
                      help="Higher = easier to trigger."),
            FieldSpec("voice", "stt_model", "Speech-to-text model", "choice",
                      choices=WHISPER_MODELS, restart=True,
                      help="Larger models are more accurate but slower."),
        ),
    )
    privacy = WizardStep(
        STEP_PRIVACY,
        "Privacy",
        "MimOSA is private by design. Pick where answers are generated and how "
        "much conversation history to keep.",
        fields=(
            FieldSpec("privacy", "llm_provider", "Answer engine", "choice",
                      choices=LLM_PROVIDERS,
                      help="'none' and 'local' keep everything on your machine."),
            FieldSpec("privacy", "store_history", "Remember conversation", "bool",
                      help="Keep recent turns for context (never written to disk)."),
            FieldSpec("privacy", "conversation_history_limit", "History limit",
                      "int", minimum=MIN_HISTORY_LIMIT, maximum=MAX_HISTORY_LIMIT,
                      step=1, help="How many turns to keep for context."),
        ),
    )
    system = WizardStep(
        STEP_SYSTEM,
        "System Integration",
        "Decide what MimOSA may do on your computer. Safe mode keeps you in "
        "control by confirming anything destructive.",
        fields=(
            FieldSpec("system", "file_operations_enabled", "Allow file operations",
                      "bool"),
            FieldSpec("system", "app_control_enabled", "Allow application control",
                      "bool"),
            FieldSpec("system", "system_controls_enabled", "Allow system controls",
                      "bool"),
            FieldSpec("system", "safe_mode", "Safe mode (recommended)", "bool",
                      help="Confirm destructive & system actions."),
        ),
    )
    finish = WizardStep(
        STEP_FINISH,
        "All set!",
        "You're ready to go. MimOSA will start listening for your wake word. "
        "Open Settings anytime to fine-tune things.",
        fields=(),
    )
    return (welcome, voice, privacy, system, finish)


class SetupWizardController:
    """Drive the first-run wizard over a working copy of the config.

    Args:
        manager: The :class:`AppConfigManager` to commit into on finish.
        steps: Optional override of the step list (defaults to
            :func:`build_wizard_steps`).
    """

    def __init__(self, manager: AppConfigManager,
                 steps: Optional[Tuple[WizardStep, ...]] = None) -> None:
        self._manager = manager
        self._steps: Tuple[WizardStep, ...] = steps or build_wizard_steps()
        self._index = 0
        self._finished = False
        self._working: AppConfig = self._clone(manager.get())

    @staticmethod
    def _clone(cfg: AppConfig) -> AppConfig:
        return AppConfig.from_dict(cfg.to_dict())

    # -- step access -------------------------------------------------------

    @property
    def steps(self) -> Tuple[WizardStep, ...]:
        return self._steps

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def index(self) -> int:
        return self._index

    @property
    def current_step(self) -> WizardStep:
        return self._steps[self._index]

    @property
    def is_first(self) -> bool:
        return self._index == 0

    @property
    def is_last(self) -> bool:
        return self._index == len(self._steps) - 1

    @property
    def finished(self) -> bool:
        return self._finished

    def progress(self) -> float:
        """Fraction complete in ``[0, 1]`` based on the current step index."""
        if len(self._steps) <= 1:
            return 1.0
        return self._index / (len(self._steps) - 1)

    # -- navigation --------------------------------------------------------

    def next(self) -> WizardStep:
        """Advance one step (clamped at the last). Returns the new step."""
        if self._index < len(self._steps) - 1:
            self._index += 1
        return self.current_step

    def back(self) -> WizardStep:
        """Go back one step (clamped at the first). Returns the new step."""
        if self._index > 0:
            self._index -= 1
        return self.current_step

    def goto(self, step_id: str) -> WizardStep:
        """Jump to a step by id. Raises ``KeyError`` if unknown."""
        for i, step in enumerate(self._steps):
            if step.step_id == step_id:
                self._index = i
                return step
        raise KeyError(f"unknown wizard step: {step_id!r}")

    # -- editing -----------------------------------------------------------

    @property
    def working_config(self) -> AppConfig:
        return self._working

    def get_value(self, section: str, name: str) -> Any:
        target = getattr(self._working, section)
        return getattr(target, name)

    def set_value(self, section: str, name: str, value: Any) -> Any:
        """Set a working-copy field, then validate (clamps/normalises).

        Returns the stored (possibly coerced) value.
        """
        target = getattr(self._working, section, None)
        if target is None or not hasattr(target, name):
            raise KeyError(f"{section}.{name} is not a valid setting")
        setattr(target, name, value)
        self._working.validate()
        return getattr(getattr(self._working, section), name)

    # -- completion --------------------------------------------------------

    def finish(self, *, persist: bool = True) -> AppConfig:
        """Commit the working copy and mark the wizard complete.

        Idempotent: calling more than once is harmless.
        """
        self._working.validate()
        self._working.first_run_complete = True
        self._manager.replace(self._working, persist=persist)
        self._manager.mark_first_run_complete(persist=persist)
        self._finished = True
        logger.info("First-run setup wizard completed.")
        return self._manager.get()

    def cancel(self, *, mark_complete: bool = True, persist: bool = True) -> None:
        """Abort the wizard, discarding working edits.

        By default this still marks the wizard complete (so a user who skips
        setup isn't nagged on every launch); pass ``mark_complete=False`` to
        leave first-run state untouched.
        """
        self._working = self._clone(self._manager.get())
        self._finished = True
        if mark_complete:
            self._manager.mark_first_run_complete(persist=persist)

    @classmethod
    def should_run(cls, manager: AppConfigManager) -> bool:
        """Whether the wizard should be shown (i.e. this is a first run)."""
        return manager.is_first_run()
