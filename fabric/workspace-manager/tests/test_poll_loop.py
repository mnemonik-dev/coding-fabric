"""Poll-loop behaviour: ledger gating and workspace cleanup."""

from __future__ import annotations

from pathlib import Path

import poll_loop
from dispatch import DispatchOutcome
from kaneo_client import Ticket
from poll_loop import PollLoop, PollLoopConfig


def _ticket(id_: str = "ABC123", status: str = "to-do") -> Ticket:
    return Ticket(id=id_, title="t", description="", status=status,
                  project_id="p", labels=(), raw={})


class FakeKaneo:
    def __init__(self) -> None:
        self.comments: list[str] = []

    async def create_comment(self, ticket_id: str, body: str) -> None:
        self.comments.append(body)


def _loop(tmp_path: Path, monkeypatch) -> PollLoop:
    monkeypatch.setenv("SYMPHONY_LEDGER_FILE", str(tmp_path / "ledger.json"))
    monkeypatch.setenv("SYMPHONY_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("SYMPHONY_REPO_ROOT", str(tmp_path / "repo"))
    return PollLoop(FakeKaneo(), PollLoopConfig())  # type: ignore[arg-type]


async def test_cleanup_removes_workspace_of_done_ticket(tmp_path, monkeypatch) -> None:
    loop = _loop(tmp_path, monkeypatch)
    ws = tmp_path / "ws" / "ABC123"
    ws.mkdir(parents=True)
    (ws / "f").write_text("x")
    await loop._cleanup_finished([_ticket(status="done")])
    assert not ws.exists()


async def test_cleanup_keeps_active_ticket_workspace(tmp_path, monkeypatch) -> None:
    loop = _loop(tmp_path, monkeypatch)
    ws = tmp_path / "ws" / "ABC123"
    ws.mkdir(parents=True)
    await loop._cleanup_finished([_ticket(status="review")])
    assert ws.exists()


async def test_dispatch_recorded_then_not_repeated(tmp_path, monkeypatch) -> None:
    loop = _loop(tmp_path, monkeypatch)
    calls = 0

    class _Res:
        exit_code = 0

    async def fake_dispatch(ticket, **_):
        nonlocal calls
        calls += 1
        return DispatchOutcome(ticket.id, "code", "claude", None, _Res())  # type: ignore[arg-type]

    monkeypatch.setattr(poll_loop, "dispatch_ticket", fake_dispatch)
    t = _ticket()
    assert loop._should_dispatch(t)
    await loop._dispatch_with_semaphore(t)
    assert calls == 1
    assert not loop._should_dispatch(t)


async def test_skipped_outcome_not_recorded(tmp_path, monkeypatch) -> None:
    loop = _loop(tmp_path, monkeypatch)

    async def fake_dispatch(ticket, **_):
        return DispatchOutcome(ticket.id, "code", "claude", None, None, skipped=True)

    monkeypatch.setattr(poll_loop, "dispatch_ticket", fake_dispatch)
    t = _ticket()
    await loop._dispatch_with_semaphore(t)
    assert loop._should_dispatch(t)
