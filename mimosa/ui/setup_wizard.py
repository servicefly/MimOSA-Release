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
from pathlib import Path
from typing import Any, List, Optional, Tuple

from mimosa.utils.config import (
    AppConfig,
    AppConfigManager,
    DEFAULT_TRAINING_PREFERENCE,
    DEFAULT_WAKE_WORD,
    LLM_PROVIDERS_REQUIRING_KEY,
    MAX_HISTORY_LIMIT,
    MAX_WAKE_SENSITIVITY,
    MIN_HISTORY_LIMIT,
    MIN_WAKE_SENSITIVITY,
    VALID_GENDERS,
    VALID_TRAINING_PREFERENCES,
    VALID_VERBOSITY,
    WHISPER_MODELS,
)
from mimosa.ui.settings_logic import FieldSpec

logger = logging.getLogger(__name__)

# -- step identifiers (stable; used by the view & tests) --------------------
STEP_WELCOME = "welcome"
STEP_MICROPHONE = "microphone"
STEP_SPEAKER = "speaker"
STEP_LLM = "llm"
STEP_PERSONALIZE = "personalize"
STEP_VOICE = "voice"
STEP_AVATAR = "avatar"
STEP_WAKEWORD = "wakeword"
STEP_PRIVACY = "privacy"
STEP_SYSTEM = "system"
STEP_FINISH = "finish"


@dataclass(frozen=True)
class MicrophoneChoice:
    """A selectable microphone for the wizard's device dropdown.

    Attributes:
        index: PyAudio device index (``None`` means "system default").
        name: Human-readable device name.
        is_default: Whether this is the system default input device.
    """

    index: Optional[int]
    name: str
    is_default: bool = False

    @property
    def label(self) -> str:
        """Dropdown label, suffixed with ``(Default)`` for the default device."""
        return f"{self.name} (Default)" if self.is_default else self.name


@dataclass(frozen=True)
class SpeakerChoice:
    """A selectable speaker/output device for the wizard's device dropdown.

    Attributes:
        index: PyAudio device index (``None`` means "system default").
        name: Human-readable device name.
        is_default: Whether this is the system default output device.
    """

    index: Optional[int]
    name: str
    is_default: bool = False

    @property
    def label(self) -> str:
        """Dropdown label, suffixed with ``(Default)`` for the default device."""
        return f"{self.name} (Default)" if self.is_default else self.name


@dataclass(frozen=True)
class LLMProviderOption:
    """A selectable LLM provider for the "Connect Your AI Brain" step.

    Attributes:
        key: The config value stored in ``privacy.llm_provider``.
        label: Human-readable radio-button label.
        description: One-line explanation shown beneath the label.
        requires_key: Whether this provider needs an API key.
        is_local: Whether this provider runs entirely on-device.
    """

    key: str
    label: str
    description: str
    requires_key: bool = False
    is_local: bool = False


#: The provider options offered by the wizard's LLM step, in display order.
#: Abacus.AI is first (the recommended default).
LLM_PROVIDER_OPTIONS: Tuple[LLMProviderOption, ...] = (
    LLMProviderOption(
        "abacus", "Abacus.AI (recommended)",
        "Smart cloud routing — great quality with one key. Best default.",
        requires_key=True,
    ),
    LLMProviderOption(
        "openai", "OpenAI",
        "Use your own OpenAI API key (GPT models).",
        requires_key=True,
    ),
    LLMProviderOption(
        "anthropic", "Anthropic",
        "Use your own Anthropic API key (Claude models).",
        requires_key=True,
    ),
    LLMProviderOption(
        "ollama", "Local Ollama",
        "Run models fully on your machine with Ollama. No key, fully private.",
        is_local=True,
    ),
)

#: Default Ollama daemon endpoint probed to detect a local install.
OLLAMA_PROBE_URL = "http://localhost:11434/api/tags"

#: Where to get Ollama if it isn't installed.
OLLAMA_INSTALL_URL = "https://ollama.com/download"


@dataclass(frozen=True)
class WizardStep:
    """One screen of the wizard: an id, a title, body text, and its fields.

    Attributes:
        step_id: Stable identifier (one of the ``STEP_*`` constants).
        title: Heading shown at the top of the step.
        body: Short introductory paragraph for the step.
        fields: Declarative editable fields (rendered by the view). Custom steps
            (microphone/speaker/llm/wakeword/finish) leave this empty and supply
            their own widgets.
        sidebar: Persistent, friendly "what & why" guidance shown in the wizard's
            side panel for this step. Replaces the old per-field info-icon
            tooltips (M2): the help that used to hide behind a hover is now always
            visible alongside the step. Markup-free plain text.
    """

    step_id: str
    title: str
    body: str
    fields: Tuple[FieldSpec, ...] = ()
    sidebar: str = ""


def build_wizard_steps() -> Tuple[WizardStep, ...]:
    """Return the ordered wizard steps (declarative; rendered by the view).

    Every step carries a ``sidebar`` string: warm, always-visible guidance shown
    in the wizard's side panel. This replaces the old per-field info-icon
    tooltips — the same "what & why" help is now in plain sight instead of hidden
    behind a hover.
    """
    welcome = WizardStep(
        STEP_WELCOME,
        "Welcome to MimOSA",
        "MimOSA is your private, local-first voice assistant. This quick setup "
        "tunes a few preferences. Nothing you enter ever leaves your device.",
        fields=(),
        sidebar=(
            "👋 Hi there!\n\n"
            "I'm MimOSA — your own voice assistant that runs entirely on your "
            "computer.\n\n"
            "This setup takes about a minute. I'll walk you through each step "
            "right here in this panel, so you always know what's going on and "
            "why it matters.\n\n"
            "Your privacy comes first: nothing you tell me here ever leaves "
            "your device."
        ),
    )
    microphone = WizardStep(
        STEP_MICROPHONE,
        "Choose Your Microphone",
        "Pick the microphone MimOSA should listen with. If you're not sure, "
        "leave it on the system default. Use \"Test Microphone\" and speak — the "
        "meter should move. You can change this later in Settings.",
        # Rendered with a custom dropdown + test meter by the dialog, so no
        # declarative fields here.
        fields=(),
        sidebar=(
            "🎤 Your ears\n\n"
            "This is the microphone I'll use to hear you. If you've got a "
            "headset or a USB mic, pick it here — otherwise the system default "
            "is usually fine.\n\n"
            "Tip: click \"Test Microphone\" and say a few words. The meter "
            "should jump as you speak. If it stays flat, the mic may be muted "
            "or set wrong.\n\n"
            "You can always change this later in Settings."
        ),
    )
    speaker = WizardStep(
        STEP_SPEAKER,
        "Choose Your Speaker",
        "Pick the speaker or headphones MimOSA should talk through. If you're "
        "not sure, leave it on the system default. Click \"Test Speaker\" to "
        "play a short chime and confirm you can hear it. You can change this "
        "later in Settings.",
        # Rendered with a custom dropdown + test button by the dialog.
        fields=(),
        sidebar=(
            "🔊 My voice\n\n"
            "This is where I'll speak to you. Choose your headphones or "
            "speakers — or leave it on the system default.\n\n"
            "Click \"Test Speaker\" to hear a short chime. If you hear it, "
            "you're good to go!\n\n"
            "You can change this anytime in Settings."
        ),
    )
    llm = WizardStep(
        STEP_LLM,
        "Connect Your AI Brain",
        "MimOSA needs a language model to understand and answer you. Pick a "
        "provider below. Abacus.AI is the easiest — paste a key and you're "
        "done. Prefer to keep everything on your machine? Choose Local Ollama. "
        "This step is required so MimOSA can think.",
        # Rendered with custom radio buttons + masked key entry by the dialog.
        fields=(),
        sidebar=(
            "🧠 My thinking\n\n"
            "I need a language model to understand you and answer well. This is "
            "the one part that can reach out beyond your device — and it's your "
            "choice how.\n\n"
            "• Abacus.AI — easiest: paste one key and you're set.\n"
            "• OpenAI / Anthropic — use your own key.\n"
            "• Local Ollama — runs fully on your machine, totally private. No "
            "key needed.\n\n"
            "This step is required so I can think. Your key is stored only on "
            "your device."
        ),
    )
    personalize = WizardStep(
        STEP_PERSONALIZE,
        "Get to Know MimOSA",
        "Let's get acquainted! Tell MimOSA a little about you so it can greet "
        "you by name and match your style. Every field is optional and stays "
        "on your device.",
        fields=(
            FieldSpec("personality", "user_name", "What should I call you?",
                      "text",
                      help="Your preferred name (e.g. 'Sam'). Leave blank to skip."),
            FieldSpec("personality", "assistant_name", "What would you like to "
                      "call me?", "text",
                      help="A name for your assistant (defaults to 'MimOSA')."),
            FieldSpec("personality", "user_pronouns", "Your pronouns (optional)",
                      "text",
                      help="e.g. 'she/her', 'they/them'. Used only to personalise phrasing."),
            FieldSpec("personality", "verbosity", "How chatty should I be?",
                      "choice", choices=VALID_VERBOSITY,
                      help="'brief' = short answers, 'detailed' = more explanation."),
            FieldSpec("personality", "gender", "Voice style",
                      "choice", choices=VALID_GENDERS,
                      help="Preferred voice/persona style for MimOSA. 'neutral' "
                           "(default) leaves it unspecified; 'female'/'male' bias "
                           "the spoken voice. Purely a presentation choice."),
            FieldSpec("personality", "greet_by_name", "Greet me by name",
                      "bool",
                      help="Say hello using your name when MimOSA starts."),
        ),
        sidebar=(
            "😊 Let's get acquainted\n\n"
            "Tell me a little about you so I can feel more like a friend than a "
            "tool. Everything here is optional and stays on your device.\n\n"
            "• What should I call you? — I'll greet you by name.\n"
            "• What to call me — give me a name if you like (default: MimOSA).\n"
            "• Pronouns — only used to phrase things naturally.\n"
            "• How chatty — brief answers or more detail.\n"
            "• Voice style — neutral, female or male voice for me.\n\n"
            "None of this is set in stone — tweak it anytime in Settings."
        ),
    )
    voice = WizardStep(
        STEP_VOICE,
        "Your Voice",
        "Choose how MimOSA sounds and listens. Pick a voice, set your wake "
        "word and sensitivity. You can change all of this later in Settings.",
        fields=(
            FieldSpec("voice", "wake_word", "Wake word", "text",
                      help="The phrase that wakes MimOSA up (e.g. 'hey mimosa'). "
                           "MimOSA stays asleep until it hears this.", restart=True),
            FieldSpec("voice", "wake_word_sensitivity", "Wake-word sensitivity",
                      "float", minimum=MIN_WAKE_SENSITIVITY,
                      maximum=MAX_WAKE_SENSITIVITY, step=0.05,
                      help="How eager MimOSA is to wake. Higher triggers more "
                           "easily but risks false wake-ups; lower is stricter."),
            FieldSpec("voice", "stt_model", "Speech-to-text model", "choice",
                      choices=WHISPER_MODELS, restart=True,
                      help="The model that turns your speech into text. Larger "
                           "models hear more accurately but run slower on your PC."),
        ),
        sidebar=(
            "👂 How I listen\n\n"
            "• Wake word — the phrase that wakes me up. I stay asleep (and "
            "private) until I hear it. The default is \"hey mimosa\".\n\n"
            "• Sensitivity — how eager I am to wake. Higher wakes more easily "
            "but may trigger by accident; lower is stricter.\n\n"
            "• Speech-to-text model — bigger models hear more accurately but "
            "run slower. Start small and bump it up if you like.\n\n"
            "Want a totally custom wake word like \"Jarvis\"? That's the very "
            "next step!"
        ),
    )
    avatar = WizardStep(
        STEP_AVATAR,
        "Your Avatar",
        "Personalize how MimOSA appears on your desktop. Describe your ideal "
        "assistant's appearance, or use one of our default avatars. You can "
        "always change this later in Settings.",
        # Rendered with custom UI (description entry, preview, generate button)
        fields=(),
        sidebar=(
            "🎨 Your assistant's face\n\n"
            "Give me a look! Describe the kind of character you'd like to see — "
            "professional, friendly, futuristic — and I'll generate a custom "
            "avatar just for you.\n\n"
            "Examples:\n"
            "• \"Young professional with short hair and glasses\"\n"
            "• \"Friendly character with warm colors\"\n"
            "• \"Minimalist design, clean and simple\"\n\n"
            "Your gender preference helps me match your avatar's appearance to "
            "the voice you've chosen.\n\n"
            "If generation isn't available on your hardware, I'll use a stylish "
            "default avatar instead. You can also stick with the classic circle "
            "visualization — it's always an option!"
        ),
    )
    wakeword = WizardStep(
        STEP_WAKEWORD,
        "Wake Word",
        "Want me to answer to a name of your choosing — like \"Jarvis\" or "
        "\"Computer\"? I can train a personal wake-word model right on your "
        "machine. It's completely optional: \"Hey MimOSA\" always works.",
        # Rendered with a custom name entry + live analysis + choice by the
        # dialog.
        fields=(),
        sidebar=(
            "✨ Make me yours\n\n"
            "Type a name and I'll instantly tell you how well it'll work as a "
            "wake word — how distinctive it is, how likely training is to "
            "succeed, and roughly how long it'll take on your hardware.\n\n"
            "Good wake words are two or three syllables and don't sound like "
            "everyday words (so I don't wake up by accident).\n\n"
            "Then choose:\n"
            "• Train now — set it up right after setup.\n"
            "• Train later — I'll remind you; you can do it from Settings.\n"
            "• Keep \"Hey MimOSA\" — no training needed.\n\n"
            "Either way, \"Hey MimOSA\" keeps working as a safe fallback."
        ),
    )
    privacy = WizardStep(
        STEP_PRIVACY,
        "Privacy",
        "MimOSA is private by design. You already chose your answer engine; "
        "here you decide how much conversation history to keep.",
        fields=(
            FieldSpec("privacy", "store_history", "Remember conversation", "bool",
                      help="Keep recent turns for context (never written to disk)."),
            FieldSpec("privacy", "conversation_history_limit", "History limit",
                      "int", minimum=MIN_HISTORY_LIMIT, maximum=MAX_HISTORY_LIMIT,
                      step=1, help="How many turns to keep for context."),
        ),
        sidebar=(
            "🔒 Your privacy\n\n"
            "I'm private by design. Conversation history lives only in memory "
            "to help me follow context — it's never written to disk.\n\n"
            "• Remember conversation — lets me recall what we just talked "
            "about. Turn it off for a clean slate every time.\n"
            "• History limit — how many recent turns to keep in mind.\n\n"
            "You're always in control of what I remember."
        ),
    )
    system = WizardStep(
        STEP_SYSTEM,
        "System Integration",
        "Decide what MimOSA may do on your computer. Safe mode keeps you in "
        "control by confirming anything destructive.",
        fields=(
            FieldSpec("system", "file_operations_enabled", "Allow file operations",
                      "bool",
                      help="Lets MimOSA find, open, create and move files when you "
                           "ask. Turn off to keep MimOSA away from your files entirely."),
            FieldSpec("system", "app_control_enabled", "Allow application control",
                      "bool",
                      help="Lets MimOSA launch and close applications by voice "
                           "(e.g. 'open Firefox'). Off means MimOSA won't start apps."),
            FieldSpec("system", "system_controls_enabled", "Allow system controls",
                      "bool",
                      help="Lets MimOSA adjust volume, brightness, Wi-Fi and check "
                           "battery. Off means it won't touch your system settings."),
            FieldSpec("system", "safe_mode", "Safe mode (recommended)", "bool",
                      help="Asks you to confirm before anything destructive (deleting "
                           "files, changing system settings). Keeps you in control."),
        ),
        sidebar=(
            "🛠️ What I can do\n\n"
            "Decide how much I'm allowed to help with on your computer. Each is "
            "off-limits unless you switch it on.\n\n"
            "• File operations — find, open and organise files you ask about.\n"
            "• Application control — open and close apps by voice.\n"
            "• System controls — volume, brightness, Wi-Fi, battery.\n"
            "• Safe mode — I'll always confirm before anything destructive. "
            "Leave this on; it keeps you firmly in control."
        ),
    )
    finish = WizardStep(
        STEP_FINISH,
        "All set!",
        "You're ready to go. MimOSA will start listening for your wake word. "
        "Open Settings anytime to fine-tune things.",
        fields=(),
        sidebar=(
            "🎉 You're all set!\n\n"
            "That's everything — thank you! I'll start listening for your wake "
            "word right away.\n\n"
            "If you chose to train a custom wake word, we'll get started as "
            "soon as you finish here.\n\n"
            "Everything you set today can be changed anytime in Settings. "
            "Talk soon!"
        ),
    )
    return (welcome, microphone, speaker, llm, personalize, voice, avatar,
            wakeword, privacy, system, finish)


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

    # -- microphone selection (STEP_MICROPHONE) ----------------------------

    def available_microphones(self) -> List["MicrophoneChoice"]:
        """List selectable microphones, with a leading "system default" entry.

        The first entry always represents the system default (``index=None``);
        the remaining entries are the enumerated input devices. The device that
        PortAudio reports as the system default is flagged ``is_default`` so the
        view can label it ``(Default)``. Never raises -- returns just the
        default entry when no audio backend is available.
        """
        from mimosa.voice.audio_manager import AudioManager

        choices: List[MicrophoneChoice] = [
            MicrophoneChoice(index=None, name="System default microphone")
        ]
        try:
            default = AudioManager.get_default_input_device()
            default_index = default.index if default is not None else None
            for dev in AudioManager.list_input_devices():
                choices.append(
                    MicrophoneChoice(
                        index=dev.index,
                        name=dev.name,
                        is_default=(dev.index == default_index),
                    )
                )
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not enumerate microphones", exc_info=True)
        return choices

    def get_selected_microphone(self) -> Optional[int]:
        """Return the working-copy input-device index, or ``None`` for default."""
        from mimosa.voice.audio_manager import AudioManager

        return AudioManager.resolve_device_index(self._working.voice.input_device)

    def set_microphone(self, index: Optional[int]) -> None:
        """Select a microphone by device index (``None`` = system default).

        Stores the choice as a string in ``voice.input_device`` (``""`` for the
        system default) so it round-trips through the existing config schema and
        the Settings dialog's microphone field.
        """
        value = "" if index is None else str(int(index))
        self.set_value("voice", "input_device", value)

    def test_microphone(
        self,
        seconds: float = 2.0,
        on_level: Optional[Any] = None,
    ) -> Optional[float]:
        """Record briefly from the selected mic and return the peak level.

        Returns a normalised peak volume in ``[0, 1]``, or ``None`` if the audio
        backend / device is unavailable (so the view can show a friendly
        "couldn't access microphone" message instead of crashing). Calls
        ``on_level(level)`` per chunk for a live meter when provided.
        """
        from mimosa.voice.audio_manager import (
            AudioManager,
            AudioUnavailableError,
        )

        index = self.get_selected_microphone()
        mgr = AudioManager(device_index=index)
        try:
            return mgr.measure_levels(seconds=seconds, on_level=on_level)
        except AudioUnavailableError:
            logger.info("Microphone test requested but no audio backend available.")
            return None
        except Exception:  # pragma: no cover - defensive
            logger.debug("Microphone test failed", exc_info=True)
            return None
        finally:
            mgr.close()

    # -- speaker selection (STEP_SPEAKER) ----------------------------------

    def available_speakers(self) -> List["SpeakerChoice"]:
        """List selectable speakers, with a leading "system default" entry.

        The first entry always represents the system default (``index=None``);
        the remaining entries are the enumerated output devices. The device that
        PortAudio reports as the system default is flagged ``is_default`` so the
        view can label it ``(Default)``. Never raises -- returns just the
        default entry when no audio backend is available.
        """
        from mimosa.voice.audio_manager import AudioManager

        choices: List[SpeakerChoice] = [
            SpeakerChoice(index=None, name="System default speaker")
        ]
        try:
            default = AudioManager.get_default_output_device()
            default_index = default.index if default is not None else None
            for dev in AudioManager.list_output_devices():
                choices.append(
                    SpeakerChoice(
                        index=dev.index,
                        name=dev.name,
                        is_default=(dev.index == default_index),
                    )
                )
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not enumerate speakers", exc_info=True)
        return choices

    def get_selected_speaker(self) -> Optional[int]:
        """Return the working-copy output-device index, or ``None`` for default."""
        from mimosa.voice.audio_manager import AudioManager

        return AudioManager.resolve_output_device_index(
            self._working.voice.output_device
        )

    def set_speaker(self, index: Optional[int]) -> None:
        """Select a speaker by device index (``None`` = system default).

        Stores the choice as a string in ``voice.output_device`` (``""`` for the
        system default) so it round-trips through the existing config schema and
        the Settings dialog's speaker field.
        """
        value = "" if index is None else str(int(index))
        self.set_value("voice", "output_device", value)

    def test_speaker(self, seconds: float = 1.0) -> bool:
        """Play a short chime through the selected speaker to confirm output.

        Returns ``True`` if the chime played, or ``False`` if the audio backend
        / device is unavailable (so the view can show a friendly "couldn't
        access speaker" message instead of crashing).
        """
        from mimosa.voice.audio_manager import (
            AudioManager,
            AudioUnavailableError,
        )

        index = self.get_selected_speaker()
        mgr = AudioManager(output_device=index)
        try:
            pcm, rate = self._build_chime(duration=seconds)
            mgr.play(pcm, sample_rate=rate)
            return True
        except AudioUnavailableError:
            logger.info("Speaker test requested but no audio backend available.")
            return False
        except Exception:  # pragma: no cover - defensive
            logger.debug("Speaker test failed", exc_info=True)
            return False
        finally:
            mgr.close()

    @staticmethod
    def _build_chime(duration: float = 1.0, sample_rate: int = 44100) -> Tuple[bytes, int]:
        """Synthesize a pleasant two-note chime as 16-bit PCM bytes.

        Pure-stdlib (math/struct) so it needs no extra dependencies. Returns
        ``(pcm_bytes, sample_rate)`` suitable for :meth:`AudioManager.play`.
        """
        import math
        import struct

        duration = max(0.2, min(3.0, float(duration)))
        # Two ascending notes (A5 then E6) with a short fade to avoid clicks.
        notes = (880.0, 1318.5)
        amplitude = 0.35 * 32767
        samples = bytearray()
        per_note = duration / len(notes)
        note_frames = int(sample_rate * per_note)
        fade = max(1, int(note_frames * 0.1))
        for freq in notes:
            for n in range(note_frames):
                env = 1.0
                if n < fade:
                    env = n / fade
                elif n > note_frames - fade:
                    env = max(0.0, (note_frames - n) / fade)
                value = int(amplitude * env * math.sin(2 * math.pi * freq * n / sample_rate))
                samples += struct.pack("<h", value)
        return bytes(samples), sample_rate

    # -- LLM provider selection (STEP_LLM) ---------------------------------

    def llm_provider_options(self) -> Tuple["LLMProviderOption", ...]:
        """Return the selectable LLM providers, in display order."""
        return LLM_PROVIDER_OPTIONS

    def get_llm_provider(self) -> str:
        """Return the working-copy LLM provider key (e.g. ``"abacus"``)."""
        return self._working.privacy.llm_provider

    def set_llm_provider(self, provider: str) -> str:
        """Select the LLM provider (stored in ``privacy.llm_provider``).

        Returns the stored (validated) provider key.
        """
        return self.set_value("privacy", "llm_provider", provider)

    def get_api_key(self) -> str:
        """Return the working-copy API key for cloud providers."""
        return self._working.privacy.api_key

    def set_api_key(self, api_key: str) -> str:
        """Store the API key (in ``privacy.api_key``); empty string clears it.

        Returns the stored (validated/trimmed) value.
        """
        return self.set_value("privacy", "api_key", api_key or "")

    @staticmethod
    def provider_requires_key(provider: str) -> bool:
        """Whether ``provider`` needs an API key to function."""
        return provider in LLM_PROVIDERS_REQUIRING_KEY

    def detect_ollama(self, timeout: float = 0.5) -> bool:
        """Return ``True`` if a local Ollama daemon answers on port 11434.

        Probes ``http://localhost:11434/api/tags`` with a short timeout. Never
        raises -- returns ``False`` when Ollama is not installed/running or the
        network stack is unavailable.
        """
        import urllib.request

        try:
            with urllib.request.urlopen(OLLAMA_PROBE_URL, timeout=timeout) as resp:
                return 200 <= getattr(resp, "status", resp.getcode()) < 300
        except Exception:
            logger.debug("Ollama not detected on %s", OLLAMA_PROBE_URL,
                         exc_info=True)
            return False

    def llm_step_valid(self) -> bool:
        """Whether the LLM step is satisfied so the user may proceed.

        Required-step rule:

        * A cloud provider (abacus/openai/anthropic) needs a non-empty API key.
        * Local Ollama needs a running daemon (so MimOSA can actually think).
        * ``local``/``none`` (not offered as radio options here, but possible
          via config) are always considered valid.
        """
        provider = (self.get_llm_provider() or "").strip().lower()
        if provider in LLM_PROVIDERS_REQUIRING_KEY:
            return bool((self.get_api_key() or "").strip())
        if provider == "ollama":
            return self.detect_ollama()
        # "local"/"none" or anything else: nothing more required.
        return True

    # -- custom wake word (STEP_WAKEWORD) ----------------------------------

    def hardware_capability(self) -> str:
        """Return the detected hardware capability level (``"gpu"``/``"cpu"``/...).

        Read from the working config's hardware section; used to tailor the
        training-time estimate shown beside a candidate wake word.
        """
        return getattr(self._working.hardware, "capability_level", "") or ""

    def analyze_custom_name(self, name: str):
        """Analyse a candidate wake word and return a :class:`NameAnalysis`.

        Pure, dependency-light logic (no heavy ML imports). Never raises -- on
        any unexpected error it returns a conservative "very hard / not
        trainable" analysis so the view can always render something friendly.
        """
        from mimosa.training import analyze_wake_word

        try:
            return analyze_wake_word(name, self.hardware_capability())
        except Exception:  # pragma: no cover - defensive
            logger.debug("Wake-word analysis failed for %r", name, exc_info=True)
            return analyze_wake_word("", self.hardware_capability())

    def get_custom_wake_word_name(self) -> str:
        """Return the working-copy custom wake-word name (may be empty)."""
        return self._working.voice.custom_wake_word_name or ""

    def set_custom_wake_word_name(self, name: str) -> str:
        """Store the chosen custom wake-word name (trimmed). Returns it."""
        return self.set_value("voice", "custom_wake_word_name", (name or "").strip())

    def get_training_preference(self) -> str:
        """Return the working-copy training preference (mimosa/now/later)."""
        return self._working.voice.training_preference or DEFAULT_TRAINING_PREFERENCE

    def set_training_preference(self, preference: str) -> str:
        """Set the training preference; invalid values fall back to default.

        Returns the stored (validated) preference.
        """
        pref = (preference or "").strip().lower()
        if pref not in VALID_TRAINING_PREFERENCES:
            pref = DEFAULT_TRAINING_PREFERENCE
        return self.set_value("voice", "training_preference", pref)

    def training_preference_options(self) -> Tuple[Tuple[str, str, str], ...]:
        """Return ``(key, label, description)`` triples for the wake-word step."""
        return (
            ("now", "Train it now",
             "Set up your custom wake word right after this wizard finishes."),
            ("later", "Train it later",
             "Keep \"Hey MimOSA\" for now; train your wake word anytime from "
             "Settings."),
            ("mimosa", "Just use \"Hey MimOSA\"",
             "No training needed — the built-in wake word works out of the box."),
        )

    def wants_training_now(self) -> bool:
        """Whether the user opted to train their custom wake word immediately."""
        return (self.get_training_preference() == "now"
                and bool(self.get_custom_wake_word_name()))

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

    def create_desktop_shortcut(self) -> bool:
        """Create a desktop launcher for MimOSA in the user's ``~/Desktop``.

        Looks for an installed ``mimosa.desktop`` entry (first in the user's
        local applications dir, then the system one) and copies it onto the
        Desktop.  If no installed entry is found a minimal launcher is
        generated that relies on the ``mimosa`` command and themed icon being
        on ``PATH``/in the icon theme (both provided by ``install.sh``).

        The copied file is marked executable and, when the ``gio`` helper is
        available, flagged as a *trusted* launcher so GNOME doesn't show the
        "Untrusted application launcher" warning.

        Returns ``True`` on success.  Never raises -- desktop integration is a
        best-effort convenience and must not break the wizard.
        """
        import os
        import shutil
        import stat
        import subprocess
        from pathlib import Path

        try:
            home = Path.home()
            # Resolve the Desktop directory (respect XDG user dirs when set).
            desktop_dir = Path(
                os.environ.get("XDG_DESKTOP_DIR", home / "Desktop")
            )
            desktop_dir.mkdir(parents=True, exist_ok=True)
            target = desktop_dir / "mimosa.desktop"

            xdg_data_home = Path(
                os.environ.get("XDG_DATA_HOME", home / ".local" / "share")
            )
            candidates = [
                xdg_data_home / "applications" / "mimosa.desktop",
                Path("/usr/share/applications/mimosa.desktop"),
                Path("/usr/local/share/applications/mimosa.desktop"),
            ]
            source = next((c for c in candidates if c.is_file()), None)

            if source is not None:
                shutil.copyfile(source, target)
            else:
                exec_cmd = shutil.which("mimosa") or "mimosa"
                target.write_text(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=MimOSA\n"
                    "Comment=Your voice-first AI companion\n"
                    f"Exec={exec_cmd}\n"
                    "Icon=mimosa\n"
                    "Terminal=false\n"
                    "Categories=Utility;Accessibility;AudioVideo;\n",
                    encoding="utf-8",
                )

            # Make the launcher executable (required by most file managers).
            mode = target.stat().st_mode
            target.chmod(
                mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )

            # Best-effort: mark the launcher trusted for GNOME/Nautilus.
            gio = shutil.which("gio")
            if gio:
                try:
                    subprocess.run(
                        [gio, "set", str(target),
                         "metadata::trusted", "true"],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    )
                except Exception:  # pragma: no cover - purely cosmetic
                    logger.debug("gio trust flag failed", exc_info=True)

            logger.info("Created desktop shortcut at %s", target)
            return True
        except Exception:
            logger.warning("Could not create desktop shortcut", exc_info=True)
            return False

    # -----------------------------------------------------------------
    # Avatar generation methods (M8.2)
    # -----------------------------------------------------------------

    def get_avatar_description(self) -> str:
        """Get the user's avatar description text."""
        return getattr(self._working.avatar, 'description', '')

    def set_avatar_description(self, description: str):
        """Set the avatar description text."""
        # Avatar description stored temporarily; not in final config yet
        # Will be used during generation
        if not hasattr(self._working.avatar, '_temp_description'):
            object.__setattr__(self._working.avatar, '_temp_description', '')
        object.__setattr__(self._working.avatar, '_temp_description', description.strip())

    def get_avatar_enabled(self) -> bool:
        """Check if avatar is enabled (vs. circle)."""
        return self._working.avatar.enabled

    def set_avatar_enabled(self, enabled: bool):
        """Enable or disable avatar (use circle if disabled)."""
        self._working.avatar.enabled = enabled

    # -- avatar presets (M8/v2.0.0-beta wizard "Your Avatar" step) --------
    #
    # The wizard offers a small set of one-click starter avatars so a new user
    # gets a face without needing the (optional) AI generator. Each preset maps
    # to a bundled default sprite gender and a sensibly paired TTS voice, and
    # flips ``avatar.enabled`` on. The "circle" preset keeps the classic
    # listening circle (avatar disabled) for users who prefer it or whose
    # hardware can't drive a sprite.

    #: (preset_id, label, description, gender) for the wizard avatar picker.
    AVATAR_PRESETS: Tuple[Tuple[str, str, str, str], ...] = (
        ("feminine", "Feminine",
         "A friendly feminine character, paired with a feminine voice.",
         "female"),
        ("masculine", "Masculine",
         "A friendly masculine character, paired with a masculine voice.",
         "male"),
        ("neutral", "Neutral",
         "A gender-neutral character with a neutral voice.",
         "neutral"),
        ("circle", "Classic circle",
         "Keep the classic listening circle \u2014 no character avatar.",
         "neutral"),
    )

    def avatar_preset_options(self) -> Tuple[Tuple[str, str, str], ...]:
        """Return ``(preset_id, label, description)`` rows for the avatar step."""
        return tuple((pid, label, desc) for pid, label, desc, _g in self.AVATAR_PRESETS)

    def _paired_voice_for_gender(self, gender: str) -> Optional[str]:
        """Best default voice_id for a gender preset (``None`` if unavailable)."""
        try:
            from mimosa.avatar.voice_library import get_voices_for_gender

            voices = get_voices_for_gender(gender)
            if voices:
                return voices[0].voice_id
        except Exception:  # pragma: no cover - defensive; voice lib optional
            logger.debug("Voice pairing lookup failed for %s", gender, exc_info=True)
        return None

    def get_selected_avatar_preset(self) -> str:
        """Return the currently-selected preset id.

        Derives the selection from the working config: if the avatar is
        disabled we report ``"circle"``; otherwise we match the paired voice /
        personalization gender back to a preset, defaulting to ``"neutral"``.
        """
        if not self._working.avatar.enabled:
            return "circle"
        gender = (self._working.personality.gender or "neutral").lower()
        for pid, _label, _desc, g in self.AVATAR_PRESETS:
            if pid != "circle" and g == gender:
                return pid
        return "neutral"

    def select_avatar_preset(self, preset_id: str) -> str:
        """Apply an avatar preset to the working config.

        Returns the applied preset id (falling back to ``"neutral"`` for an
        unknown id). The "circle" preset disables the avatar; every other
        preset enables it, records the detected render tier, and pairs a
        matching default voice.
        """
        valid = {pid for pid, _l, _d, _g in self.AVATAR_PRESETS}
        if preset_id not in valid:
            preset_id = "neutral"

        if preset_id == "circle":
            self._working.avatar.enabled = False
            self._working.avatar.tier = "circle_only"
            logger.info("Wizard: avatar preset 'circle' (classic listening circle)")
            return preset_id

        gender = next(g for pid, _l, _d, g in self.AVATAR_PRESETS if pid == preset_id)
        self._working.avatar.enabled = True
        # Pick the best render tier the host can drive; degrades to circle_only
        # transparently on constrained hardware.
        try:
            from mimosa.system.capability_detector import detect_avatar_tier

            self._working.avatar.tier = detect_avatar_tier()
        except Exception:  # pragma: no cover - defensive
            self._working.avatar.tier = "2d"
        # Keep persona gender + paired voice in sync with the chosen look.
        self._working.personality.gender = gender
        voice_id = self._paired_voice_for_gender(gender)
        if voice_id:
            self._working.avatar.voice_id = voice_id
        logger.info(
            "Wizard: avatar preset '%s' (gender=%s, tier=%s, voice=%s)",
            preset_id, gender, self._working.avatar.tier, voice_id,
        )
        return preset_id

    # -- voice picker (v2.0.0-beta wizard "Your Voice" step, item #7) ------

    def voice_options(self) -> Tuple[Tuple[str, str, str], ...]:
        """Return ``(voice_id, label, description)`` for every catalog voice.

        Backs the wizard's voice dropdown so users pick a named voice instead
        of guessing an opaque model id. Degrades to an empty tuple if the voice
        library is unavailable.
        """
        try:
            from mimosa.avatar.voice_library import (
                format_voice_description,
                get_all_voices,
            )

            return tuple(
                (v.voice_id, v.name, format_voice_description(v))
                for v in get_all_voices()
            )
        except Exception:  # pragma: no cover - defensive; voice lib optional
            logger.debug("Voice catalog unavailable", exc_info=True)
            return ()

    def get_selected_voice(self) -> Optional[str]:
        """Return the currently-selected voice id (avatar voice or default)."""
        return self._working.avatar.voice_id

    def set_selected_voice(self, voice_id: Optional[str]) -> Optional[str]:
        """Record the chosen TTS voice on the working config."""
        voice_id = (voice_id or "").strip() or None
        self._working.avatar.voice_id = voice_id
        return voice_id

    def get_avatar_custom_sprite_path(self) -> Optional[str]:
        """Get the path to the custom avatar sprite."""
        return self._working.avatar.custom_sprite_path

    def set_avatar_custom_sprite_path(self, path: Optional[str]):
        """Set the path to the custom avatar sprite."""
        self._working.avatar.custom_sprite_path = path

    def generate_avatar(
        self,
        description: str,
        gender: Optional[str] = None
    ) -> Optional[Path]:
        """
        Generate an avatar from description.
        
        Args:
            description: Text description of desired avatar
            gender: Optional gender preference (uses personalization if not provided)
        
        Returns:
            Path to generated avatar, or None if generation failed
        """
        try:
            from mimosa.avatar.generator import AvatarGenerator
            from mimosa.avatar.cache_manager import AvatarCacheManager
            
            # Use personalization gender if not explicitly provided
            if gender is None:
                gender = self._working.personalization.gender or 'neutral'
            
            # Generate avatar
            generator = AvatarGenerator()
            cache_manager = AvatarCacheManager()
            
            # Generate to temp location
            import tempfile
            temp_dir = Path(tempfile.mkdtemp())
            temp_sprite = temp_dir / "avatar.png"
            
            result = generator.generate_avatar(
                description=description,
                gender=gender,
                output_path=temp_sprite,
                method='auto'  # Try AI, fall back to default
            )
            
            if result and result.exists():
                # Cache the avatar
                avatar_id = cache_manager.save_avatar(
                    sprite_path=result,
                    description=description,
                    gender=gender,
                    source='setup_wizard'
                )
                
                # Get cached path
                cached_path = cache_manager.load_avatar(avatar_id)
                
                # Clean up temp
                if temp_sprite.exists() and temp_sprite != cached_path:
                    temp_sprite.unlink()
                if temp_dir.exists():
                    temp_dir.rmdir()
                
                return cached_path
            
            return None
            
        except Exception as e:
            logger.warning("Avatar generation failed: %s", e, exc_info=True)
            return None

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
