"""Minimal async client for Kaneo's REST API.

Symphony reads the active-ticket queue from Kaneo and writes state
transitions + progress comments back. We use Kaneo's REST API directly
(not its MCP server) because Symphony is a server-side daemon — MCP's
session-bearer model is designed for stdio-attached LLM clients, REST
is more natural for a long-running poller.

Authentication: a session bearer obtained via the same OAuth 2.0
device-code flow the bot uses (POST /api/auth/device/code →
/api/auth/device/token). Bearer is read from ``KANEO_MCP_BEARER`` env
on service start; ``KANEO_BASE_URL`` carries the canonical URL
(``https://kaneo.mnemonic-fabric.ts:8443`` in this stack).

Polling cadence is owned by ``poll_loop`` — this module is just the
client transport.
"""

from __future__ import annotations

import logging
import os
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Ticket:
    """Normalized Kaneo task as Symphony sees it."""

    id: str
    title: str
    description: str
    status: str
    project_id: str
    labels: tuple[str, ...]
    # Useful for hands-off scheduling: lowercased label set, ordered by
    # priority — Symphony picks a workflow stage from this in dispatch.
    raw: dict[str, Any]


class KaneoError(Exception):
    pass


class KaneoUnauthorized(KaneoError):
    pass


class KaneoClient:
    def __init__(
        self,
        base_url: str | None = None,
        bearer: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base_url = (base_url or os.environ.get("KANEO_BASE_URL", "")).rstrip("/")
        self._bearer = bearer or os.environ.get("KANEO_MCP_BEARER", "")
        if not self._base_url or not self._bearer:
            raise KaneoError(
                "KaneoClient requires KANEO_BASE_URL + KANEO_MCP_BEARER (or constructor args)"
            )
        # Caddy serves Kaneo's vhost with internal-CA TLS — disable cert
        # verification at the transport. Same posture as the bot's own
        # MCP entry (Authorization header is the only secret guard).
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._bearer}"},
            timeout=timeout,
            verify=ssl_ctx,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----- Reads -----

    async def list_tasks_for_project(self, project_id: str) -> list[Ticket]:
        """Fetch tasks for one project. Filter to non-terminal upstream."""
        r = await self._client.get(f"/api/projects/{project_id}/tasks")
        if r.status_code == 401:
            raise KaneoUnauthorized("bearer rejected — re-pair via scripts/pair-kaneo.py")
        r.raise_for_status()
        items = r.json() if isinstance(r.json(), list) else r.json().get("tasks", [])
        return [_to_ticket(item, project_id) for item in items]

    async def list_workspaces(self) -> list[dict[str, Any]]:
        r = await self._client.get("/api/workspaces")
        if r.status_code == 401:
            raise KaneoUnauthorized("bearer rejected — re-pair via scripts/pair-kaneo.py")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("workspaces", [])

    async def list_projects(self, workspace_id: str) -> list[dict[str, Any]]:
        r = await self._client.get(f"/api/workspaces/{workspace_id}/projects")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("projects", [])

    # ----- Writes -----

    async def update_task_status(self, task_id: str, status: str) -> None:
        r = await self._client.patch(
            f"/api/tasks/{task_id}",
            json={"status": status},
        )
        if r.status_code >= 400:
            raise KaneoError(f"update_task_status({task_id}, {status}) → HTTP {r.status_code}")

    async def create_comment(self, task_id: str, body: str) -> None:
        r = await self._client.post(
            f"/api/tasks/{task_id}/comments",
            json={"body": body},
        )
        if r.status_code >= 400:
            raise KaneoError(f"create_comment({task_id}) → HTTP {r.status_code}")

    async def list_comments(self, task_id: str) -> list[dict[str, Any]]:
        """Return comments on a task ordered oldest → newest."""
        r = await self._client.get(f"/api/tasks/{task_id}/comments")
        if r.status_code == 401:
            raise KaneoUnauthorized("bearer rejected — re-pair via scripts/pair-kaneo.py")
        if r.status_code >= 400:
            raise KaneoError(f"list_comments({task_id}) → HTTP {r.status_code}")
        data = r.json()
        items = data if isinstance(data, list) else data.get("comments", [])
        return list(items)


def _to_ticket(item: dict[str, Any], project_id: str) -> Ticket:
    raw_labels = item.get("labels") or []
    labels: list[str] = []
    for label in raw_labels:
        if isinstance(label, str):
            labels.append(label.lower())
        elif isinstance(label, dict) and isinstance(label.get("name"), str):
            labels.append(label["name"].lower())
    return Ticket(
        id=str(item.get("id", "")),
        title=str(item.get("title", "")),
        description=str(item.get("description") or ""),
        status=str(item.get("status", "")),
        project_id=project_id,
        labels=tuple(labels),
        raw=item,
    )
