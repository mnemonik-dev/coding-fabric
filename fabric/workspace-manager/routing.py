"""Dynamic per-stage engine/model routing, editable at runtime.

A small JSON file (``SYMPHONY_ROUTING_FILE``) maps a stage to an engine and/or
model. It is re-read whenever its mtime changes, so edits — by hand or through
the ``/routing`` API — apply to the next dispatch with no restart or redeploy.

Precedence for a run: ticket labels (``engine:`` / ``model:``)  >  this table
>  the workflow file's front matter.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any

from agent_runner import get_adapter

logger = logging.getLogger(__name__)

STAGE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")


class RoutingTable:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._mtime: int = -1
        self._stages: dict[str, dict[str, str]] = {}

    def _reload(self) -> None:
        try:
            mtime = self._path.stat().st_mtime_ns
        except FileNotFoundError:
            self._mtime, self._stages = -1, {}
            return
        if mtime == self._mtime:
            return
        stages: dict[str, dict[str, str]] = {}
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
            for stage, entry in (raw.get("stages") or {}).items():
                if STAGE_RE.match(str(stage)) and isinstance(entry, dict):
                    stages[stage] = {
                        k: str(v) for k, v in entry.items() if k in ("engine", "model") and v
                    }
        except (OSError, ValueError, AttributeError):
            logger.exception("routing: cannot read %s; ignoring it", self._path)
            stages = {}
        self._mtime, self._stages = mtime, stages

    def get(self, stage: str) -> dict[str, str]:
        with self._lock:
            self._reload()
            return dict(self._stages.get(stage, {}))

    def all(self) -> dict[str, dict[str, str]]:
        with self._lock:
            self._reload()
            return {s: dict(e) for s, e in self._stages.items()}

    def set(self, stage: str, *, engine: str | None = None, model: str | None = None) -> None:
        """Merge ``engine``/``model`` into a stage's entry. Raises ValueError."""
        if not STAGE_RE.match(stage):
            raise ValueError(f"invalid stage: {stage!r}")
        if engine:
            get_adapter(engine)
        if model and not MODEL_RE.match(model):
            raise ValueError(f"invalid model: {model!r}")
        with self._lock:
            self._reload()
            entry = dict(self._stages.get(stage, {}))
            if engine:
                entry["engine"] = engine
            if model:
                entry["model"] = model
            self._stages[stage] = entry
            self._save()

    def clear(self, stage: str) -> bool:
        with self._lock:
            self._reload()
            if self._stages.pop(stage, None) is None:
                return False
            self._save()
            return True

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps({"stages": self._stages}, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
        self._mtime = self._path.stat().st_mtime_ns


_table: RoutingTable | None = None


def get_routing() -> RoutingTable:
    global _table
    if _table is None:
        path = os.environ.get(
            "SYMPHONY_ROUTING_FILE",
            str(Path.home() / ".fabric" / "workspace-manager" / "routing.json"),
        )
        _table = RoutingTable(Path(path))
    return _table
