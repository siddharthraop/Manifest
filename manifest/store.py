"""Where runs, rules, reviews and reports live in the cloud (build step 4, ADR 0008).

    Firestore (default) database
      runs/{run_id}       one migration: map, mode, status, gate, summary, metrics, outcome
      rules/{rule_id}     mapping rules: proposed by a run, approved or rejected by a writer
      reviews/{id}        topics the agent sent to a writer
      audit/{auto}        every write: who, what, when (the handoff's audit-trail rule)
    Cloud Storage
      runs/{run_id}/...   report.html and report.json, run.json, proposed rules, review issues

Two implementations share one interface: Firestore + Cloud Storage in production, memory + a
local folder in tests and on a laptop. The Google libraries are imported only when used.
"""

from __future__ import annotations

import datetime as dt
import mimetypes
import shutil
from pathlib import Path
from typing import Any, Protocol

COLLECTIONS = ("runs", "rules", "reviews", "audit")


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class NotFound(KeyError):
    pass


class Store(Protocol):
    def get(self, collection: str, doc_id: str) -> dict[str, Any]: ...
    def list(self, collection: str, limit: int = 50, **where: str) -> list[dict[str, Any]]: ...
    def put(self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str) -> None: ...
    def merge(self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str) -> None: ...


class Blobs(Protocol):
    def upload_dir(self, local: Path, prefix: str) -> list[str]: ...
    def read(self, path: str) -> tuple[bytes, str]: ...


# ---------------------------------------------------------------- in memory (tests, laptop)


class MemoryStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict[str, Any]]] = {c: {} for c in COLLECTIONS}

    def get(self, collection: str, doc_id: str) -> dict[str, Any]:
        try:
            return dict(self.data[collection][doc_id])
        except KeyError:
            raise NotFound(f"{collection}/{doc_id}") from None

    def list(self, collection: str, limit: int = 50, **where: str) -> list[dict[str, Any]]:
        docs = [d for d in self.data[collection].values() if all(d.get(k) == v for k, v in where.items())]
        return [dict(d) for d in sorted(docs, key=lambda d: str(d.get("updated_at", "")), reverse=True)][
            :limit
        ]

    def _audit(self, actor: str, action: str, target: str) -> None:
        n = len(self.data["audit"]) + 1
        self.data["audit"][f"{n:08d}"] = {
            "actor": actor,
            "action": action,
            "target": target,
            "updated_at": now(),
        }

    def put(self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str) -> None:
        self.data[collection][doc_id] = {**data, "id": doc_id, "updated_at": now()}
        self._audit(actor, action, f"{collection}/{doc_id}")

    def merge(self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str) -> None:
        old = self.data[collection].get(doc_id, {})
        self.data[collection][doc_id] = {**old, **data, "id": doc_id, "updated_at": now()}
        self._audit(actor, action, f"{collection}/{doc_id}")


class LocalBlobs:
    def __init__(self, root: Path) -> None:
        self.root = root

    def upload_dir(self, local: Path, prefix: str) -> list[str]:
        dest = self.root / prefix
        shutil.copytree(local, dest, dirs_exist_ok=True)
        return sorted(str(p.relative_to(self.root)) for p in dest.rglob("*") if p.is_file())

    def read(self, path: str) -> tuple[bytes, str]:
        p = (self.root / path).resolve()
        if not p.is_relative_to(self.root.resolve()) or not p.is_file():
            raise NotFound(path)
        return p.read_bytes(), mimetypes.guess_type(p.name)[0] or "application/octet-stream"


# ---------------------------------------------------------------- Google Cloud (production)


class FirestoreStore:
    """Firestore in Native mode. Credentials and project come from the runtime service account."""

    def __init__(self, project: str | None = None) -> None:
        from google.cloud import firestore

        self.db = firestore.Client(project=project)
        self._query = firestore.Query

    def get(self, collection: str, doc_id: str) -> dict[str, Any]:
        snap = self.db.collection(collection).document(doc_id).get()
        if not snap.exists:
            raise NotFound(f"{collection}/{doc_id}")
        return dict(snap.to_dict() or {})

    def list(self, collection: str, limit: int = 50, **where: str) -> list[dict[str, Any]]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        q: Any = self.db.collection(collection)
        for k, v in where.items():
            q = q.where(filter=FieldFilter(k, "==", v))
        if not where:  # an equality filter plus order_by needs a composite index; sort those here instead
            q = q.order_by("updated_at", direction=self._query.DESCENDING)
        docs = [dict(s.to_dict() or {}) for s in q.limit(limit if not where else 500).stream()]
        return sorted(docs, key=lambda d: str(d.get("updated_at", "")), reverse=True)[:limit]

    def _write(
        self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str, merge: bool
    ) -> None:
        batch = self.db.batch()
        batch.set(
            self.db.collection(collection).document(doc_id),
            {**data, "id": doc_id, "updated_at": now()},
            merge=merge,
        )
        batch.set(
            self.db.collection("audit").document(),
            {"actor": actor, "action": action, "target": f"{collection}/{doc_id}", "updated_at": now()},
        )
        batch.commit()  # the document and its audit record are written together or not at all

    def put(self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str) -> None:
        self._write(collection, doc_id, data, actor, action, merge=False)

    def merge(self, collection: str, doc_id: str, data: dict[str, Any], actor: str, action: str) -> None:
        self._write(collection, doc_id, data, actor, action, merge=True)


class GcsBlobs:
    def __init__(self, bucket: str, project: str | None = None) -> None:
        from google.cloud import storage  # type: ignore[attr-defined]

        self.bucket = storage.Client(project=project).bucket(bucket.removeprefix("gs://"))

    def upload_dir(self, local: Path, prefix: str) -> list[str]:
        names = []
        for p in sorted(local.rglob("*")):
            if p.is_file():
                name = f"{prefix}/{p.relative_to(local)}"
                self.bucket.blob(name).upload_from_filename(
                    str(p), content_type=mimetypes.guess_type(p.name)[0]
                )
                names.append(name)
        return names

    def read(self, path: str) -> tuple[bytes, str]:
        from google.api_core.exceptions import NotFound as GcsNotFound

        blob = self.bucket.blob(path)
        try:
            data = blob.download_as_bytes()
        except GcsNotFound:
            raise NotFound(path) from None
        return data, blob.content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
