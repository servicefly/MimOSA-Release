"""Hermetic tests for the task-control skill (M7.4).

Drives a real in-memory :class:`TaskQueue` (no threads) so assertions are
deterministic. The skill itself is purely local (no LLM, no network).
"""

from __future__ import annotations

import pytest

from mimosa.skills.base_skill import SkillResult
from mimosa.skills.task_skill import TaskControlSkill
from mimosa.tasks.task_queue import TaskQueue, TaskStatus


@pytest.fixture()
def queue():
    q = TaskQueue(db_path=":memory:")
    # A handler that blocks at the first checkpoint unless told to finish, so
    # tasks remain "active" for control tests when we leave them queued.
    q.register_handler("demo", lambda task, control: "done")
    yield q
    q.close()


@pytest.fixture()
def skill(queue):
    return TaskControlSkill(queue=queue)


# --------------------------------------------------------------------------
# basic contract
# --------------------------------------------------------------------------

def test_skill_metadata(skill):
    assert skill.name == "tasks"
    assert skill.intents == ["task_control"]
    assert skill.uses_llm is False


def test_lazy_queue_built_when_none():
    s = TaskControlSkill()
    assert s.queue is not None  # in-memory default


def test_returns_skill_result(skill):
    res = skill.handle("what are you working on?")
    assert isinstance(res, SkillResult)
    assert res.skill == "tasks"


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def test_status_no_tasks(skill):
    res = skill.handle("what are you working on?")
    assert res.metadata["active"] == 0
    assert "no background tasks" in res.text.lower()


def test_status_lists_active(skill, queue):
    queue.enqueue("Index Docs", "demo")
    queue.enqueue("Convert Files", "demo")
    res = skill.handle("task status")
    assert res.metadata["active"] == 2
    assert "Index Docs" in res.text
    assert "Convert Files" in res.text


# --------------------------------------------------------------------------
# pause
# --------------------------------------------------------------------------

def test_pause_everything(skill, queue):
    queue.enqueue("A", "demo")
    queue.enqueue("B", "demo")
    res = skill.handle("pause everything")
    assert res.metadata["action"] == "pause"
    assert res.metadata["affected"] == 2
    assert all(t.status == TaskStatus.PAUSED for t in queue.list_tasks())


def test_pause_single_when_only_one(skill, queue):
    queue.enqueue("Only Job", "demo")
    res = skill.handle("hold on")
    assert res.metadata["affected"] == 1
    assert "Only Job" in res.text


def test_pause_nothing_running(skill):
    res = skill.handle("pause everything")
    assert res.metadata["affected"] == 0
    assert "nothing running" in res.text.lower()


def test_pause_by_name(skill, queue):
    queue.enqueue("Index Documents", "demo")
    queue.enqueue("Convert Photos", "demo")
    res = skill.handle("pause the index documents task")
    assert res.success is True
    assert res.metadata["action"] == "pause"


def test_pause_ambiguous_asks(skill, queue):
    queue.enqueue("Alpha Job", "demo")
    queue.enqueue("Beta Job", "demo")
    res = skill.handle("pause it")  # no name, multiple tasks
    assert res.success is False
    assert res.metadata["reason"] == "ambiguous"


# --------------------------------------------------------------------------
# resume
# --------------------------------------------------------------------------

def test_resume_everything(skill, queue):
    queue.enqueue("A", "demo")
    queue.enqueue("B", "demo")
    queue.pause_all()
    res = skill.handle("carry on")
    assert res.metadata["action"] == "resume"
    assert res.metadata["affected"] == 2
    assert all(t.status == TaskStatus.QUEUED for t in queue.list_tasks())


def test_resume_nothing_paused(skill, queue):
    queue.enqueue("A", "demo")
    res = skill.handle("resume everything")
    assert res.metadata["affected"] == 0
    assert "nothing paused" in res.text.lower()


def test_resume_single(skill, queue):
    t = queue.enqueue("Solo", "demo")
    queue.pause(t.id)
    res = skill.handle("continue")
    assert res.metadata["affected"] == 1


# --------------------------------------------------------------------------
# cancel
# --------------------------------------------------------------------------

def test_cancel_everything(skill, queue):
    queue.enqueue("A", "demo")
    queue.enqueue("B", "demo")
    res = skill.handle("cancel everything")
    assert res.metadata["action"] == "cancel"
    assert res.metadata["affected"] == 2
    assert all(t.status == TaskStatus.CANCELLED for t in queue.list_tasks())


def test_cancel_single(skill, queue):
    queue.enqueue("Only", "demo")
    res = skill.handle("abort the task")
    assert res.success is True
    assert res.metadata["affected"] == 1


def test_cancel_nothing(skill):
    res = skill.handle("cancel the task")
    assert res.metadata["affected"] == 0
    assert "nothing running" in res.text.lower()


def test_cancel_by_name(skill, queue):
    queue.enqueue("Download Movie", "demo")
    queue.enqueue("Index Mail", "demo")
    res = skill.handle("cancel the download movie job")
    assert res.success is True


# --------------------------------------------------------------------------
# robustness
# --------------------------------------------------------------------------

def test_run_never_raises(skill):
    # run() wraps handle(); even odd input returns a SkillResult.
    res = skill.run("")
    assert isinstance(res, SkillResult)


def test_empty_input_defaults_to_status(skill):
    res = skill.handle("")
    assert res.metadata.get("active") == 0
