"""Tests for the V2 Task Store (app/task_store_v2.py)."""

import pytest

from app.models import TaskStatusV2, TaskV2
from app.task_store_v2 import TaskStoreV2


def make_task(task_id="TASK-1", investigation_id="CASE:U00299", **kw) -> TaskV2:
    base = dict(
        task_id=task_id,
        investigation_id=investigation_id,
        status=TaskStatusV2.PENDING,
        user_request="Why was F3 flagged?",
        selected_skill="timeline_investigation",
    )
    base.update(kw)
    return TaskV2(**base)


@pytest.fixture()
def store(tmp_path):
    s = TaskStoreV2(tmp_path / "tasks_v2_test.db")
    yield s
    if s._conn is not None:
        s._conn.close()


class TestCrud:
    def test_create_and_get_roundtrip(self, store):
        task = make_task(
            tool_call_ids=["TC-1"], artifact_ids=["ART-1"],
            started_at="2026-08-27T00:00:00Z",
        )
        store.create(task)
        loaded = store.get("TASK-1")
        assert loaded == task

    def test_get_missing_returns_none(self, store):
        assert store.get("nope") is None

    def test_update_persists_mutation(self, store):
        task = make_task()
        store.create(task)
        task.status = TaskStatusV2.EXECUTING
        task.tool_call_ids = ["TC-9"]
        store.update(task)
        loaded = store.get("TASK-1")
        assert loaded.status == TaskStatusV2.EXECUTING
        assert loaded.tool_call_ids == ["TC-9"]

    def test_list_by_investigation(self, store):
        store.create(make_task("T-A", "CASE:U00299"))
        store.create(make_task("T-B", "CASE:U00299"))
        store.create(make_task("T-C", "CASE:U00010"))
        got = [t.task_id for t in store.list_by_investigation("CASE:U00299")]
        assert sorted(got) == ["T-A", "T-B"]
        assert [t.task_id for t in store.list_by_investigation("CASE:U00010")] == ["T-C"]

    def test_list_unknown_investigation_empty(self, store):
        assert store.list_by_investigation("CASE:NOPE") == []

    def test_reload_from_new_store_instance(self, store, tmp_path):
        db = tmp_path / "tasks_v2_reload.db"
        s1 = TaskStoreV2(db)
        original = make_task(
            "T-R", status=TaskStatusV2.COMPLETED,
            plan_id="PLAN-1", tool_call_ids=["TC-1"],
            completed_at="2026-08-27T01:00:00Z",
        )
        s1.create(original)
        if s1._conn is not None:
            s1._conn.close()

        s2 = TaskStoreV2(db)      # fresh instance over the same file
        loaded = s2.get("T-R")
        assert loaded == original
