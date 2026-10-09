"""Content-addressed artifact store (ARCHITECTURE §5.1, §15.2).

Files are written under artifacts/<sha[:2]>/<sha> and never modified. The
file is written first (atomic rename) and registered in the DB afterwards.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from robustlab.storage.db import Database, now_iso


class ArtifactStore:
    def __init__(self, root: Path, db: Database):
        self.root = root
        self.db = db
        root.mkdir(parents=True, exist_ok=True)

    def rel_path(self, sha: str) -> str:
        return f"{sha[:2]}/{sha}"

    def path(self, sha: str) -> Path:
        return self.root / self.rel_path(sha)

    def put_bytes(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        dest = self.path(sha)
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, dest)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        self.db.insert(
            "artifact",
            {"sha256": sha, "size": len(data), "rel_path": self.rel_path(sha), "created_at": now_iso()},
            if_absent=True,
        )
        return sha

    def put_file(self, path: Path) -> str:
        return self.put_bytes(path.read_bytes())

    def read(self, sha: str) -> bytes:
        data = self.path(sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"artifact {sha} is corrupted")
        return data
