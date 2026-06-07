"""Pydantic Job model + JobStatus enum + state-machine transition map.

The 13 status values, the 27+ fields, and the transition graph are all pinned
by tech-spec Data Models. Any change here must be reflected there first.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Strict RFC 4122 v4: lowercase hex only; version digit pinned to '4';
# variant digit constrained to {8,9,a,b}. Anchored on both sides — rejects any
# extraneous control char, whitespace, or path component.
UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


class JobStatus(StrEnum):
    QUEUED = "queued"
    WRITING = "writing"
    SCORING = "scoring"
    PREVIEW_SENT = "preview-sent"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    ATTEST_PENDING = "attest-pending"
    DONE = "done"
    REJECTED = "rejected"
    REGENERATED = "regenerated"
    PUBLISH_FAILED = "publish-failed"
    ATTEST_FAILED = "attest-failed"
    FAILED = "failed"


class Job(BaseModel):
    """One ticket in queue.jsonl. Field set is fixed by tech-spec Data Models."""

    model_config = ConfigDict(extra="forbid")

    # Identity / lifecycle timestamps
    id: str
    created_at: datetime
    fire_at: datetime | None = None
    approval_deadline: datetime | None = None
    cleanup_at: datetime | None = None

    # Operator intent
    prompt: str
    feedback_for_retry: str | None = None
    mode: Literal["approval", "auto"]

    # State machine
    status: JobStatus

    # Scoring artefacts
    score: int | None = None
    issues: list[str] = Field(default_factory=list)

    # Worktree / article
    worktree_path: str | None = None
    article_path: str | None = None

    # Preview mirror
    preview_segments: list[str] = Field(default_factory=list)
    preview_message_id: int | None = None
    preview_pending: bool = False
    notify_pending: bool = False

    # Telegram routing echo (so the bot can reply on the right thread)
    chat_id: int | None = None
    thread_id: int | None = None
    reply_to_message_id: int | None = None

    # Publishing
    tentative_publish_started_at: datetime | None = None
    publish_error: str | None = None
    post_url: str | None = None
    post_message_ids: list[int] = Field(default_factory=list)
    published_at: datetime | None = None

    # Attestation
    attest_pending_since: datetime | None = None
    content_sha256: str | None = None
    attestation_hash: str | None = None
    attest_attempts: int = 0

    # Diagnostics for terminal-error states
    error_message: str | None = None

    # Regenerate linkage
    regenerated_to: str | None = None
    retries_of: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_uuid_v4(cls, v: str) -> str:
        if not UUID4_RE.fullmatch(v):
            raise ValueError(
                f"id must be a lowercase canonical UUID v4 (RFC 4122), got: {v!r}"
            )
        return v

    @classmethod
    def allowed_transitions(cls) -> dict[JobStatus, set[JobStatus]]:
        """State-machine map from tech-spec Data Models diagram.

        Terminal states map to empty sets. ``regenerated`` is treated as
        terminal: the [Retry] flow creates a NEW job and links via
        ``regenerated_to`` / ``retries_of`` rather than mutating the original.
        """
        return {
            JobStatus.QUEUED: {JobStatus.WRITING, JobStatus.FAILED},
            JobStatus.WRITING: {JobStatus.SCORING, JobStatus.FAILED},
            JobStatus.SCORING: {JobStatus.PREVIEW_SENT, JobStatus.FAILED},
            JobStatus.PREVIEW_SENT: {
                JobStatus.PUBLISHING,
                JobStatus.REJECTED,
                JobStatus.REGENERATED,
            },
            JobStatus.PUBLISHING: {JobStatus.PUBLISHED, JobStatus.PUBLISH_FAILED},
            # ``published → done`` is the attest-on-first-try happy path; the
            # task spec is explicit ("на успехе CAS published → done").
            # ``published → attest-pending`` is the retry-needed branch.
            JobStatus.PUBLISHED: {JobStatus.DONE, JobStatus.ATTEST_PENDING},
            JobStatus.ATTEST_PENDING: {JobStatus.DONE, JobStatus.ATTEST_FAILED},
            # terminal states
            JobStatus.DONE: set(),
            JobStatus.REJECTED: set(),
            JobStatus.REGENERATED: set(),
            JobStatus.PUBLISH_FAILED: set(),
            JobStatus.ATTEST_FAILED: set(),
            JobStatus.FAILED: set(),
        }
