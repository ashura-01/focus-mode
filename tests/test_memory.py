from datetime import datetime, timedelta

import pytest

from focus_mode import memory
from focus_mode.db import Database
from focus_mode.memory import Memory, build_documents, keyword_search


@pytest.fixture
def db():
    now = datetime(2026, 10, 12, 10, 0)
    d = Database(":memory:", now=lambda: now)
    t1 = d.add_task("Study for Data Structures", "high", "2026-10-12", notes="Binary trees and recursion are hard for me")
    d.add_task("Morning Workout", "low", "2026-10-12", notes="5k run")
    d.add_session("focus", 1500, True, t1, started_at=now - timedelta(days=1, hours=2), note="Struggled with AVL tree rotations")
    d.add_session("focus", 1500, True, None, started_at=now - timedelta(days=2, hours=1), note="Wrote the essay introduction")
    d.add_session("focus", 900, True, None, started_at=now - timedelta(days=2))
    d.set_done(t1, True)
    return d


def test_documents_cover_tasks_sessions_days(db):
    docs = build_documents(db)
    kinds = sorted({d.meta["kind"] for d in docs})
    assert kinds == ["day", "session", "task"]
    ids = {d.id for d in docs}
    assert "task:1" in ids and any(i.startswith("day:") for i in ids)
    # only sessions that have notes become their own document
    assert sum(d.meta["kind"] == "session" for d in docs) == 2
    assert any("Binary trees" in d.text for d in docs)


def test_light_search_is_relevant_and_incremental(db, tmp_path):
    mem = Memory(tmp_path / "chroma", "light")
    docs = build_documents(db)
    first = mem.sync(docs)
    assert first["indexed"] == len(docs) and first["removed"] == 0
    top = mem.search("tree rotations", n=3)
    assert top and "tree" in top[0]["text"].lower()
    essay = mem.search("essay writing", n=1)
    assert "essay" in essay[0]["text"].lower()
    only_sessions = mem.search("workout", n=5, kind="session")
    assert all(r["kind"] == "session" for r in only_sessions)

    # nothing changed -> nothing re-embedded
    assert mem.sync(docs)["indexed"] == 0
    # edit one note -> exactly one document re-embedded; delete a task -> removed
    db.update_task(2, notes="10k run along the river")
    db.delete_task(1)
    again = mem.sync(build_documents(db))
    assert again["indexed"] >= 1 and again["removed"] == 1
    assert mem.count() == len(build_documents(db))


def test_unload_and_reload_keep_data(db, tmp_path):
    mem = Memory(tmp_path / "chroma", "light")
    mem.sync(build_documents(db))
    assert mem.loaded
    mem.unload()
    assert not mem.loaded
    assert mem.search("essay", n=1)  # reopens from disk
    mem.unload()


def test_rebuild_and_empty(tmp_path, db):
    mem = Memory(tmp_path / "chroma", "light")
    assert mem.search("anything") == []
    mem.sync(build_documents(db))
    out = mem.rebuild(build_documents(db))
    assert out["indexed"] == out["total"]
    mem.unload()


def test_modes_use_separate_collections(db, tmp_path):
    mem = Memory(tmp_path / "chroma", "light")
    mem.sync(build_documents(db))
    mem.set_mode("bogus")  # invalid -> stays light
    assert mem.mode == "light"
    mem.unload()


def test_keyword_fallback(db):
    docs = build_documents(db)
    hits = keyword_search(docs, "binary trees recursion", n=2)
    assert hits and "Binary trees" in hits[0]["text"]
    assert keyword_search(docs, "zzzz") == []


def test_hashed_vector_properties():
    import numpy as np

    a = memory.hashed_vector("binary trees are hard")
    b = memory.hashed_vector("binary tree")
    c = memory.hashed_vector("morning run in the park")
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5
    assert float(a @ b) > float(a @ c)
    assert float(np.linalg.norm(memory.hashed_vector(""))) == 0.0
