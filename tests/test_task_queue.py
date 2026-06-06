"""Hermetic tests for the background task queue (M7.1).

All tests use an in-memory SQLite database and drive the queue *synchronously*
(``process_next``/``drain``) so they never depend on thread timing. A small set
of tests exercise the real background workers with deterministic polling.
"""

from __future__ import annotations

import threading
import time

import pytest

from mimosa.tasks.task_queue import (
    DEFAULT_MAX_CONCURRENT,
    Task,
    TaskCancelled,
    TaskControl,
    TaskPaused,
    TaskQueue,
    TaskStatus,
)


@pytest.fixture()
def queue():
    q = TaskQueue(db_path=":memory:")
    yield q
    q.close()


def _ok_handler(result="done"):
    def handler(task, control):
        control.set_progress(1.0, "finished")
        return result
    return handler


# --------------------------------------------------------------------------
# enqueue / inspect
# --------------------------------------------------------------------------

def test_enqueue_returns_queued_task(queue):
    t = queue.enqueue("Index", "demo", {"path": "/x"})
    assert isinstance(t, Task)
    assert t.status == TaskStatus.QUEUED
    assert t.kind == "demo"
    assert t.payload == {"path": "/x"}
    assert t.id


def test_enqueue_blank_name_defaults(queue):
    t = queue.enqueue("   ", "demo")
    assert t.name == "Untitled task"


def test_enqueue_requires_kind(queue):
    with pytest.raises(ValueError):
        queue.enqueue("name", "   ")


def test_get_unknown_returns_none(queue):
    assert queue.get("nope") is None


def test_get_roundtrips_task(queue):
    t = queue.enqueue("A", "demo", {"k": 1})
    got = queue.get(t.id)
    assert got is not None
    assert got.id == t.id
    assert got.payload == {"k": 1}


def test_list_tasks_newest_first(queue):
    a = queue.enqueue("A", "demo")
    b = queue.enqueue("B", "demo")
    ids = [t.id for t in queue.list_tasks()]
    assert ids[0] == b.id and ids[1] == a.id


def test_list_tasks_filter_by_status(queue):
    queue.enqueue("A", "demo")
    assert len(queue.list_tasks(status=TaskStatus.QUEUED)) == 1
    assert len(queue.list_tasks(status=TaskStatus.COMPLETED)) == 0


def test_counts_and_running_count(queue):
    queue.enqueue("A", "demo")
    queue.enqueue("B", "demo")
    assert queue.counts().get("queued") == 2
    assert queue.running_count() == 0


# --------------------------------------------------------------------------
# handler registration & execution
# --------------------------------------------------------------------------

def test_has_handler(queue):
    assert not queue.has_handler("demo")
    queue.register_handler("demo", _ok_handler())
    assert queue.has_handler("demo")


def test_register_handler_validates(queue):
    with pytest.raises(ValueError):
        queue.register_handler("", _ok_handler())
    with pytest.raises(TypeError):
        queue.register_handler("demo", "not callable")


def test_process_next_runs_and_completes(queue):
    queue.register_handler("demo", _ok_handler({"value": 42}))
    t = queue.enqueue("Job", "demo")
    finished = queue.process_next()
    assert finished.status == TaskStatus.COMPLETED
    assert finished.result == {"value": 42}
    assert finished.progress == 1.0
    assert finished.started_at is not None
    assert finished.finished_at is not None


def test_process_next_returns_none_when_empty(queue):
    assert queue.process_next() is None


def test_task_without_handler_stays_queued(queue):
    queue.enqueue("Job", "no_handler_kind")
    # No handler registered -> not runnable -> nothing processed.
    assert queue.process_next() is None
    assert queue.list_tasks()[0].status == TaskStatus.QUEUED


def test_handler_exception_marks_failed(queue):
    def boom(task, control):
        raise RuntimeError("kaboom")
    queue.register_handler("demo", boom)
    queue.enqueue("Job", "demo")
    finished = queue.process_next()
    assert finished.status == TaskStatus.FAILED
    assert "kaboom" in finished.error


def test_drain_runs_all(queue):
    queue.register_handler("demo", _ok_handler())
    for i in range(5):
        queue.enqueue(f"J{i}", "demo")
    processed = queue.drain()
    assert processed == 5
    assert all(t.status == TaskStatus.COMPLETED for t in queue.list_tasks())


def test_drain_respects_max_tasks(queue):
    queue.register_handler("demo", _ok_handler())
    for i in range(5):
        queue.enqueue(f"J{i}", "demo")
    assert queue.drain(max_tasks=2) == 2
    assert len(queue.list_tasks(status=TaskStatus.QUEUED)) == 3


# --------------------------------------------------------------------------
# priority ordering
# --------------------------------------------------------------------------

def test_priority_runs_first(queue):
    order = []
    def handler(task, control):
        order.append(task.name)
    queue.register_handler("demo", handler)
    queue.enqueue("low", "demo", priority=0)
    queue.enqueue("high", "demo", priority=10)
    queue.drain()
    assert order[0] == "high"


# --------------------------------------------------------------------------
# pause / resume / cancel
# --------------------------------------------------------------------------

def test_pause_queued_task(queue):
    t = queue.enqueue("Job", "demo")
    assert queue.pause(t.id) is True
    assert queue.get(t.id).status == TaskStatus.PAUSED


def test_resume_requeues_paused(queue):
    t = queue.enqueue("Job", "demo")
    queue.pause(t.id)
    assert queue.resume(t.id) is True
    assert queue.get(t.id).status == TaskStatus.QUEUED


def test_resume_non_paused_returns_false(queue):
    t = queue.enqueue("Job", "demo")
    assert queue.resume(t.id) is False


def test_running_task_pauses_at_checkpoint(queue):
    def handler(task, control):
        control.checkpoint()  # pause signal raised here
        return "should not reach"
    queue.register_handler("demo", handler)
    t = queue.enqueue("Job", "demo")
    queue._control[t.id] = "pause"  # simulate a pause request before it runs
    finished = queue.process_next()
    assert finished.status == TaskStatus.PAUSED
    # progress preserved; can resume and complete later
    queue._control.clear()
    queue.resume(t.id)
    queue.register_handler("demo", _ok_handler("ok"))
    finished2 = queue.process_next()
    assert finished2.status == TaskStatus.COMPLETED


def test_cancel_queued_task(queue):
    t = queue.enqueue("Job", "demo")
    assert queue.cancel(t.id) is True
    assert queue.get(t.id).status == TaskStatus.CANCELLED


def test_running_task_cancels_at_checkpoint(queue):
    def handler(task, control):
        control.checkpoint()
        return "nope"
    queue.register_handler("demo", handler)
    t = queue.enqueue("Job", "demo")
    queue._control[t.id] = "cancel"
    finished = queue.process_next()
    assert finished.status == TaskStatus.CANCELLED


def test_cancel_terminal_returns_false(queue):
    queue.register_handler("demo", _ok_handler())
    t = queue.enqueue("Job", "demo")
    queue.process_next()
    assert queue.cancel(t.id) is False


def test_pause_all_and_resume_all(queue):
    for i in range(3):
        queue.enqueue(f"J{i}", "demo")
    assert queue.pause_all() == 3
    assert queue.is_paused_all is True
    assert all(t.status == TaskStatus.PAUSED for t in queue.list_tasks())
    assert queue.resume_all() == 3
    assert queue.is_paused_all is False
    assert all(t.status == TaskStatus.QUEUED for t in queue.list_tasks())


def test_global_pause_blocks_new_starts(queue):
    queue.register_handler("demo", _ok_handler())
    queue.enqueue("Job", "demo")
    queue.pause_all()
    # claim must refuse while globally paused
    assert queue.process_next() is None


# --------------------------------------------------------------------------
# concurrency gate & resource gate
# --------------------------------------------------------------------------

def test_resource_gate_blocks_claim():
    q = TaskQueue(db_path=":memory:", resource_gate=lambda: False)
    q.register_handler("demo", _ok_handler())
    q.enqueue("Job", "demo")
    assert q.process_next() is None  # gate closed
    q.close()


def test_resource_gate_allows_claim():
    q = TaskQueue(db_path=":memory:", resource_gate=lambda: True)
    q.register_handler("demo", _ok_handler())
    q.enqueue("Job", "demo")
    assert q.process_next().status == TaskStatus.COMPLETED
    q.close()


def test_resource_gate_exception_fails_open():
    def bad_gate():
        raise RuntimeError("sensor down")
    q = TaskQueue(db_path=":memory:", resource_gate=bad_gate)
    q.register_handler("demo", _ok_handler())
    q.enqueue("Job", "demo")
    # gate crashing must not block the queue (fail-open)
    assert q.process_next().status == TaskStatus.COMPLETED
    q.close()


def test_default_max_concurrent_constant():
    assert DEFAULT_MAX_CONCURRENT == 2


# --------------------------------------------------------------------------
# progress reporting
# --------------------------------------------------------------------------

def test_progress_is_clamped(queue):
    def handler(task, control):
        control.set_progress(2.0, "over")  # clamps to 1.0
        control.set_progress(-1.0, "under")  # clamps to 0.0
    queue.register_handler("demo", handler)
    t = queue.enqueue("Job", "demo")
    queue.process_next()
    assert 0.0 <= queue.get(t.id).progress <= 1.0


def test_progress_message_recorded(queue):
    def handler(task, control):
        control.set_progress(0.5, "halfway")
    queue.register_handler("demo", handler)
    t = queue.enqueue("Job", "demo")
    queue.process_next()
    assert queue.get(t.id).message == "halfway"


# --------------------------------------------------------------------------
# persistence & recovery
# --------------------------------------------------------------------------

def test_persistence_across_reopen(tmp_path):
    db = tmp_path / "tasks.db"
    q1 = TaskQueue(db_path=db)
    t = q1.enqueue("Persistent", "demo", {"a": 1})
    q1.close()
    q2 = TaskQueue(db_path=db)
    got = q2.get(t.id)
    assert got is not None
    assert got.name == "Persistent"
    assert got.payload == {"a": 1}
    q2.close()


def test_recover_orphans_requeues_running(tmp_path):
    db = tmp_path / "tasks.db"
    q1 = TaskQueue(db_path=db)
    t = q1.enqueue("Job", "demo")
    # Force a RUNNING row as if crashed mid-flight.
    q1._set_status(t.id, TaskStatus.RUNNING)
    q1.close()
    q2 = TaskQueue(db_path=db)  # _recover_orphans runs in ctor
    assert q2.get(t.id).status == TaskStatus.QUEUED
    q2.close()


# --------------------------------------------------------------------------
# maintenance
# --------------------------------------------------------------------------

def test_purge_terminal(queue):
    queue.register_handler("demo", _ok_handler())
    queue.enqueue("Done", "demo")
    queue.process_next()
    queue.enqueue("Pending", "demo")
    removed = queue.purge()
    assert removed == 1
    assert len(queue.list_tasks()) == 1


def test_clear_all(queue):
    queue.enqueue("A", "demo")
    queue.enqueue("B", "demo")
    queue.clear_all()
    assert queue.list_tasks() == []


def test_active_tasks(queue):
    queue.register_handler("demo", _ok_handler())
    queue.enqueue("Q", "demo")
    done = queue.enqueue("D", "demo")
    queue.process_next()  # completes the highest/oldest? both priority 0 -> oldest first = Q
    active = queue.active_tasks()
    assert all(t.status.is_active for t in active)


# --------------------------------------------------------------------------
# TaskStatus helpers
# --------------------------------------------------------------------------

def test_status_is_terminal():
    assert TaskStatus.COMPLETED.is_terminal
    assert TaskStatus.FAILED.is_terminal
    assert TaskStatus.CANCELLED.is_terminal
    assert not TaskStatus.QUEUED.is_terminal


def test_status_is_active():
    assert TaskStatus.QUEUED.is_active
    assert TaskStatus.RUNNING.is_active
    assert TaskStatus.PAUSED.is_active
    assert not TaskStatus.COMPLETED.is_active


def test_task_to_dict_roundtrip_fields(queue):
    t = queue.enqueue("Job", "demo", {"x": 1})
    d = t.to_dict()
    assert d["name"] == "Job"
    assert d["status"] == "queued"
    assert d["payload"] == {"x": 1}


# --------------------------------------------------------------------------
# background workers (real threads, deterministic via events)
# --------------------------------------------------------------------------

def test_background_workers_run_tasks(tmp_path):
    q = TaskQueue(db_path=tmp_path / "t.db", max_concurrent=2)
    done = threading.Event()
    def handler(task, control):
        done.set()
        return "ok"
    q.register_handler("demo", handler)
    q.enqueue("Job", "demo")
    q.start()
    assert done.wait(timeout=3.0)
    # give the worker a moment to persist completion
    for _ in range(50):
        if q.counts().get("completed"):
            break
        time.sleep(0.02)
    q.stop()
    assert q.counts().get("completed") == 1
    q.close()


def test_start_is_idempotent(queue):
    queue.start()
    n = len(queue._workers)
    queue.start()
    assert len(queue._workers) == n
    queue.stop()
