"""Tests for the training dependency checker (Milestone 2).

The checker accepts an injectable ``probe`` so we never touch the real
environment or trigger heavy imports during the test-suite.
"""

from __future__ import annotations

from mimosa.training.dependencies import (
    DependencyReport,
    check_dependencies,
    dependencies_satisfied,
)


def _probe_all_present(_module: str) -> bool:
    return True


def _probe_none_present(_module: str) -> bool:
    return False


def test_all_present_is_satisfied():
    report = check_dependencies(probe=_probe_all_present)
    assert isinstance(report, DependencyReport)
    assert report.satisfied is True
    assert report.missing_required == []
    assert report.missing_optional == []
    assert report.download_mb == 0
    assert report.pip_install_args() == []


def test_none_present_is_not_satisfied():
    report = check_dependencies(probe=_probe_none_present)
    assert report.satisfied is False
    assert len(report.missing_required) >= 1
    assert len(report.missing_optional) >= 1
    assert report.download_mb > 0
    assert "torch" in report.pip_install_args()


def test_optional_only_missing_is_still_satisfied():
    optional = {"scipy", "soundfile"}
    report = check_dependencies(probe=lambda m: m not in optional)
    # Optional deps don't block training.
    assert report.satisfied is True
    assert report.missing_required == []
    assert {d.module for d in report.missing_optional} == optional


def test_single_required_missing_blocks():
    report = check_dependencies(probe=lambda m: m != "torch")
    assert report.satisfied is False
    assert "torch" in {d.module for d in report.missing_required}
    assert "torch" in report.pip_install_args()


def test_prompt_message_is_friendly_and_nonempty():
    report = check_dependencies(probe=_probe_none_present)
    message = report.prompt_message()
    assert isinstance(message, str)
    assert message.strip()


def test_download_size_text_has_units():
    report = check_dependencies(probe=_probe_none_present)
    text = report.download_size_text
    assert "MB" in text or "GB" in text


def test_dependencies_satisfied_helper():
    assert dependencies_satisfied(probe=_probe_all_present) is True
    assert dependencies_satisfied(probe=_probe_none_present) is False


def test_check_dependencies_default_probe_does_not_raise():
    # With no probe it inspects the real environment; must never raise.
    report = check_dependencies()
    assert isinstance(report, DependencyReport)
    assert isinstance(report.satisfied, bool)
