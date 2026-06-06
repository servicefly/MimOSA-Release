"""Hermetic tests for the graceful error reporter (M8.1)."""

from __future__ import annotations

import pytest

from mimosa.core.error_reporter import (
    ErrorReport,
    ErrorReporter,
    GENERIC_MESSAGE,
    friendly_message,
)
from mimosa.tasks.error_learner import ErrorFixLearner
from mimosa.memory.preference_learner import PreferenceLearner


# --------------------------------------------------------------------------
# friendly_message
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "exc,needle",
    [
        (FileNotFoundError("x"), "couldn't find"),
        (PermissionError("nope"), "permission"),
        (TimeoutError("slow"), "too long"),
        (ConnectionError("net"), "network"),
        (IsADirectoryError("d"), "folder"),
        (MemoryError(), "memory"),
        (NotImplementedError(), "yet"),
        (ValueError("bad"), "didn't look quite right"),
    ],
)
def test_friendly_message_by_type(exc, needle):
    assert needle in friendly_message(exc).lower()


def test_friendly_message_by_keyword():
    # A generic OSError whose message mentions a full disk.
    msg = friendly_message(OSError("write failed: No space left on device"))
    assert "disk is full" in msg.lower()


def test_friendly_message_never_raises_and_has_fallback():
    class Weird(Exception):
        def __str__(self):  # noqa: D401 - deliberately hostile
            raise RuntimeError("boom")

    # Should swallow the hostile __str__ and fall back gracefully.
    assert friendly_message(Weird()) == GENERIC_MESSAGE


def test_friendly_message_unknown_type():
    class CustomError(Exception):
        pass

    assert friendly_message(CustomError("???")) == GENERIC_MESSAGE


# --------------------------------------------------------------------------
# ErrorReporter (no learner)
# --------------------------------------------------------------------------

def test_report_without_learner():
    r = ErrorReporter(log=False)
    rep = r.report(FileNotFoundError("/tmp/x"))
    assert isinstance(rep, ErrorReport)
    assert "couldn't find" in rep.message.lower()
    assert rep.category == "file"
    assert rep.exc_type == "FileNotFoundError"
    assert rep.suggestion is None
    assert rep.signature  # normalised, non-empty
    assert rep.spoken() == rep.message  # no suggestion appended


def test_record_fix_without_learner_is_false():
    r = ErrorReporter(log=False)
    assert r.report(ValueError("x")) is not None
    assert r.record_fix("ValueError: x", "do the thing") is False


# --------------------------------------------------------------------------
# ErrorReporter (with learner)
# --------------------------------------------------------------------------

@pytest.fixture()
def learner():
    return ErrorFixLearner(PreferenceLearner(db_path=":memory:"), confidence_threshold=0.0)


def test_report_with_learner_suggests_known_fix(learner):
    raw = "FileNotFoundError: /home/me/notes.txt"
    # Teach it a fix several times so confidence is solid.
    for _ in range(4):
        learner.record_fix(raw, "create the missing file")
    r = ErrorReporter(learner=learner, log=False)
    rep = r.report(FileNotFoundError("/home/me/notes.txt"))
    assert rep.suggestion == "create the missing file"
    assert "fixed by: create the missing file" in rep.spoken().lower()


def test_report_generalises_across_paths(learner):
    for _ in range(4):
        learner.record_fix("FileNotFoundError: /home/a/x.txt", "create it")
    r = ErrorReporter(learner=learner, log=False)
    # A *different* path should still match the learned signature.
    rep = r.report(FileNotFoundError("/var/tmp/other.log"))
    assert rep.suggestion == "create it"


def test_record_fix_with_learner(learner):
    r = ErrorReporter(learner=learner, log=False)
    assert r.record_fix("ValueError: bad input", "sanitise the input") is True


def test_reporter_swallows_learner_errors():
    class BadLearner:
        available = True

        def suggest_fix(self, error):
            raise RuntimeError("learner exploded")

    r = ErrorReporter(learner=BadLearner(), log=False)
    rep = r.report(ValueError("x"))  # must not raise
    assert rep.suggestion is None



# --------------------------------------------------------------------------
# Router integration: _enrich_errors (M8.1)
# --------------------------------------------------------------------------

from mimosa.core.intent_router import IntentRouter
from mimosa.skills.base_skill import SkillResult


def test_router_enriches_failed_result_with_known_fix(learner):
    for _ in range(4):
        learner.record_fix("FileNotFoundError: /home/me/data.txt", "create the file")
    reporter = ErrorReporter(learner=learner, log=False)
    router = IntentRouter(error_reporter=reporter)
    failed = SkillResult(
        text="Sorry, I ran into a problem.",
        success=False,
        skill="x",
        metadata={"error": "FileNotFoundError: /home/me/data.txt"},
    )
    out = router._enrich_errors(failed)
    assert out.metadata["fix_suggestion"] == "create the file"
    assert "create the file" in out.text
    assert out.metadata["error_signature"]


def test_router_passthrough_on_success(learner):
    reporter = ErrorReporter(learner=learner, log=False)
    router = IntentRouter(error_reporter=reporter)
    ok = SkillResult(text="done", success=True, skill="x")
    assert router._enrich_errors(ok).text == "done"


def test_router_no_reporter_is_noop():
    router = IntentRouter()  # no error_reporter
    failed = SkillResult(
        text="Sorry.", success=False, skill="x",
        metadata={"error": "ValueError: bad"},
    )
    out = router._enrich_errors(failed)
    assert out.text == "Sorry."
    assert "fix_suggestion" not in out.metadata


def test_router_failed_without_error_metadata_unchanged(learner):
    reporter = ErrorReporter(learner=learner, log=False)
    router = IntentRouter(error_reporter=reporter)
    # A graceful failure that wasn't an exception (no 'error' key).
    failed = SkillResult(text="Which one?", success=False, skill="x",
                         metadata={"reason": "ambiguous"})
    out = router._enrich_errors(failed)
    assert out.text == "Which one?"
    assert "fix_suggestion" not in out.metadata
