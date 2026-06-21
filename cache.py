"""On-disk cache keyed by date (B7).

A cache hit avoids any network call. Entries are namespaced by an explicit
date stamp so yesterday's data never satisfies today's lookup.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, cache_dir: str | Path = ".cache") -> None:
        self.dir = Path(cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str, key: str, stamp: str) -> Path:
        digest = hashlib.sha1(f"{namespace}:{key}:{stamp}".encode()).hexdigest()[:16]
        safe_ns = "".join(c if c.isalnum() else "_" for c in namespace)[:32]
        return self.dir / f"{safe_ns}_{digest}.json"

    def get(self, namespace: str, key: str, stamp: str | None = None) -> Any | None:
        stamp = stamp or date.today().isoformat()
        path = self._path(namespace, key, stamp)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return None

    def set(self, namespace: str, key: str, value: Any, stamp: str | None = None) -> None:
        stamp = stamp or date.today().isoformat()
        path = self._path(namespace, key, stamp)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(value, fh, default=str)
        tmp.replace(path)
