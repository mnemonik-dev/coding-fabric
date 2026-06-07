"""UUID v4 validator on Job.id — strict RFC 4122 v4 only.

Defense-in-depth: rejecting path-traversal characters at the model layer means
that any downstream code that interpolates `job.id` into a filesystem path
cannot be tricked even by a malformed line that bypasses front-door validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from content_publisher.models import Job, JobStatus


def _base(**overrides):
    payload = {
        "id": "11111111-1111-4111-8111-111111111111",
        "created_at": "2026-06-06T14:00:00Z",
        "prompt": "p",
        "mode": "approval",
        "status": JobStatus.QUEUED,
    }
    payload.update(overrides)
    return payload


def test_accepts_canonical_uuid_v4() -> None:
    j = Job(**_base(id="abcd1234-12ab-4def-89ab-0123456789ab"))
    assert j.id == "abcd1234-12ab-4def-89ab-0123456789ab"


def test_rejects_uuid_v1() -> None:
    # version digit '1' instead of '4'
    with pytest.raises(ValidationError):
        Job(**_base(id="abcd1234-12ab-1def-89ab-0123456789ab"))


def test_rejects_path_traversal() -> None:
    for bad in ("../../etc/passwd", "..", "/abs", "back\\slash"):
        with pytest.raises(ValidationError):
            Job(**_base(id=bad))


def test_rejects_control_chars() -> None:
    base_id = "abcd1234-12ab-4def-89ab-0123456789ab"
    for ctl in ("\x00", "\n", "\r", "\t"):
        with pytest.raises(ValidationError):
            Job(**_base(id=base_id + ctl))


def test_rejects_uppercase_hex() -> None:
    with pytest.raises(ValidationError):
        Job(**_base(id="ABCD1234-12AB-4DEF-89AB-0123456789AB"))
