"""Unified application configuration for MimOSA (M3.3).

This module is the single source of truth for *all* user-tunable preferences:
voice/audio, skills, system-integration safety toggles, privacy/data, and the
existing UI/avatar preferences (which continue to live in
:class:`mimosa.ui.ui_config.UIConfig`).

Design goals
------------
* **Privacy-first & local.** Everything is stored in a small JSON file under
  the user's XDG config dir (``~/.config/mimosa/settings.json`` by default).
  Nothing is ever transmitted off the device; there is no telemetry.
* **Zero GUI dependencies.** Like :mod:`mimosa.ui.ui_config`, this module never
  imports GTK/Cairo or any heavy audio/ML library, so it loads and unit-tests
  cleanly on a headless machine. Option lists (Whisper models, providers, ...)
  are mirrored here as plain constants to avoid importing those subsystems.
* **Robust I/O.** Loading never raises on a missing/corrupt file; it degrades
  to defaults. Saving is atomic (temp file + ``os.replace``).
* **Versioned & migratable.** The on-disk payload carries a ``version`` field;
  :func:`_migrate` upgrades older layouts in place so future schema changes
  never brick an existing install.
* **Thread-safe.** :class:`AppConfigManager` guards all reads/writes with a
  re-entrant lock, so background voice threads and the GTK main loop can share
  one manager safely.
* **Backward compatible.** The avatar window and many tests still load
  ``ui.json`` directly via :class:`UIConfig`. The manager keeps that file in
  sync: it seeds the ``ui`` section from ``ui.json`` when ``settings.json`` is
  absent, and mirrors the ``ui`` section back to ``ui.json`` on every save.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mimosa.ui.ui_config import UIConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version & option catalogues
# ---------------------------------------------------------------------------

#: Bump whenever the on-disk schema changes in a way that needs migration.
CONFIG_VERSION = 1

#: Whisper STT model sizes (mirrors :mod:`mimosa.voice.stt`).
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large")
DEFAULT_WHISPER_MODEL = "base"

#: LLM providers. ``none`` disables the LLM entirely (skills-only operation).
LLM_PROVIDERS = ("abacus", "local", "none")
DEFAULT_LLM_PROVIDER = "abacus"

#: Wake-word sensitivity / TTS speed bounds.
MIN_WAKE_SENSITIVITY = 0.0
MAX_WAKE_SENSITIVITY = 1.0
DEFAULT_WAKE_SENSITIVITY = 0.5

MIN_TTS_SPEED = 0.5
MAX_TTS_SPEED = 2.0
DEFAULT_TTS_SPEED = 1.0

DEFAULT_WAKE_WORD = "hey mimosa"

#: Conversation-history bounds.
MIN_HISTORY_LIMIT = 1
MAX_HISTORY_LIMIT = 500
DEFAULT_HISTORY_LIMIT = 20

#: Data-retention: 0 means "keep until the user clears it" (session-scoped in
#: practice, since history is never written to disk by default).
MIN_RETENTION_DAYS = 0
MAX_RETENTION_DAYS = 3650
DEFAULT_RETENTION_DAYS = 0

#: Canonical skill identifiers shipped with MimOSA. Used to seed default
#: enable/priority state. Unknown skills discovered at runtime are appended.
DEFAULT_SKILL_ORDER = (
    "time",
    "calculator",
    "weather",
    "file_operations",
    "application",
    "system_control",
    "system_info",
    "greeting",
    "research",
    "tasks",
    "question",
)

# Background tasks / advanced features (M7) defaults and bounds.
DEFAULT_MAX_CONCURRENT_TASKS = 2
MIN_MAX_CONCURRENT_TASKS = 1
MAX_MAX_CONCURRENT_TASKS = 8
DEFAULT_TASK_CPU_THRESHOLD = 85.0
DEFAULT_TASK_MEM_THRESHOLD = 85.0
MIN_RESOURCE_THRESHOLD = 10.0
MAX_RESOURCE_THRESHOLD = 100.0

# Research (M6) defaults and bounds.
DEFAULT_RESEARCH_MAX_SOURCES = 6
MIN_RESEARCH_MAX_SOURCES = 1
MAX_RESEARCH_MAX_SOURCES = 25
DEFAULT_RESEARCH_TOKEN_BUDGET = 3000
MIN_RESEARCH_TOKEN_BUDGET = 256
MAX_RESEARCH_TOKEN_BUDGET = 200000
DEFAULT_RESEARCH_PER_CATEGORY_CAP = 3
MIN_RESEARCH_PER_CATEGORY_CAP = 1
MAX_RESEARCH_PER_CATEGORY_CAP = 25
RESEARCH_BACKENDS = ("none", "duckduckgo")
DEFAULT_RESEARCH_BACKEND = "duckduckgo"

# -- Personalisation ("Get to Know MimOSA") ---------------------------------
DEFAULT_ASSISTANT_NAME = "MimOSA"
#: Hard cap on stored personalisation strings so a pasted essay can't bloat the
#: config or a spoken greeting. Values are trimmed, never rejected.
MAX_PERSONALIZATION_LEN = 80
VALID_VERBOSITY = ("brief", "balanced", "detailed")
DEFAULT_VERBOSITY = "balanced"


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def default_config_path() -> Path:
    """Return the unified-config path, honoring ``MIMOSA_CONFIG`` & XDG.

    Order of precedence:

    1. ``MIMOSA_CONFIG`` env var (used by tests and power users).
    2. ``$XDG_CONFIG_HOME/mimosa/settings.json``.
    3. ``~/.config/mimosa/settings.json``.
    """
    override = os.environ.get("MIMOSA_CONFIG")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "mimosa" / "settings.json"


# ---------------------------------------------------------------------------
# Section dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VoiceSettings:
    """Wake-word, STT, TTS, and audio-device preferences."""

    wake_word: str = DEFAULT_WAKE_WORD
    wake_word_sensitivity: float = DEFAULT_WAKE_SENSITIVITY
    stt_model: str = DEFAULT_WHISPER_MODEL
    tts_voice: str = ""          # Piper voice id; "" => engine default
    tts_speed: float = DEFAULT_TTS_SPEED
    input_device: str = ""       # "" => system default input
    output_device: str = ""      # "" => system default output

    def validate(self) -> "VoiceSettings":
        if not isinstance(self.wake_word, str) or not self.wake_word.strip():
            self.wake_word = DEFAULT_WAKE_WORD
        else:
            self.wake_word = self.wake_word.strip()

        try:
            self.wake_word_sensitivity = round(
                _clamp(float(self.wake_word_sensitivity),
                       MIN_WAKE_SENSITIVITY, MAX_WAKE_SENSITIVITY), 3
            )
        except (TypeError, ValueError):
            self.wake_word_sensitivity = DEFAULT_WAKE_SENSITIVITY

        if self.stt_model not in WHISPER_MODELS:
            self.stt_model = DEFAULT_WHISPER_MODEL

        self.tts_voice = str(self.tts_voice or "")

        try:
            self.tts_speed = round(
                _clamp(float(self.tts_speed), MIN_TTS_SPEED, MAX_TTS_SPEED), 3
            )
        except (TypeError, ValueError):
            self.tts_speed = DEFAULT_TTS_SPEED

        self.input_device = str(self.input_device or "")
        self.output_device = str(self.output_device or "")
        return self


@dataclass
class SkillsSettings:
    """Per-skill enable flags and priority ordering.

    ``enabled`` maps a skill id to a bool. ``order`` is the priority sequence
    (earlier = higher priority). Skills missing from either container fall back
    to "enabled" and are appended to the order on validation.
    """

    enabled: Dict[str, bool] = field(default_factory=dict)
    order: List[str] = field(default_factory=lambda: list(DEFAULT_SKILL_ORDER))
    custom: List[Dict[str, Any]] = field(default_factory=list)  # future: user skills

    def validate(self) -> "SkillsSettings":
        if not isinstance(self.enabled, dict):
            self.enabled = {}
        # Coerce values to bools, keys to str.
        self.enabled = {str(k): bool(v) for k, v in self.enabled.items()}

        if not isinstance(self.order, list) or not self.order:
            self.order = list(DEFAULT_SKILL_ORDER)
        else:
            # de-duplicate while preserving order; coerce to str
            seen: set = set()
            cleaned: List[str] = []
            for item in self.order:
                s = str(item)
                if s not in seen:
                    seen.add(s)
                    cleaned.append(s)
            self.order = cleaned

        # Ensure every default skill has an enabled entry (default True) and a
        # slot in the order.
        for sid in DEFAULT_SKILL_ORDER:
            self.enabled.setdefault(sid, True)
            if sid not in self.order:
                self.order.append(sid)

        # Normalise custom user-defined skills (M4.1). Each entry is stored as a
        # plain dict; we round-trip through CustomSkillSpec so a hand-edited file
        # is repaired rather than trusted blindly. Unusable entries are dropped.
        if not isinstance(self.custom, list):
            self.custom = []
        else:
            from mimosa.skills.custom_skill import CustomSkillSpec

            cleaned_custom: List[Dict[str, Any]] = []
            seen_ids: set = set()
            for entry in self.custom:
                try:
                    spec = CustomSkillSpec.from_dict(
                        entry if isinstance(entry, dict) else {}
                    )
                except Exception:  # pragma: no cover - defensive
                    continue
                if not spec.id or not spec.is_usable() or spec.id in seen_ids:
                    continue
                seen_ids.add(spec.id)
                cleaned_custom.append(spec.to_dict())
            self.custom = cleaned_custom
        return self

    def custom_specs(self):
        """Return the validated custom skills as ``CustomSkillSpec`` objects."""
        from mimosa.skills.custom_skill import CustomSkillSpec

        return [CustomSkillSpec.from_dict(entry) for entry in self.custom]

    def add_custom_skill(self, spec) -> Dict[str, Any]:
        """Add or replace (by id) a custom skill; returns the stored dict.

        ``spec`` may be a :class:`CustomSkillSpec` or a plain dict. Raises
        :class:`~mimosa.skills.custom_skill.CustomSkillError` if it is not a
        usable skill (needs a name, a trigger, and a response/LLM prompt).
        """
        from mimosa.skills.custom_skill import normalize_custom_spec

        normalized = normalize_custom_spec(spec)
        payload = normalized.to_dict()
        self.custom = [c for c in self.custom if c.get("id") != normalized.id]
        self.custom.append(payload)
        return payload

    def remove_custom_skill(self, skill_id: str) -> bool:
        """Remove a custom skill by id. Returns ``True`` if one was removed."""
        before = len(self.custom)
        self.custom = [c for c in self.custom if c.get("id") != str(skill_id)]
        return len(self.custom) < before

    def is_enabled(self, skill_id: str) -> bool:
        return bool(self.enabled.get(skill_id, True))

    def set_enabled(self, skill_id: str, value: bool) -> None:
        self.enabled[str(skill_id)] = bool(value)

    def priority_of(self, skill_id: str) -> int:
        """Return the 0-based priority index of ``skill_id`` (lower = higher)."""
        try:
            return self.order.index(skill_id)
        except ValueError:
            return len(self.order)


@dataclass
class SystemIntegrationSettings:
    """Safety toggles governing what MimOSA is allowed to do on the system."""

    file_operations_enabled: bool = True
    app_control_enabled: bool = True
    system_controls_enabled: bool = True
    safe_mode: bool = True
    confirm_destructive: bool = True
    confirm_app_launch: bool = False
    confirm_system_controls: bool = True

    def validate(self) -> "SystemIntegrationSettings":
        self.file_operations_enabled = bool(self.file_operations_enabled)
        self.app_control_enabled = bool(self.app_control_enabled)
        self.system_controls_enabled = bool(self.system_controls_enabled)
        self.safe_mode = bool(self.safe_mode)
        self.confirm_destructive = bool(self.confirm_destructive)
        self.confirm_app_launch = bool(self.confirm_app_launch)
        self.confirm_system_controls = bool(self.confirm_system_controls)
        # Safe mode forces confirmation on destructive & system actions.
        if self.safe_mode:
            self.confirm_destructive = True
            self.confirm_system_controls = True
        return self


@dataclass
class PrivacySettings:
    """LLM provider choice and conversation-history/data-retention policy."""

    llm_provider: str = DEFAULT_LLM_PROVIDER
    conversation_history_limit: int = DEFAULT_HISTORY_LIMIT
    store_history: bool = True
    data_retention_days: int = DEFAULT_RETENTION_DAYS
    #: Persist conversation history to the on-device SQLite store (M5.1) so
    #: context survives restarts. When False, history is session-only (RAM).
    persist_conversations: bool = True
    #: Silent background preference learning (M5.2). When False, MimOSA never
    #: records behavioural patterns.
    learn_preferences: bool = True
    #: Build on-device semantic memory for long-term recall (M5.3).
    semantic_memory: bool = True
    #: Let the Privacy Guard (M5.4) auto-detect sensitive topics and route them
    #: to a local-only model so they never reach the cloud.
    auto_private_mode: bool = True

    def validate(self) -> "PrivacySettings":
        if self.llm_provider not in LLM_PROVIDERS:
            self.llm_provider = DEFAULT_LLM_PROVIDER
        self.persist_conversations = bool(self.persist_conversations)
        self.learn_preferences = bool(self.learn_preferences)
        self.semantic_memory = bool(self.semantic_memory)
        self.auto_private_mode = bool(self.auto_private_mode)

        try:
            self.conversation_history_limit = int(
                _clamp(int(self.conversation_history_limit),
                       MIN_HISTORY_LIMIT, MAX_HISTORY_LIMIT)
            )
        except (TypeError, ValueError):
            self.conversation_history_limit = DEFAULT_HISTORY_LIMIT

        self.store_history = bool(self.store_history)

        try:
            self.data_retention_days = int(
                _clamp(int(self.data_retention_days),
                       MIN_RETENTION_DAYS, MAX_RETENTION_DAYS)
            )
        except (TypeError, ValueError):
            self.data_retention_days = DEFAULT_RETENTION_DAYS
        return self

    def privacy_summary(self) -> str:
        """Return a short human-readable summary of the privacy posture."""
        if self.llm_provider == "none":
            llm = "No LLM (skills-only; fully offline)"
        elif self.llm_provider == "local":
            llm = "Local LLM (on-device; nothing leaves your machine)"
        else:
            llm = "Abacus.AI cloud LLM (queries sent to Abacus.AI)"
        hist = (
            f"up to {self.conversation_history_limit} turns"
            if self.store_history else "history disabled"
        )
        retain = (
            "kept only for this session"
            if self.data_retention_days == 0
            else f"retained {self.data_retention_days} day(s)"
        )
        return f"{llm}. Conversation: {hist}, {retain}. All settings stored locally; no telemetry."


@dataclass
class TasksSettings:
    """Background task queue & advanced-features policy (M7).

    Privacy-first / local-first like the rest of MimOSA: tasks run on-device,
    nothing is reported anywhere, and resource monitoring is a local psutil
    read. Everything degrades gracefully when disabled or when psutil is absent.
    """

    #: Master switch for the background task queue. When False, the task-control
    #: skill simply reports there is nothing running and no workers start.
    background_tasks_enabled: bool = True
    #: Maximum tasks allowed to run concurrently (clamped to a sane range).
    max_concurrent: int = DEFAULT_MAX_CONCURRENT_TASKS
    #: Gate new task starts on system load via the psutil resource monitor.
    resource_monitoring: bool = True
    #: CPU percent at/above which the system is "busy" and new starts defer.
    cpu_threshold: float = DEFAULT_TASK_CPU_THRESHOLD
    #: Memory percent at/above which the system is "busy".
    mem_threshold: float = DEFAULT_TASK_MEM_THRESHOLD
    #: Learn which fixes resolve which errors (builds on M5.2 preference
    #: learning). When False, no error-fix observations are recorded.
    learn_error_fixes: bool = True

    def validate(self) -> "TasksSettings":
        self.background_tasks_enabled = bool(self.background_tasks_enabled)
        self.resource_monitoring = bool(self.resource_monitoring)
        self.learn_error_fixes = bool(self.learn_error_fixes)

        try:
            self.max_concurrent = int(
                _clamp(int(self.max_concurrent),
                       MIN_MAX_CONCURRENT_TASKS, MAX_MAX_CONCURRENT_TASKS)
            )
        except (TypeError, ValueError):
            self.max_concurrent = DEFAULT_MAX_CONCURRENT_TASKS

        try:
            self.cpu_threshold = float(
                _clamp(float(self.cpu_threshold),
                       MIN_RESOURCE_THRESHOLD, MAX_RESOURCE_THRESHOLD)
            )
        except (TypeError, ValueError):
            self.cpu_threshold = DEFAULT_TASK_CPU_THRESHOLD

        try:
            self.mem_threshold = float(
                _clamp(float(self.mem_threshold),
                       MIN_RESOURCE_THRESHOLD, MAX_RESOURCE_THRESHOLD)
            )
        except (TypeError, ValueError):
            self.mem_threshold = DEFAULT_TASK_MEM_THRESHOLD
        return self


@dataclass
class ResearchSettings:
    """Web research / multi-source synthesis policy (M6).

    Privacy-first defaults: web search is **disabled** out of the box so a
    fresh install never makes a surprise network request. The user must opt in
    (setup wizard or settings) before MimOSA will reach the internet to gather
    sources. Everything else degrades gracefully offline.
    """

    #: Master opt-in. When False, the research skill answers locally with a
    #: "web search is off" message and never touches the network.
    web_search_enabled: bool = False
    #: Search backend to use when ``web_search_enabled`` is True. ``"none"``
    #: keeps the pipeline fully offline even when research is enabled.
    backend: str = DEFAULT_RESEARCH_BACKEND
    #: Maximum number of sources fed into synthesis per query.
    max_sources: int = DEFAULT_RESEARCH_MAX_SOURCES
    #: Per-category cap so one perspective cannot crowd out the rest.
    per_category_cap: int = DEFAULT_RESEARCH_PER_CATEGORY_CAP
    #: Token budget for the evidence + synthesis call (negotiated down to fit).
    token_budget: int = DEFAULT_RESEARCH_TOKEN_BUDGET
    #: Append a short "budget note" to spoken answers when sources were trimmed.
    include_budget_note: bool = False
    #: Learn cost/budget patterns per topic (builds on M5.2 preference
    #: learning). When False, no research-cost observations are recorded.
    learn_cost_patterns: bool = True

    def validate(self) -> "ResearchSettings":
        self.web_search_enabled = bool(self.web_search_enabled)
        if self.backend not in RESEARCH_BACKENDS:
            self.backend = DEFAULT_RESEARCH_BACKEND
        self.include_budget_note = bool(self.include_budget_note)
        self.learn_cost_patterns = bool(self.learn_cost_patterns)

        try:
            self.max_sources = int(
                _clamp(int(self.max_sources),
                       MIN_RESEARCH_MAX_SOURCES, MAX_RESEARCH_MAX_SOURCES)
            )
        except (TypeError, ValueError):
            self.max_sources = DEFAULT_RESEARCH_MAX_SOURCES

        try:
            self.per_category_cap = int(
                _clamp(int(self.per_category_cap),
                       MIN_RESEARCH_PER_CATEGORY_CAP, MAX_RESEARCH_PER_CATEGORY_CAP)
            )
        except (TypeError, ValueError):
            self.per_category_cap = DEFAULT_RESEARCH_PER_CATEGORY_CAP

        try:
            self.token_budget = int(
                _clamp(int(self.token_budget),
                       MIN_RESEARCH_TOKEN_BUDGET, MAX_RESEARCH_TOKEN_BUDGET)
            )
        except (TypeError, ValueError):
            self.token_budget = DEFAULT_RESEARCH_TOKEN_BUDGET
        return self


@dataclass
class PersonalitySettings:
    """User personalisation captured by the "Get to Know MimOSA" wizard step.

    Everything here is optional and stored locally only -- it shapes how MimOSA
    greets and addresses the user. Empty strings mean "not provided" and the
    assistant falls back to neutral defaults. Strings are trimmed to
    :data:`MAX_PERSONALIZATION_LEN` so they stay safe to speak and store.
    """

    #: What the user would like to be called (e.g. "Sam"). Blank = unknown.
    user_name: str = ""
    #: What the user wants to call the assistant. Defaults to "MimOSA".
    assistant_name: str = DEFAULT_ASSISTANT_NAME
    #: Optional pronouns for the user, used to personalise phrasing if set.
    user_pronouns: str = ""
    #: How chatty MimOSA should be: "brief" | "balanced" | "detailed".
    verbosity: str = DEFAULT_VERBOSITY
    #: When True, MimOSA greets the user by name on startup.
    greet_by_name: bool = True

    def _trim(self, value) -> str:
        try:
            text = str(value).strip()
        except Exception:
            return ""
        return text[:MAX_PERSONALIZATION_LEN]

    def validate(self) -> "PersonalitySettings":
        self.user_name = self._trim(self.user_name)
        self.assistant_name = self._trim(self.assistant_name) or DEFAULT_ASSISTANT_NAME
        self.user_pronouns = self._trim(self.user_pronouns)
        verbosity = self._trim(self.verbosity).lower()
        self.verbosity = verbosity if verbosity in VALID_VERBOSITY else DEFAULT_VERBOSITY
        self.greet_by_name = bool(self.greet_by_name)
        return self

    def display_user(self) -> str:
        """A safe label for the user ("there" when no name is known)."""
        return self.user_name or "there"

    def greeting(self) -> str:
        """A friendly, personalised greeting line."""
        if self.greet_by_name and self.user_name:
            return f"Hi {self.user_name}, I'm {self.assistant_name}."
        return f"Hi, I'm {self.assistant_name}."


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


@dataclass
class AppConfig:
    """The complete MimOSA configuration tree.

    Embeds the existing :class:`UIConfig` as the ``ui`` section so avatar
    preferences are not duplicated.
    """

    version: int = CONFIG_VERSION
    first_run_complete: bool = False  # set once the setup wizard finishes (M4.2)
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    skills: SkillsSettings = field(default_factory=SkillsSettings)
    system: SystemIntegrationSettings = field(default_factory=SystemIntegrationSettings)
    privacy: PrivacySettings = field(default_factory=PrivacySettings)
    research: ResearchSettings = field(default_factory=ResearchSettings)
    tasks: TasksSettings = field(default_factory=TasksSettings)
    personality: PersonalitySettings = field(default_factory=PersonalitySettings)
    ui: UIConfig = field(default_factory=UIConfig)

    def validate(self) -> "AppConfig":
        try:
            self.version = int(self.version)
        except (TypeError, ValueError):
            self.version = CONFIG_VERSION
        self.first_run_complete = bool(self.first_run_complete)
        self.voice.validate()
        self.skills.validate()
        self.system.validate()
        self.privacy.validate()
        self.research.validate()
        self.tasks.validate()
        self.personality.validate()
        self.ui.validate()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "first_run_complete": self.first_run_complete,
            "voice": asdict(self.voice),
            "skills": asdict(self.skills),
            "system": asdict(self.system),
            "privacy": asdict(self.privacy),
            "research": asdict(self.research),
            "tasks": asdict(self.tasks),
            "personality": asdict(self.personality),
            "ui": self.ui.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """Build a config from a (possibly old/partial) dict, then validate."""
        data = _migrate(dict(data or {}))

        def _section(klass, key):
            raw = data.get(key) or {}
            if not isinstance(raw, dict):
                raw = {}
            known = {f.name for f in fields(klass)}
            filtered = {k: v for k, v in raw.items() if k in known}
            return klass(**filtered)

        cfg = cls(
            version=data.get("version", CONFIG_VERSION),
            first_run_complete=bool(data.get("first_run_complete", False)),
            voice=_section(VoiceSettings, "voice"),
            skills=_section(SkillsSettings, "skills"),
            system=_section(SystemIntegrationSettings, "system"),
            privacy=_section(PrivacySettings, "privacy"),
            research=_section(ResearchSettings, "research"),
            tasks=_section(TasksSettings, "tasks"),
            personality=_section(PersonalitySettings, "personality"),
            ui=UIConfig.from_dict(data.get("ui") or {}),
        )
        return cfg.validate()


def _migrate(data: Dict[str, Any]) -> Dict[str, Any]:
    """Upgrade an on-disk payload to the current :data:`CONFIG_VERSION`.

    Migrations are applied cumulatively. Unknown/future versions are left
    untouched (forward-compatible: unknown keys are ignored on load).
    """
    if not isinstance(data, dict):
        return {}

    version = data.get("version")
    if not isinstance(version, int):
        # Legacy / pre-versioned payloads -> treat as version 0.
        version = 0

    # --- v0 -> v1: introduce the sectioned layout ------------------------
    if version < 1:
        # A pre-versioned file may have been a flat UIConfig dump. If so, nest
        # it under "ui" so it is not lost.
        ui_markers = {"size", "opacity", "theme", "animation_style"}
        if "ui" not in data and ui_markers & set(data.keys()):
            ui_keys = {
                k: v for k, v in data.items()
                if k not in {"voice", "skills", "system", "privacy", "version"}
            }
            data = {"ui": ui_keys}
        data["version"] = 1
        version = 1

    # Future migrations: `if version < 2: ...`

    return data


# ---------------------------------------------------------------------------
# Thread-safe manager
# ---------------------------------------------------------------------------


class AppConfigManager:
    """Thread-safe load/save/observe wrapper around :class:`AppConfig`.

    All public methods acquire a re-entrant lock so the manager can be shared
    between the GTK main loop and background voice/worker threads.
    """

    def __init__(self, path: Optional[os.PathLike] = None,
                 config: Optional[AppConfig] = None) -> None:
        self._lock = threading.RLock()
        self._path = Path(path) if path is not None else default_config_path()
        self._config = config.validate() if config is not None else AppConfig()
        self._observers: List[Callable[[AppConfig], None]] = []

    # -- introspection -----------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def config(self) -> AppConfig:
        """Return the live config object (callers should treat as read-mostly)."""
        with self._lock:
            return self._config

    def get(self) -> AppConfig:
        with self._lock:
            return self._config

    # -- observers ---------------------------------------------------------

    def add_observer(self, callback: Callable[[AppConfig], None]) -> None:
        """Register ``callback`` to be invoked (with the config) after changes."""
        with self._lock:
            if callback not in self._observers:
                self._observers.append(callback)

    def remove_observer(self, callback: Callable[[AppConfig], None]) -> None:
        with self._lock:
            if callback in self._observers:
                self._observers.remove(callback)

    def _notify(self) -> None:
        # Snapshot observers + config under the lock, fire callbacks outside it
        # to avoid holding the lock during arbitrary user code.
        with self._lock:
            observers = list(self._observers)
            cfg = self._config
        for cb in observers:
            try:
                cb(cfg)
            except Exception:  # pragma: no cover - observer faults are non-fatal
                logger.exception("Config observer raised; continuing")

    # -- persistence -------------------------------------------------------

    def load(self) -> AppConfig:
        """Load (or initialize) the config from disk; never raises.

        If ``settings.json`` is missing, the ``ui`` section is seeded from the
        legacy ``ui.json`` (via :meth:`UIConfig.load`) so existing avatar
        preferences carry over to the unified file on first save.
        """
        with self._lock:
            try:
                with open(self._path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not isinstance(data, dict):
                    raise ValueError("config root is not an object")
                self._config = AppConfig.from_dict(data)
                logger.debug("Loaded app config from %s", self._path)
            except FileNotFoundError:
                logger.debug("No app config at %s; seeding from defaults/ui.json",
                             self._path)
                self._config = AppConfig(ui=UIConfig.load())
                self._config.validate()
            except Exception as exc:
                logger.warning("Could not read app config %s (%s); using defaults",
                               self._path, exc)
                self._config = AppConfig()
            return self._config

    def save(self) -> bool:
        """Atomically persist the config; also mirror the ``ui`` section.

        Returns ``True`` on success. Mirroring to ``ui.json`` keeps the avatar
        window (which loads :class:`UIConfig` directly) in sync. Never raises.
        """
        with self._lock:
            self._config.validate()
            payload = json.dumps(self._config.to_dict(), indent=2, sort_keys=True)
            ok = self._atomic_write(self._path, payload)
            # Mirror UI section to the legacy ui.json for backward compat.
            try:
                self._config.ui.save()
            except Exception:  # pragma: no cover
                logger.debug("ui.json mirror failed (non-fatal)")
        self._notify()
        return ok

    @staticmethod
    def _atomic_write(target: Path, payload: str) -> bool:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".settings.", suffix=".json",
                                       dir=str(target.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:  # pragma: no cover
                        pass
            logger.debug("Saved app config to %s", target)
            return True
        except Exception as exc:
            logger.warning("Could not save app config %s (%s)", target, exc)
            return False

    # -- mutation ----------------------------------------------------------

    def replace(self, config: AppConfig, *, persist: bool = True) -> bool:
        """Swap in a fully-formed config (e.g. the dialog's working copy)."""
        with self._lock:
            self._config = config.validate()
        if persist:
            return self.save()
        self._notify()
        return True

    def update_section(self, section: str, *, persist: bool = True, **changes) -> AppConfig:
        """Apply ``changes`` to one section (``voice``/``skills``/...) and save."""
        with self._lock:
            target = getattr(self._config, section, None)
            if target is None:
                raise KeyError(f"unknown config section: {section!r}")
            for key, value in changes.items():
                if hasattr(target, key):
                    setattr(target, key, value)
                else:
                    raise KeyError(f"{section}.{key} is not a valid field")
            self._config.validate()
        if persist:
            self.save()
        else:
            self._notify()
        return self._config

    def is_first_run(self) -> bool:
        """Whether the first-run setup wizard has *not* yet been completed (M4.2)."""
        with self._lock:
            return not bool(self._config.first_run_complete)

    def mark_first_run_complete(self, *, persist: bool = True) -> AppConfig:
        """Record that the setup wizard finished (so it won't show again)."""
        with self._lock:
            self._config.first_run_complete = True
        if persist:
            self.save()
        else:
            self._notify()
        return self._config

    def reset(self, *, persist: bool = True) -> AppConfig:
        """Reset all settings to defaults."""
        with self._lock:
            self._config = AppConfig()
        if persist:
            self.save()
        else:
            self._notify()
        return self._config
