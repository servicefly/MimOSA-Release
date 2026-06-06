"""Hermetic tests for the M8.3 runtime service wiring (AppServices)."""

from __future__ import annotations

import pytest

from mimosa.core.runtime import AppServices, MaintenanceReport
from mimosa.memory.conversation_store import ConversationStore
from mimosa.memory.preference_learner import PreferenceLearner
from mimosa.utils.config import AppConfig, PrivacySettings, TasksSettings


def _make_config(**task_kwargs) -> AppConfig:
    cfg = AppConfig()
    for k, v in task_kwargs.items():
        setattr(cfg.tasks, k, v)
    cfg.tasks.validate()
    return cfg


# A deterministic sampler reporting an idle system (so the gate stays open).
def _idle_sampler():
    return (1.0, 1.0, 4096.0, 0.1)


def test_build_all_services_enabled():
    cfg = _make_config(
        background_tasks_enabled=True,
        resource_monitoring=True,
        learn_error_fixes=True,
    )
    services = AppServices.from_config(
        cfg, sampler=_idle_sampler, tasks_db_path=":memory:"
    )
    assert services.task_queue is not None
    assert services.resource_monitor is not None
    assert services.error_learner is not None
    assert services.error_reporter is not None
    services.shutdown()


def test_tasks_disabled_no_queue():
    cfg = _make_config(background_tasks_enabled=False)
    services = AppServices.from_config(cfg, tasks_db_path=":memory:")
    assert services.task_queue is None
    # Error reporter is always built (cross-cutting), regardless of tasks.
    assert services.error_reporter is not None


def test_resource_monitoring_disabled_no_monitor():
    cfg = _make_config(resource_monitoring=False)
    services = AppServices.from_config(
        cfg, sampler=_idle_sampler, tasks_db_path=":memory:"
    )
    assert services.resource_monitor is None
    # Queue still builds, just without a gate.
    assert services.task_queue is not None
    services.shutdown()


def test_learn_error_fixes_disabled_learner_not_recording():
    cfg = _make_config(learn_error_fixes=False)
    learner = PreferenceLearner(db_path=":memory:")
    services = AppServices.from_config(
        cfg, preference_learner=learner, tasks_db_path=":memory:"
    )
    # Disabled learner should refuse to record.
    assert services.error_learner is not None
    assert services.error_learner.enabled is False
    assert services.error_learner.record_fix("some error", "some fix") is False


def test_resource_gate_wired_into_queue():
    cfg = _make_config(background_tasks_enabled=True, resource_monitoring=True)
    services = AppServices.from_config(
        cfg, sampler=_idle_sampler, tasks_db_path=":memory:"
    )
    # The queue received a callable gate from the monitor.
    assert services.task_queue._resource_gate is not None
    assert callable(services.task_queue._resource_gate)
    services.shutdown()


def test_start_and_shutdown_idempotent():
    cfg = _make_config(background_tasks_enabled=True)
    services = AppServices.from_config(cfg, tasks_db_path=":memory:")
    services.start()
    services.start()  # idempotent
    services.shutdown()
    services.shutdown()  # idempotent, no raise


def test_context_manager():
    cfg = _make_config(background_tasks_enabled=True)
    with AppServices.from_config(cfg, tasks_db_path=":memory:") as services:
        assert services._started is True
    assert services._started is False


def test_run_maintenance_no_store_is_noop():
    cfg = _make_config()
    services = AppServices.from_config(cfg, tasks_db_path=":memory:")
    report = services.run_maintenance()
    assert isinstance(report, MaintenanceReport)
    assert report.ran is False
    assert "skipped" in report.summary().lower()


def test_run_maintenance_purges_and_vacuums():
    cfg = _make_config()
    cfg.privacy.data_retention_days = 7
    cfg.privacy.validate()
    store = ConversationStore(db_path=":memory:")
    services = AppServices.from_config(
        cfg, conversation_store=store, tasks_db_path=":memory:"
    )
    report = services.run_maintenance()
    assert report.ran is True
    # In-memory store: vacuum is a no-op (returns False) but purge ran.
    assert report.messages_purged == 0
    assert report.conversation_vacuumed is False
    assert "Maintenance" in report.summary()


def test_run_maintenance_retention_zero_keeps_everything():
    cfg = _make_config()
    cfg.privacy.data_retention_days = 0
    cfg.privacy.validate()

    purged_calls = []

    class _Store:
        def purge_older_than(self, days):
            purged_calls.append(days)
            return 5

        def vacuum(self):
            return True

        def close(self):
            pass

    services = AppServices.from_config(
        cfg, conversation_store=_Store(), tasks_db_path=":memory:"
    )
    report = services.run_maintenance()
    # retention 0 means purge_older_than is never called.
    assert purged_calls == []
    assert report.messages_purged == 0
    assert report.conversation_vacuumed is True


def test_maintenance_defensive_when_store_raises():
    cfg = _make_config()
    cfg.privacy.data_retention_days = 30
    cfg.privacy.validate()

    class _BrokenStore:
        def purge_older_than(self, days):
            raise RuntimeError("db locked")

        def vacuum(self):
            raise RuntimeError("db locked")

        def close(self):
            pass

    services = AppServices.from_config(
        cfg, conversation_store=_BrokenStore(), tasks_db_path=":memory:"
    )
    # Should not raise despite the store blowing up.
    report = services.run_maintenance()
    assert report.ran is True
    assert report.messages_purged == 0
    assert report.conversation_vacuumed is False
