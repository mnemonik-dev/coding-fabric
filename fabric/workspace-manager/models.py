"""
Pydantic models for all request/response bodies.
task_id regex enforced at the model level: ^[A-Z0-9-]{3,40}$
repo: ^[a-z0-9-]{1,64}$
topic: ^[a-z][a-z0-9-]{0,32}$
base_ref: ^[a-zA-Z0-9._/-]{1,64}$ (no leading dash to block option injection)
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

TASK_ID_RE = re.compile(r"^[A-Z0-9\-]{3,40}$")

# Strict allowlists — no traversal characters, no shell metachars.
# repo: lowercase letters, digits, hyphen only; no slash, no dot-dot.
REPO_RE = r"^[a-z0-9-]{1,64}$"
# topic: must start with a letter; lowercase letters, digits, hyphen.
TOPIC_RE = r"^[a-z][a-z0-9-]{0,32}$"
# base_ref: letters, digits, dot, slash, underscore, hyphen; NO leading dash
# (would be interpreted as a git option flag).
BASE_REF_RE = r"^[a-zA-Z0-9._/][a-zA-Z0-9._/-]{0,63}$"


def _validate_task_id(v: str) -> str:
    if not TASK_ID_RE.fullmatch(v):
        raise ValueError(
            f"task_id must match ^[A-Z0-9-]{{3,40}}$, got: {v!r}"
        )
    return v


TaskId = Annotated[str, Field(min_length=3, max_length=40, pattern=r"^[A-Z0-9\-]{3,40}$")]


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateWorktreeRequest(BaseModel):
    task_id: TaskId
    repo: Annotated[str, Field(min_length=1, max_length=64, pattern=REPO_RE)]
    base_ref: Annotated[str, Field(min_length=1, max_length=64, pattern=BASE_REF_RE)]
    topic: Annotated[str, Field(min_length=1, max_length=33, pattern=TOPIC_RE)]

    @field_validator("repo", "topic")
    @classmethod
    def reject_traversal(cls, v: str) -> str:
        if ".." in v or v.startswith("/") or "\\" in v:
            raise ValueError("path traversal sequence rejected")
        return v


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class WorktreeInfo(BaseModel):
    task_id: str
    repo: str
    base_ref: str
    topic: str
    path: str
    env_path: str
    created_at: datetime
    status: str = "active"


class CreateWorktreeResponse(BaseModel):
    task_id: str
    path: str
    env_path: str
    created_at: datetime


class DeleteWorktreeResponse(BaseModel):
    task_id: str
    deleted: bool


class WorktreeListResponse(BaseModel):
    worktrees: list[WorktreeInfo]
    count: int
    capacity_used: int
    capacity_total: int


class HealthResponse(BaseModel):
    ok: bool
    sccache_mounted: bool
    vault_reachable: bool
    capacity_used: int
    capacity_total: int


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
