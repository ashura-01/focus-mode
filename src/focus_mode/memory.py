"""Local semantic memory for the agent, stored in ChromaDB.

Built to be cheap:
  * chromadb is imported only when something is first searched, and dropped again
    after 90 idle seconds, so day to day the app doesn't carry it.
  * Indexing is incremental: each document carries a content hash and only new or
    changed ones are embedded.
  * The default "light" embedding is a small feature-hashing model written for this
    app (no download, no model in RAM, microseconds per document). "neural" uses
    ChromaDB's MiniLM model: better at meaning, but ~90 MB download and more RAM.

Database access happens elsewhere (main thread); this module only needs the
documents it is handed, so it is safe to run on a worker thread.
"""

from __future__ import annotations

import gc
import hashlib
import importlib.util
import os
import re
import threading
import time
import zlib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("OMP_NUM_THREADS", "2")  # keep onnxruntime (neural mode) off most cores

IDLE_UNLOAD_SECONDS = 90
BATCH = 64
DIM = 384


class Doc(NamedTuple):
    id: str
    text: str
    meta: dict


class MemoryUnavailable(Exception):
    """ChromaDB or the chosen embedding model can't be used right now."""


# -- documents (built on the main thread from the SQLite database) ------------
def _part_of_day(hour: int) -> str:
    return "morning" if hour < 12 else "afternoon" if hour < 17 else "evening" if hour < 21 else "night"


def build_documents(db, today: date | None = None, days: int = 365) -> list[Doc]:
    today = today or db.today()
    cutoff = today - timedelta(days=days)
    docs: list[Doc] = []

    for t in db.list_tasks("all"):
        status = "done" if t.done else "open"
        when = f"{t.due_date} {t.start_time}-{t.end_time}" if t.start_time else t.due_date
        text = f"Task: {t.title}. Priority {t.priority}. Due {when}. Status {status}."
        if t.notes:
            text += f" Notes: {t.notes}"
        docs.append(Doc(f"task:{t.id}", text, {"kind": "task", "date": t.due_date}))

    sessions = db.recent_sessions(limit=5000, since=cutoff)
    by_day: dict[str, list[dict]] = {}
    for s in sessions:
        by_day.setdefault(s["started_at"][:10], []).append(s)
        if s["note"]:
            started = datetime.fromisoformat(s["started_at"])
            text = (
                f"Session on {started:%A %Y-%m-%d} in the {_part_of_day(started.hour)}: "
                f"{s['seconds'] // 60} minutes of {s['kind']}"
                + (f" on task {s['task']}" if s["task"] else "")
                + f". Note: {s['note']}"
            )
            docs.append(Doc(f"session:{s['id']}", text, {"kind": "session", "date": s["started_at"][:10]}))

    done_by_day: dict[str, list[str]] = {}
    for t in db.list_tasks("completed"):
        if t.done_at and t.done_at[:10] >= cutoff.isoformat():
            done_by_day.setdefault(t.done_at[:10], []).append(t.title)
    for day, rows in by_day.items():
        d = date.fromisoformat(day)
        minutes = sum(r["seconds"] for r in rows) // 60
        tasks_worked = sorted({r["task"] for r in rows if r["task"]})
        text = f"Day {d:%A %Y-%m-%d}: {minutes} minutes focused in {len(rows)} sessions."
        if tasks_worked:
            text += " Worked on: " + ", ".join(tasks_worked) + "."
        if done_by_day.get(day):
            text += " Completed tasks: " + ", ".join(done_by_day[day]) + "."
        notes = [r["note"] for r in rows if r["note"]]
        if notes:
            text += " Notes: " + " | ".join(notes[:5])
        docs.append(Doc(f"day:{day}", text, {"kind": "day", "date": day}))
    return docs


def _digest(text: str) -> str:
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


# -- light embedding ------------------------------------------------------------
_STOP = frozenset(
    "a an and are as at be by for from has have i in is it its of on or that the this to was were will with my me we you your".split()
)


def _stem(word: str) -> str:
    for suffix in ("ing", "ies", "ed", "es", "s", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return word


def hashed_vector(text: str, dim: int = DIM):
    """Feature-hashed bag of stems + bigrams + char trigrams, L2-normalised."""
    import numpy as np

    vec = np.zeros(dim, dtype=np.float32)
    words = [_stem(w) for w in re.findall(r"[^\W_]+", text.lower()) if w not in _STOP]

    def add(feature: str, weight: float) -> None:
        h = zlib.crc32(feature.encode())
        vec[h % dim] += weight if (h >> 17) & 1 else -weight

    for i, w in enumerate(words):
        add("w:" + w, 1.0)
        if i + 1 < len(words):
            add("b:" + w + " " + words[i + 1], 0.7)
        if len(w) >= 4:  # partial-word matches ("tree" ~ "trees", typos)
            for j in range(len(w) - 2):
                add("c:" + w[j : j + 3], 0.2)
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm else vec


_HASH_EF = None


def hashing_embedding():
    """Build the Chroma embedding function lazily (so importing this module stays free)."""
    global _HASH_EF
    if _HASH_EF is None:
        from chromadb.api.types import Documents, EmbeddingFunction

        class FocusHashEmbedding(EmbeddingFunction[Documents]):
            def __init__(self, dim: int = DIM) -> None:
                self.dim = dim

            def __call__(self, input: Documents):  # noqa: A002 - signature required by chromadb
                return [hashed_vector(t, self.dim) for t in input]

            @staticmethod
            def name() -> str:
                return "focus_hash"

            def get_config(self) -> dict:
                return {"dim": self.dim}

            @staticmethod
            def build_from_config(config: dict):
                return FocusHashEmbedding(**config)

        _HASH_EF = FocusHashEmbedding
    return _HASH_EF()


# -- fallback without chromadb ------------------------------------------------------
def keyword_search(docs: list[Doc], query: str, n: int = 5, kind: str | None = None) -> list[dict]:
    q = {_stem(w) for w in re.findall(r"[^\W_]+", query.lower()) if w not in _STOP}
    scored = []
    for d in docs:
        if kind and d.meta.get("kind") != kind:
            continue
        words = {_stem(w) for w in re.findall(r"[^\W_]+", d.text.lower())}
        hits = len(q & words)
        if hits:
            scored.append((hits / (len(q) or 1), d))
    scored.sort(key=lambda x: (-x[0], x[1].meta.get("date", "")), reverse=False)
    return [
        {"id": d.id, "text": d.text, "kind": d.meta.get("kind"), "date": d.meta.get("date"), "score": round(s, 3)}
        for s, d in scored[:n]
    ]


# -- the store -------------------------------------------------------------------------
class Memory:
    def __init__(self, path: Path, mode: str = "light") -> None:
        self.path = Path(path)
        self.mode = mode if mode in ("light", "neural") else "light"
        self._lock = threading.RLock()
        self._client = None
        self._col = None
        self._timer: threading.Timer | None = None

    @staticmethod
    def available() -> bool:
        return importlib.util.find_spec("chromadb") is not None

    def set_mode(self, mode: str) -> None:
        mode = mode if mode in ("light", "neural") else "light"
        with self._lock:
            if mode != self.mode:
                self.unload()
                self.mode = mode

    @property
    def loaded(self) -> bool:
        return self._col is not None

    # -- lifecycle --
    def _open(self):
        if self._col is not None:
            return self._col
        if not self.available():
            raise MemoryUnavailable("ChromaDB isn't installed (pip install chromadb).")
        try:
            import chromadb
            from chromadb.config import Settings

            self.path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.path), settings=Settings(anonymized_telemetry=False))
            if self.mode == "neural":
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

                ef = DefaultEmbeddingFunction()
                ef(["warm up"])  # downloads the model on first use; fail here, not mid-query
            else:
                ef = hashing_embedding()
            self._col = self._client.get_or_create_collection(
                f"focus_{self.mode}", embedding_function=ef, metadata={"hnsw:space": "cosine"}
            )
        except MemoryUnavailable:
            raise
        except Exception as exc:
            self._client = self._col = None
            raise MemoryUnavailable(f"Couldn't start the {self.mode} memory: {exc}") from exc
        return self._col

    def _touch(self) -> None:
        if self._timer:
            self._timer.cancel()
        self._timer = threading.Timer(IDLE_UNLOAD_SECONDS, self.unload)
        self._timer.daemon = True
        self._timer.start()

    def unload(self) -> None:
        """Drop the client and (neural) model from RAM. Data stays on disk."""
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            was_open = self._client is not None
            self._col = self._client = None
            if was_open:
                try:
                    from chromadb.api.client import SharedSystemClient

                    SharedSystemClient.clear_system_cache()
                except Exception:
                    pass
                gc.collect()

    # -- operations --
    def sync(self, docs: list[Doc]) -> dict:
        """Make the index match `docs`, embedding only what changed."""
        with self._lock:
            col = self._open()
            existing = col.get(include=["metadatas"])
            have = {i: (m or {}).get("h") for i, m in zip(existing["ids"], existing["metadatas"])}
            wanted = {d.id: d for d in docs}
            stale = [i for i in have if i not in wanted]
            todo = [d for d in docs if have.get(d.id) != _digest(d.text)]
            for i in range(0, len(todo), BATCH):
                chunk = todo[i : i + BATCH]
                col.upsert(
                    ids=[d.id for d in chunk],
                    documents=[d.text for d in chunk],
                    metadatas=[{**d.meta, "h": _digest(d.text)} for d in chunk],
                )
                time.sleep(0.005)  # be polite to a busy low-end CPU
            for i in range(0, len(stale), 500):
                col.delete(ids=stale[i : i + 500])
            self._touch()
            return {"indexed": len(todo), "removed": len(stale), "total": len(docs)}

    def search(self, query: str, n: int = 5, kind: str | None = None) -> list[dict]:
        with self._lock:
            col = self._open()
            total = col.count()
            if not total:
                return []
            res = col.query(
                query_texts=[query],
                n_results=max(1, min(n, total)),
                where={"kind": kind} if kind else None,
                include=["documents", "metadatas", "distances"],
            )
            self._touch()
            out = []
            for i, text, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
                out.append(
                    {"id": i, "text": text, "kind": meta.get("kind"), "date": meta.get("date"), "score": round(1 - float(dist), 3)}
                )
            return out

    def rebuild(self, docs: list[Doc]) -> dict:
        with self._lock:
            col = self._open()
            ids = col.get(include=[])["ids"]
            for i in range(0, len(ids), 500):
                col.delete(ids=ids[i : i + 500])
            return self.sync(docs)

    def wipe(self) -> None:
        """Delete the whole vector store from disk."""
        import shutil

        with self._lock:
            self.unload()
            shutil.rmtree(self.path, ignore_errors=True)

    def count(self) -> int:
        with self._lock:
            return self._open().count()
