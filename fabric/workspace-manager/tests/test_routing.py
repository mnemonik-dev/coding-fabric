"""Tests for the runtime routing table, API and Kimi adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import dispatch
import routing
from agent_runner import get_adapter, known_engines
from kaneo_client import Ticket
from routing import RoutingTable
from workflow_loader import WorkflowDefinition


def _wf(engine: str = "claude", model: str | None = None) -> WorkflowDefinition:
    return WorkflowDefinition(
        stage="code", engine=engine, model=model, allowed_tools=(), prompt=""
    )


@pytest.fixture
def table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RoutingTable:
    t = RoutingTable(tmp_path / "routing.json")
    monkeypatch.setattr(routing, "_table", t)
    return t


def test_set_get_clear_persist(table: RoutingTable, tmp_path: Path) -> None:
    table.set("code", model="sonnet")
    table.set("code", engine="kimi")
    assert table.get("code") == {"model": "sonnet", "engine": "kimi"}
    assert RoutingTable(tmp_path / "routing.json").get("code")["engine"] == "kimi"
    assert table.clear("code") is True
    assert table.clear("code") is False
    assert table.get("code") == {}


def test_external_edit_is_picked_up(table: RoutingTable, tmp_path: Path) -> None:
    assert table.get("code") == {}
    (tmp_path / "routing.json").write_text(json.dumps({"stages": {"code": {"model": "opus"}}}))
    assert table.get("code") == {"model": "opus"}


def test_validation(table: RoutingTable) -> None:
    with pytest.raises(ValueError):
        table.set("Bad Stage", model="x")
    with pytest.raises(ValueError, match="unknown engine"):
        table.set("code", engine="nope")
    with pytest.raises(ValueError):
        table.set("code", model="rm -rf")


def test_corrupt_file_ignored(table: RoutingTable, tmp_path: Path) -> None:
    (tmp_path / "routing.json").write_text("{nope")
    assert table.get("code") == {}


def test_routing_applies_then_labels_win(table: RoutingTable) -> None:
    table.set("code", model="sonnet")
    base = _wf(model="opus")
    assert dispatch.apply_routing(base, "code").model == "sonnet"
    ticket = Ticket("t", "t", "", "to-do", "p", ("model:haiku",), {})
    routed = dispatch.apply_routing(base, "code")
    assert dispatch.apply_overrides(routed, ticket).model == "haiku"
    assert dispatch.apply_routing(base, "review").model == "opus"  # other stage untouched


def test_routing_engine_switch_drops_workflow_model(table: RoutingTable) -> None:
    table.set("code", engine="kimi")
    wf = dispatch.apply_routing(_wf(model="opus"), "code")
    assert (wf.engine, wf.model) == ("kimi", None)


def test_kimi_adapter_argv() -> None:
    assert "kimi" in known_engines()
    adapter = get_adapter("kimi")
    assert adapter.build_argv(prompt="do it", mcp_config=None, model=None, allowed_tools=()) == [
        "kimi",
        "-p",
        "do it",
    ]
    assert adapter.build_argv(
        prompt="p", mcp_config="/x.json", model="kimi-code/kimi-for-coding", allowed_tools=("Bash",)
    ) == ["kimi", "-m", "kimi-code/kimi-for-coding", "-p", "p"]


def test_routing_api(table: RoutingTable, monkeypatch: pytest.MonkeyPatch) -> None:
    import main

    client = TestClient(main.app)
    r = client.put("/routing/code", json={"model": "sonnet"})
    assert r.status_code == 200 and r.json()["stages"]["code"] == {"model": "sonnet"}
    assert client.get("/routing").json()["engines"] == known_engines()
    assert client.put("/routing/code", json={"engine": "nope"}).status_code == 422
    assert client.delete("/routing/code").json()["stages"] == {}
