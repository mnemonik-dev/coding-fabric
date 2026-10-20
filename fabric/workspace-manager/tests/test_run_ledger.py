"""Unit tests for the per-ticket run ledger."""

from __future__ import annotations

from pathlib import Path

from run_ledger import RunLedger, ledger_key


def test_new_ticket_runs(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path / "l.json")
    assert ledger.should_run("T1", ledger_key("to-do", "code"))


def test_success_blocks_rerun_until_key_changes(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path / "l.json")
    key = ledger_key("to-do", "code")
    assert ledger.record("T1", key, ok=True) is False
    assert not ledger.should_run("T1", key)
    assert ledger.should_run("T1", ledger_key("review", "review"))


def test_failure_retries_then_gives_up(tmp_path: Path) -> None:
    ledger = RunLedger(tmp_path / "l.json", max_attempts=2)
    key = ledger_key("to-do", "code")
    assert ledger.record("T1", key, ok=False) is False
    assert ledger.should_run("T1", key)
    assert ledger.record("T1", key, ok=False) is True  # exhausted
    assert not ledger.should_run("T1", key)


def test_persists_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "l.json"
    key = ledger_key("qa", "qa")
    RunLedger(path).record("T1", key, ok=True)
    assert not RunLedger(path).should_run("T1", key)


def test_corrupt_file_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "l.json"
    path.write_text("{not json")
    assert RunLedger(path).should_run("T1", "x|code")
