"""Headless smoke tests for the Milestone-2 training dialogs.

These run without a display. When GTK is unavailable (the CI/headless case)
the ``open_*`` helpers must degrade gracefully: invoke the ``on_close``
callback with a safe fallback value and return ``None`` instead of raising.
"""

from __future__ import annotations

from mimosa.ui import test_wakeword_dialog, training_dialog


def test_training_dialog_module_exposes_api():
    assert hasattr(training_dialog, "open_training_dialog")
    assert hasattr(training_dialog, "TrainingDialog")


def test_test_wakeword_dialog_module_exposes_api():
    assert hasattr(test_wakeword_dialog, "open_test_wakeword_dialog")
    assert hasattr(test_wakeword_dialog, "TestWakeWordDialog")


def test_open_training_dialog_headless_calls_on_close():
    if training_dialog.HAS_GTK:
        import pytest
        pytest.skip("GTK present; headless fallback not exercised")

    captured = {}

    def _on_close(result):
        captured["result"] = result

    ret = training_dialog.open_training_dialog(
        "Jarvis", gender="female", on_close=_on_close
    )
    assert ret is None
    assert "result" in captured  # callback fired even without a display


def test_open_test_wakeword_dialog_headless_calls_on_close():
    if test_wakeword_dialog.HAS_GTK:
        import pytest
        pytest.skip("GTK present; headless fallback not exercised")

    captured = {}

    def _on_close(heard):
        captured["heard"] = heard

    ret = test_wakeword_dialog.open_test_wakeword_dialog(
        "Jarvis", on_close=_on_close
    )
    assert ret is None
    assert captured.get("heard") is False  # no mic heard in headless mode
