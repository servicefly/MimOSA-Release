"""Pure (GTK-free) controller backing the Settings dialog (M3.3).

The GTK view in :mod:`mimosa.ui.settings_dialog` is intentionally a *thin*
shell: every piece of behaviour that can be tested without a display lives
here. :class:`SettingsController` holds a **working copy** of the configuration
(so edits can be applied or cancelled), exposes a declarative description of
the pages/fields the dialog renders, validates and clamps edits, detects when
a restart is required, manages skill enable/priority state, and commits the
result back to a :class:`~mimosa.utils.config.AppConfigManager`.

This separation mirrors the M3.1/M3.2 approach (``ui_config`` /
``mouth_animator`` logic kept independent of GTK) and keeps the dialog fully
unit-testable on a headless machine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mimosa.utils.config import (
    AppConfig,
    AppConfigManager,
    DEFAULT_SKILL_ORDER,
    LLM_PROVIDERS,
    MAX_HISTORY_LIMIT,
    MAX_MAX_CONCURRENT_TASKS,
    MAX_RESEARCH_MAX_SOURCES,
    MAX_RESOURCE_THRESHOLD,
    MAX_TTS_SPEED,
    MAX_WAKE_SENSITIVITY,
    MIN_HISTORY_LIMIT,
    MIN_MAX_CONCURRENT_TASKS,
    MIN_RESEARCH_MAX_SOURCES,
    MIN_RESOURCE_THRESHOLD,
    MIN_TTS_SPEED,
    MIN_WAKE_SENSITIVITY,
    RESEARCH_BACKENDS,
    VALID_GENDERS,
    VALID_QUESTION_FREQUENCIES,
    VALID_VERBOSITY,
    WHISPER_MODELS,
)
from mimosa.ui.ui_config import (
    ANIMATION_STYLES,
    COLOR_THEMES,
    MAX_ANIM_SPEED,
    MAX_OPACITY,
    MAX_SIZE,
    MIN_ANIM_SPEED,
    MIN_OPACITY,
    MIN_SIZE,
    MOUTH_STYLES,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Declarative page / field descriptors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """Describes a single editable setting for the dialog to render.

    ``kind`` is one of: ``bool``, ``int``, ``float``, ``choice``, ``text``.
    For ``choice`` fields, ``choices`` lists the allowed values. ``minimum`` /
    ``maximum`` / ``step`` apply to numeric fields.
    """

    section: str
    name: str
    label: str
    kind: str
    help: str = ""
    choices: Tuple[Any, ...] = ()
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    step: Optional[float] = None
    restart: bool = False  # changing this field requires a restart to take effect


@dataclass(frozen=True)
class PageSpec:
    """A settings page: an id, a human title, an icon name, and its fields."""

    page_id: str
    title: str
    icon: str
    fields: Tuple[FieldSpec, ...] = ()


# -- Page identifiers (stable; used by the dialog & tests) -------------------
PAGE_VOICE = "voice"
PAGE_PERSONALIZE = "personalize"
PAGE_SKILLS = "skills"
PAGE_SYSTEM = "system"
PAGE_PRIVACY = "privacy"
PAGE_TASKS = "tasks"
PAGE_RESEARCH = "research"
PAGE_UI = "ui"
PAGE_LEARNING = "learning"
PAGE_ABOUT = "about"


def build_page_specs() -> Tuple[PageSpec, ...]:
    """Return the full, ordered page/field description used by the dialog."""
    voice = PageSpec(
        PAGE_VOICE, "Voice", "audio-input-microphone-symbolic",
        fields=(
            FieldSpec("voice", "wake_word", "Wake word", "text",
                      help="Phrase that activates listening.", restart=True),
            FieldSpec("voice", "wake_word_sensitivity", "Wake-word sensitivity",
                      "float", minimum=MIN_WAKE_SENSITIVITY,
                      maximum=MAX_WAKE_SENSITIVITY, step=0.05,
                      help="Higher = easier to trigger (more false positives)."),
            FieldSpec("voice", "stt_model", "Speech-to-text model", "choice",
                      choices=WHISPER_MODELS, restart=True,
                      help="Larger models are more accurate but slower."),
            FieldSpec("voice", "tts_voice", "Text-to-speech voice", "text",
                      help="Piper voice id (blank = engine default)."),
            FieldSpec("voice", "tts_speed", "Speech speed", "float",
                      minimum=MIN_TTS_SPEED, maximum=MAX_TTS_SPEED, step=0.1),
            FieldSpec("voice", "input_device", "Microphone (input)",
                      "device_input",
                      help="Pick the microphone MimOSA listens with "
                           "(system default if unsure).",
                      restart=True),
            FieldSpec("voice", "output_device", "Speaker (output)",
                      "device_output",
                      help="Pick the speaker MimOSA talks through "
                           "(system default if unsure).",
                      restart=True),
        ),
    )
    skills = PageSpec(
        PAGE_SKILLS, "Skills", "applications-system-symbolic",
        # Skills are rendered via a dedicated list widget, not generic fields.
        fields=(),
    )
    system = PageSpec(
        PAGE_SYSTEM, "System Integration", "preferences-system-symbolic",
        fields=(
            FieldSpec("system", "file_operations_enabled", "Allow file operations",
                      "bool", help="Create, move, search files on request."),
            FieldSpec("system", "app_control_enabled", "Allow application control",
                      "bool", help="Launch and focus applications."),
            FieldSpec("system", "system_controls_enabled", "Allow system controls",
                      "bool", help="Volume, brightness, lock, and similar."),
            FieldSpec("system", "safe_mode", "Safe mode", "bool",
                      help="Force confirmation for destructive & system actions."),
            FieldSpec("system", "confirm_destructive", "Confirm destructive actions",
                      "bool", help="Ask before deleting or overwriting."),
            FieldSpec("system", "confirm_app_launch", "Confirm app launches", "bool"),
            FieldSpec("system", "confirm_system_controls",
                      "Confirm system controls", "bool"),
        ),
    )
    privacy = PageSpec(
        PAGE_PRIVACY, "Privacy & Data", "security-high-symbolic",
        fields=(
            FieldSpec("privacy", "llm_provider", "LLM provider", "choice",
                      choices=LLM_PROVIDERS, restart=True,
                      help="'none' disables the LLM (skills only, fully offline)."),
            FieldSpec("privacy", "store_history", "Store conversation history",
                      "bool"),
            FieldSpec("privacy", "conversation_history_limit",
                      "History limit (turns)", "int",
                      minimum=MIN_HISTORY_LIMIT, maximum=MAX_HISTORY_LIMIT, step=1),
            FieldSpec("privacy", "data_retention_days", "Data retention (days)",
                      "int", minimum=0, maximum=3650, step=1,
                      help="0 = keep only for the current session."),
        ),
    )
    ui = PageSpec(
        PAGE_UI, "Appearance", "preferences-desktop-display-symbolic",
        fields=(
            FieldSpec("ui", "size", "Avatar size (px)", "int",
                      minimum=MIN_SIZE, maximum=MAX_SIZE, step=10),
            FieldSpec("ui", "opacity", "Opacity", "float",
                      minimum=MIN_OPACITY, maximum=MAX_OPACITY, step=0.05),
            FieldSpec("ui", "theme", "Color scheme", "choice",
                      choices=tuple(COLOR_THEMES.keys())),
            FieldSpec("ui", "animation_style", "Animation style", "choice",
                      choices=ANIMATION_STYLES),
            FieldSpec("ui", "animation_speed", "Animation speed", "float",
                      minimum=MIN_ANIM_SPEED, maximum=MAX_ANIM_SPEED, step=0.1),
            FieldSpec("ui", "animations_enabled", "Enable animations", "bool"),
            FieldSpec("ui", "always_on_top", "Always on top", "bool"),
            FieldSpec("ui", "lipsync_enabled", "Lip-sync (mouth animation)", "bool"),
            FieldSpec("ui", "mouth_style", "Mouth style", "choice",
                      choices=MOUTH_STYLES),
        ),
    )
    personalize = PageSpec(
        PAGE_PERSONALIZE, "Personalization", "avatar-default-symbolic",
        fields=(
            FieldSpec("personality", "user_name", "What should I call you?",
                      "text",
                      help="Your preferred name. Leave blank to skip."),
            FieldSpec("personality", "assistant_name",
                      "What would you like to call me?", "text",
                      help="A name for your assistant (defaults to 'MimOSA')."),
            FieldSpec("personality", "user_pronouns", "Your pronouns", "text",
                      help="Optional, e.g. 'she/her'. Used to personalise phrasing."),
            FieldSpec("personality", "verbosity", "Response style", "choice",
                      choices=VALID_VERBOSITY,
                      help="How chatty MimOSA should be."),
            FieldSpec("personality", "gender", "Voice style", "choice",
                      choices=VALID_GENDERS,
                      help="Preferred voice/persona style. 'neutral' leaves it "
                           "unspecified; 'female'/'male' bias the spoken voice."),
            FieldSpec("personality", "greet_by_name", "Greet me by name", "bool",
                      help="Say hello using your name when MimOSA starts."),
        ),
    )
    tasks = PageSpec(
        PAGE_TASKS, "Background Tasks", "system-run-symbolic",
        fields=(
            FieldSpec("tasks", "background_tasks_enabled",
                      "Enable background tasks", "bool",
                      help="Run long jobs in the background. Off = nothing queued."),
            FieldSpec("tasks", "max_concurrent", "Max concurrent tasks", "int",
                      minimum=MIN_MAX_CONCURRENT_TASKS,
                      maximum=MAX_MAX_CONCURRENT_TASKS, step=1,
                      help="How many tasks may run at once."),
            FieldSpec("tasks", "resource_monitoring",
                      "Defer when system is busy", "bool",
                      help="Use CPU/memory load to gate new task starts (needs psutil)."),
            FieldSpec("tasks", "cpu_threshold", "CPU busy threshold (%)", "float",
                      minimum=MIN_RESOURCE_THRESHOLD, maximum=MAX_RESOURCE_THRESHOLD,
                      step=1.0, help="Defer new tasks at/above this CPU load."),
            FieldSpec("tasks", "mem_threshold", "Memory busy threshold (%)",
                      "float", minimum=MIN_RESOURCE_THRESHOLD,
                      maximum=MAX_RESOURCE_THRESHOLD, step=1.0,
                      help="Defer new tasks at/above this memory use."),
            FieldSpec("tasks", "learn_error_fixes", "Learn error fixes", "bool",
                      help="Remember which fixes resolved past errors (local only)."),
        ),
    )
    research = PageSpec(
        PAGE_RESEARCH, "Web Research", "system-search-symbolic",
        fields=(
            FieldSpec("research", "web_search_enabled", "Enable web search",
                      "bool",
                      help="Off by default. When on, MimOSA may fetch web sources."),
            FieldSpec("research", "backend", "Search backend", "choice",
                      choices=RESEARCH_BACKENDS,
                      help="'none' keeps research fully offline even when enabled."),
            FieldSpec("research", "max_sources", "Max sources per query", "int",
                      minimum=MIN_RESEARCH_MAX_SOURCES,
                      maximum=MAX_RESEARCH_MAX_SOURCES, step=1,
                      help="How many sources to synthesise per question."),
            FieldSpec("research", "include_budget_note",
                      "Mention when sources were trimmed", "bool",
                      help="Append a short note when the budget limited sources."),
            FieldSpec("research", "learn_cost_patterns", "Learn cost patterns",
                      "bool",
                      help="Remember per-topic cost/budget patterns (local only)."),
        ),
    )
    learning = PageSpec(
        PAGE_LEARNING, "Learning & Memory", "emblem-favorite-symbolic",
        fields=(
            FieldSpec("learning", "learn_from_conversations",
                      "Learn from our conversations", "bool",
                      help="Let MimOSA quietly pick up facts and preferences as "
                           "you chat (always local, never shared)."),
            FieldSpec("learning", "allow_questions",
                      "Let me ask the occasional question", "bool",
                      help="Allow MimOSA to ask a friendly follow-up now and then "
                           "to get to know you better."),
            FieldSpec("learning", "question_frequency", "How often I can ask",
                      "choice", choices=VALID_QUESTION_FREQUENCIES,
                      help="'rarely' = at most one a day; 'often' = a few a day."),
            FieldSpec("learning", "proactive_suggestions",
                      "Offer helpful suggestions", "bool",
                      help="Let MimOSA gently suggest things based on your "
                           "habits (e.g. opening a tool you use every morning)."),
        ),
    )
    about = PageSpec(PAGE_ABOUT, "About", "help-about-symbolic", fields=())
    return (voice, personalize, skills, system, privacy, tasks, research, ui,
            learning, about)


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


@dataclass
class SkillRow:
    """A skill as presented in the Skills page list."""

    skill_id: str
    label: str
    enabled: bool
    priority: int
    uses_llm: bool = False
    description: str = ""


class SettingsController:
    """Stateful, GTK-free brain for the Settings dialog.

    Holds a working copy of :class:`AppConfig`; edits accumulate there until
    :meth:`apply` commits them to the manager (and disk). :meth:`cancel`
    discards them.
    """

    #: Fields whose change requires an app restart to take full effect.
    RESTART_FIELDS = frozenset({
        ("voice", "wake_word"),
        ("voice", "stt_model"),
        ("voice", "input_device"),
        ("voice", "output_device"),
        ("privacy", "llm_provider"),
    })

    def __init__(
        self,
        manager: AppConfigManager,
        *,
        skills_provider: Optional[Callable[[], Sequence[Any]]] = None,
        on_clear_history: Optional[Callable[[], int]] = None,
    ) -> None:
        self._manager = manager
        self._skills_provider = skills_provider
        self._on_clear_history = on_clear_history
        self._pages = build_page_specs()
        # Working copy: a deep, independent clone of the committed config.
        self._working = self._clone(manager.get())
        self._ensure_skill_rows()

    # -- cloning helpers ---------------------------------------------------

    @staticmethod
    def _clone(cfg: AppConfig) -> AppConfig:
        return AppConfig.from_dict(cfg.to_dict())

    # -- page/field introspection -----------------------------------------

    @property
    def pages(self) -> Tuple[PageSpec, ...]:
        return self._pages

    def page(self, page_id: str) -> Optional[PageSpec]:
        for p in self._pages:
            if p.page_id == page_id:
                return p
        return None

    @property
    def working_config(self) -> AppConfig:
        return self._working

    # -- generic field get/set --------------------------------------------

    def get_value(self, section: str, name: str) -> Any:
        target = getattr(self._working, section)
        return getattr(target, name)

    def set_value(self, section: str, name: str, value: Any) -> Any:
        """Set a working-copy field, validating/clamping the whole section.

        Returns the (possibly clamped) value actually stored.
        """
        target = getattr(self._working, section, None)
        if target is None or not hasattr(target, name):
            raise KeyError(f"{section}.{name} is not a valid setting")
        setattr(target, name, value)
        target.validate()
        return getattr(target, name)

    # -- skills ------------------------------------------------------------

    def _discover_skills(self) -> List[Tuple[str, str, bool, str]]:
        """Return ``(skill_id, label, uses_llm, description)`` tuples.

        Uses the injected provider when available (real :class:`IntentRouter`
        skills); otherwise falls back to the canonical default list so the page
        is populated even on a headless/test machine.
        """
        rows: List[Tuple[str, str, bool, str]] = []
        if self._skills_provider is not None:
            try:
                for skill in self._skills_provider():
                    sid = getattr(skill, "name", None) or str(skill)
                    label = sid.replace("_", " ").title()
                    uses_llm = bool(getattr(skill, "uses_llm", False))
                    desc = (getattr(skill, "__doc__", "") or "").strip().split("\n")[0]
                    rows.append((sid, label, uses_llm, desc))
            except Exception:  # pragma: no cover - provider faults are non-fatal
                logger.exception("skills_provider failed; using defaults")
                rows = []
        if not rows:
            rows = [(sid, sid.replace("_", " ").title(), False, "") for sid in DEFAULT_SKILL_ORDER]
        return rows

    def _ensure_skill_rows(self) -> None:
        """Make sure every discovered skill has enable/order state."""
        skills = self._working.skills
        for sid, _label, _llm, _desc in self._discover_skills():
            skills.enabled.setdefault(sid, True)
            if sid not in skills.order:
                skills.order.append(sid)
        skills.validate()

    def skill_rows(self) -> List[SkillRow]:
        """Return skills in priority order for the Skills page."""
        meta = {sid: (label, llm, desc)
                for sid, label, llm, desc in self._discover_skills()}
        skills = self._working.skills
        rows: List[SkillRow] = []
        for sid in skills.order:
            label, llm, desc = meta.get(sid, (sid.replace("_", " ").title(), False, ""))
            rows.append(SkillRow(
                skill_id=sid,
                label=label,
                enabled=skills.is_enabled(sid),
                priority=skills.priority_of(sid),
                uses_llm=llm,
                description=desc,
            ))
        return rows

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> None:
        self._working.skills.set_enabled(skill_id, enabled)

    def move_skill(self, skill_id: str, delta: int) -> bool:
        """Move a skill up (delta<0) or down (delta>0) in priority.

        Returns ``True`` if the order changed.
        """
        order = self._working.skills.order
        if skill_id not in order:
            return False
        idx = order.index(skill_id)
        new_idx = max(0, min(len(order) - 1, idx + delta))
        if new_idx == idx:
            return False
        order.pop(idx)
        order.insert(new_idx, skill_id)
        return True

    def set_skill_order(self, order: Sequence[str]) -> None:
        """Replace the priority order (validation re-appends any missing ids)."""
        self._working.skills.order = list(order)
        self._working.skills.validate()

    # -- validation & dirty/restart detection ------------------------------

    def validate(self) -> AppConfig:
        return self._working.validate()

    def is_dirty(self) -> bool:
        """True if the working copy differs from the committed config."""
        return self._working.to_dict() != self._manager.get().to_dict()

    def changed_fields(self) -> List[Tuple[str, str]]:
        """Return ``(section, field)`` pairs that differ from the committed config."""
        current = self._manager.get().to_dict()
        working = self._working.to_dict()
        changed: List[Tuple[str, str]] = []
        for section in ("voice", "skills", "system", "privacy", "ui"):
            cur = current.get(section, {})
            wrk = working.get(section, {})
            keys = set(cur) | set(wrk)
            for key in keys:
                if cur.get(key) != wrk.get(key):
                    changed.append((section, key))
        return changed

    def restart_required(self) -> bool:
        """True if any restart-sensitive field was changed."""
        changed = set(self.changed_fields())
        return bool(changed & self.RESTART_FIELDS)

    # -- commit / discard --------------------------------------------------

    def apply(self) -> bool:
        """Commit the working copy to the manager and persist it.

        After a successful apply the working copy is re-cloned from the freshly
        committed config so :meth:`is_dirty` reports clean.
        """
        ok = self._manager.replace(self._clone(self._working), persist=True)
        # Re-sync working copy with whatever the manager now holds.
        self._working = self._clone(self._manager.get())
        return ok

    def cancel(self) -> None:
        """Discard edits, reverting the working copy to the committed config."""
        self._working = self._clone(self._manager.get())
        self._ensure_skill_rows()

    def reset_defaults(self) -> None:
        """Reset the *working copy* to defaults (not persisted until apply)."""
        self._working = AppConfig()
        self._ensure_skill_rows()

    # -- actions -----------------------------------------------------------

    def clear_history(self) -> int:
        """Invoke the clear-history hook; returns turns cleared (0 if no hook)."""
        if self._on_clear_history is None:
            logger.debug("clear_history requested but no hook is wired")
            return 0
        try:
            result = self._on_clear_history()
            return int(result) if result is not None else 0
        except Exception:  # pragma: no cover
            logger.exception("clear_history hook failed")
            return 0

    def reset_avatar_position(self) -> None:
        """Clear the saved avatar window position (working copy)."""
        self._working.ui.pos_x = None
        self._working.ui.pos_y = None

    # -- summaries for the About / Privacy pages ---------------------------

    def privacy_summary(self) -> str:
        return self._working.privacy.privacy_summary()

    def check_for_updates(self, checker=None):
        """Check for a newer MimOSA release (About page action, M4.2).

        Returns an :class:`~mimosa.utils.updates.UpdateInfo`. The ``checker`` is
        injectable for tests; by default a network-backed
        :class:`~mimosa.utils.updates.UpdateChecker` is used. Never raises --
        failures are reported via ``UpdateInfo.error``.
        """
        from mimosa.utils.updates import UpdateChecker

        chk = checker or UpdateChecker()
        return chk.check()
