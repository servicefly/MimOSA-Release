"""End-to-end integration tests for MimOSA (Milestone 8.4d).

These tests exercise the *assembled* application the way a real session would,
but fully hermetically: no GTK display, no audio devices, no network and no
LLM. Every collaborator that would normally touch the disk uses an in-memory
SQLite database (``:memory:``) or a ``tmp_path`` file, and the intent router
runs with ``llm_provider=None`` so every skill takes its deterministic local
fallback path.

What is covered:

* A multi-turn conversation routed through a real :class:`IntentRouter`, with
  the user's :class:`PersonalitySettings` flowing all the way into the spoken
  greeting ("Get to Know MimOSA" personalization).
* The graceful-error path: a skill that raises is converted into a friendly,
  speakable result and -- when the M7.3 learner already knows a fix -- enriched
  with that suggestion via the wired :class:`ErrorReporter`.
* The runtime service bundle (:class:`AppServices`) starting, running a data
  retention + vacuum maintenance pass against an in-memory conversation store,
  and shutting down cleanly.
* The console entry point's ``--check`` path returning success and printing the
  documented log location without booting any heavy subsystem.
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from mimosa.core.intent_router import IntentRouter
from mimosa.core.error_reporter import ErrorReporter
from mimosa.core.runtime import AppServices
from mimosa.memory.conversation_store import ConversationStore
from mimosa.memory.preference_learner import PreferenceLearner
from mimosa.skills.base_skill import BaseSkill, SkillResult
from mimosa.skills.greeting_skill import GreetingSkill
from mimosa.tasks.error_learner import ErrorFixLearner
from mimosa.utils.config import (
    AppConfig,
    PersonalitySettings,
    PrivacySettings,
    TasksSettings,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _ExplodingSkill(BaseSkill):
    """A skill whose handler always raises, to drive the graceful-error path.

    It claims the ``time`` intent so the deterministic heuristic classifier
    routes "what time is it?" straight to it without needing an LLM.
    """

    name = "exploding"
    intents = ["time"]

    def handle(self, text: str, context: Optional[List] = None) -> SkillResult:
        raise RuntimeError("Connection timed out while reaching the service")


@pytest.fixture()
def preference_learner():
    pl = PreferenceLearner(db_path=":memory:")
    yield pl
    pl.close()


@pytest.fixture()
def conversation_store():
    store = ConversationStore(db_path=":memory:")
    yield store
    store.close()


# ---------------------------------------------------------------------------
# Personalized multi-turn conversation
# ---------------------------------------------------------------------------


def test_personalized_greeting_end_to_end():
    """A greeting is answered locally and addresses the user by name."""
    personality = PersonalitySettings(
        user_name="Ada", assistant_name="Sol", greet_by_name=True
    )
    personality.validate()

    router = IntentRouter(llm_provider=None, personality=personality)
    result = router.route("hello there")

    assert result.success
    assert result.skill == "greeting"
    assert "Ada" in result.text
    assert result.metadata.get("intent") == "greeting"


def test_greeting_without_personality_is_generic():
    """No personality -> friendly but non-personalized fallback greeting."""
    router = IntentRouter(llm_provider=None)
    result = router.route("hi")

    assert result.success
    assert result.skill == "greeting"
    # The generic fallbacks never contain a name token we injected.
    assert "Ada" not in result.text


def test_multi_turn_conversation_is_deterministic_offline():
    """Several turns route to the right local skills without any LLM."""
    router = IntentRouter(llm_provider=None)

    greeting = router.route("hello")
    assert greeting.metadata.get("intent") == "greeting"

    time_result = router.route("what time is it?")
    assert time_result.metadata.get("intent") == "time"
    assert time_result.success

    calc = router.route("what is 2 + 2?")
    assert calc.metadata.get("intent") == "calculator"
    assert "4" in calc.text


# ---------------------------------------------------------------------------
# Graceful-error path with fix enrichment
# ---------------------------------------------------------------------------


def test_failed_skill_returns_friendly_message_not_traceback():
    """A raising skill becomes a graceful, speakable result (no traceback)."""
    reporter = ErrorReporter()  # no learner wired
    router = IntentRouter(
        llm_provider=None,
        skills=[_ExplodingSkill(), GreetingSkill(llm_provider=None)],
        error_reporter=reporter,
    )

    result = router.route("what time is it?")

    assert result.success is False
    assert result.text  # something speakable
    assert "Traceback" not in result.text
    assert "RuntimeError" not in result.text
    # The raw exception is preserved in metadata for diagnostics only.
    assert "timed out" in result.metadata.get("error", "")


def test_failed_skill_is_enriched_with_learned_fix(preference_learner):
    """When the learner knows a fix, it is appended to the spoken reply."""
    learner = ErrorFixLearner(preference_learner)
    # Teach the learner a remedy for this class of error (record repeatedly so
    # it clears the confidence threshold).
    err = "Connection timed out while reaching the service"
    for _ in range(5):
        assert learner.record_fix(err, "check your internet connection")

    reporter = ErrorReporter(learner=learner)
    router = IntentRouter(
        llm_provider=None,
        skills=[_ExplodingSkill(), GreetingSkill(llm_provider=None)],
        error_reporter=reporter,
    )

    result = router.route("what time is it?")

    assert result.success is False
    assert "check your internet connection" in result.text
    assert result.metadata.get("fix_suggestion") == "check your internet connection"
    assert "error_signature" in result.metadata


def test_error_reporter_report_never_raises():
    """ErrorReporter.report degrades to a generic message and never throws."""
    reporter = ErrorReporter()
    report = reporter.report(ValueError("boom"))
    assert report.spoken()
    # A bare, unhinted error falls back to the generic friendly message.
    assert isinstance(report.spoken(), str)


# ---------------------------------------------------------------------------
# Runtime services: maintenance lifecycle
# ---------------------------------------------------------------------------


def test_app_services_maintenance_purges_and_vacuums(tmp_path):
    """AppServices runs a retention purge + vacuum against a live store.

    Uses an on-disk store (not ``:memory:``) so ``VACUUM`` actually executes;
    the in-memory store treats vacuum as a safe no-op.
    """
    store = ConversationStore(db_path=str(tmp_path / "conv.db"))
    # Seed messages: one ancient (purgeable) and one fresh (retained).
    store.add_message("s1", role="user", content="ancient", timestamp=0.0)
    store.add_message("s1", role="assistant", content="recent")

    config = AppConfig()
    config.privacy = PrivacySettings(data_retention_days=30)
    config.tasks = TasksSettings(background_tasks_enabled=False)

    services = AppServices.from_config(config, conversation_store=store)
    services.start()
    try:
        report = services.run_maintenance(vacuum=True)
    finally:
        services.shutdown()

    assert report.ran is True
    # The 1970-epoch message is older than 30 days -> purged.
    assert report.messages_purged == 1
    assert report.conversation_vacuumed is True


def test_app_services_builds_error_stack_even_when_tasks_disabled():
    """The graceful-error stack is always available, tasks or not."""
    config = AppConfig()
    config.tasks = TasksSettings(
        background_tasks_enabled=False, resource_monitoring=False
    )
    services = AppServices.from_config(config)
    try:
        assert services.error_reporter is not None
        assert services.error_learner is not None
        # No task queue / monitor when those features are off.
        assert services.task_queue is None
        assert services.resource_monitor is None
    finally:
        services.shutdown()


def test_app_services_context_manager_roundtrip(conversation_store):
    """Using AppServices as a context manager starts and shuts down cleanly."""
    config = AppConfig()
    config.tasks = TasksSettings(background_tasks_enabled=False)
    services = AppServices.from_config(config, conversation_store=conversation_store)
    with services as live:
        assert live is services
    # After exit the bundle is shut down (idempotent second call is safe).
    services.shutdown()


# ---------------------------------------------------------------------------
# Console entry point --check
# ---------------------------------------------------------------------------


def test_main_check_reports_environment_and_log_location(capsys):
    """`mimosa --check` returns 0 and prints the documented log location."""
    from mimosa.ui.app import main

    rc = main(["--check", "--no-log-file"])
    assert rc == 0

    out = capsys.readouterr().out
    assert "MimOSA environment" in out
    # describe_log_location() always mentions where logs live.
    assert "log" in out.lower()
