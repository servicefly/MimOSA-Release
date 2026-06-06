"""Hermetic tests for MimOSA's centralised logging configuration (M8.2)."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import pytest

from mimosa.utils import logging_setup
from mimosa.utils.logging_setup import (
    BACKUP_COUNT,
    MAX_BYTES,
    configure_logging,
    describe_log_location,
)


@pytest.fixture(autouse=True)
def _restore_root_logging():
    """Snapshot and restore the root logger around each test."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        yield
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        for h in saved_handlers:
            root.addHandler(h)
        root.setLevel(saved_level)


def _managed_handlers(root):
    return [
        h
        for h in root.handlers
        if getattr(h, logging_setup._MIMOSA_HANDLER_FLAG, False)
    ]


def test_configure_logging_creates_rotating_file_handler(tmp_path):
    log_path = tmp_path / "logs" / "mimosa.log"
    active = configure_logging(to_file=True, log_path=log_path)

    assert active == log_path
    root = logging.getLogger()
    file_handlers = [
        h
        for h in _managed_handlers(root)
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    fh = file_handlers[0]
    assert fh.maxBytes == MAX_BYTES
    assert fh.backupCount == BACKUP_COUNT


def test_configure_logging_console_only(tmp_path):
    active = configure_logging(to_file=False)
    assert active is None
    root = logging.getLogger()
    managed = _managed_handlers(root)
    # Console handler present, no rotating file handler.
    assert any(isinstance(h, logging.StreamHandler) for h in managed)
    assert not any(
        isinstance(h, logging.handlers.RotatingFileHandler) for h in managed
    )


def test_verbose_sets_debug_level(tmp_path):
    configure_logging(verbose=True, to_file=False)
    assert logging.getLogger().level == logging.DEBUG
    configure_logging(verbose=False, to_file=False)
    assert logging.getLogger().level == logging.INFO


def test_idempotent_no_duplicate_handlers(tmp_path):
    log_path = tmp_path / "logs" / "mimosa.log"
    configure_logging(to_file=True, log_path=log_path)
    configure_logging(to_file=True, log_path=log_path)
    configure_logging(to_file=True, log_path=log_path)

    root = logging.getLogger()
    managed = _managed_handlers(root)
    file_handlers = [
        h for h in managed if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    console_handlers = [
        h
        for h in managed
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(file_handlers) == 1
    assert len(console_handlers) == 1


def test_log_messages_written_to_file(tmp_path):
    log_path = tmp_path / "logs" / "mimosa.log"
    configure_logging(to_file=True, log_path=log_path)
    logging.getLogger("mimosa.test").warning("hello-from-test")

    # delay=True means file opens on first emit; it should now exist.
    assert log_path.exists()
    contents = log_path.read_text(encoding="utf-8")
    assert "hello-from-test" in contents
    assert "WARNING" in contents


def test_graceful_degradation_when_path_unwritable(tmp_path, monkeypatch):
    log_path = tmp_path / "logs" / "mimosa.log"

    def boom(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(
        logging_setup.logging.handlers,
        "RotatingFileHandler",
        boom,
    )
    # Should not raise, and should fall back to console-only (active is None).
    active = configure_logging(to_file=True, log_path=log_path)
    assert active is None
    # Only the console handler should be installed (no file written).
    root = logging.getLogger()
    assert len(_managed_handlers(root)) == 1
    assert not log_path.exists()


def test_rotation_triggers_backup(tmp_path):
    log_path = tmp_path / "logs" / "mimosa.log"
    configure_logging(to_file=True, log_path=log_path)
    logger = logging.getLogger("mimosa.rotate")
    # Emit enough data to exceed MAX_BYTES and force at least one rollover.
    chunk = "x" * 512
    for _ in range(MAX_BYTES // 256):
        logger.warning(chunk)
    # A rotated backup should now exist alongside the active log.
    assert (tmp_path / "logs" / "mimosa.log.1").exists()


def test_describe_log_location_mentions_path(tmp_path):
    log_path = tmp_path / "logs" / "mimosa.log"
    text = describe_log_location(log_path)
    assert str(log_path) in text
    assert "backups" in text.lower()


def test_default_log_path_uses_paths_module(tmp_path, monkeypatch):
    monkeypatch.setenv("MIMOSA_DATA", str(tmp_path / "data"))
    active = configure_logging(to_file=True)
    assert active is not None
    assert active == tmp_path / "data" / "logs" / "mimosa.log"
