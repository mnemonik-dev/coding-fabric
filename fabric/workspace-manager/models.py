"""
Pydantic models for all request/response bodies.
task_id regex enforced at the model level: ^[A-Z0-9-]{3,40}$
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

TASK_ID_RE = re.compile(r"^[A-Z0-9\-]{3,40}$")


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
    repo: str = Field(min_length=1, max_length=200)
    base_ref: str = Field(min_length=1, max_length=200)
    topic: str = Field(min_length=1, max_length=100)


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
