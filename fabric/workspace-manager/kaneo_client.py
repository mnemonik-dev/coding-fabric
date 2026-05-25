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
    #
    # Kaneo's REST routes (verified against usekaneo/kaneo apps/api):
    #   GET  /api/auth/organization/list             — workspaces ("orgs")
    #   GET  /api/project?workspaceId=<id>           — projects in workspace,
    #                                                  RESPONSE INCLUDES INLINE tasks[]
    #   GET  /api/project/<id>                       — one project (with tasks[])
    #   GET  /api/task/<id>                          — one task
    #   POST /api/task                               — create
    #   PATCH /api/task/<id>                         — update full body
    #   PATCH /api/task/status/<id>                  — update status only
    #   POST /api/task/move/<id>                     — move between columns
    #   GET  /api/comment/<taskId>                   — list comments
    #   POST /api/comment/<taskId>                   — create comment

    async def list_tasks_for_project(self, project_id: str) -> list[Ticket]:
        """Fetch tasks for one project."""
        data = await self.get_project(project_id)
        tasks = data.get("tasks", []) if isinstance(data, dict) else []
        return [_to_ticket(item, project_id) for item in tasks]

    async def get_project(self, project_id: str) -> dict[str, Any]:
        """Fetch a single Kaneo project (incl. description + tasks[]).

        Symphony reads ``description`` to bind a GitHub repo to the
        project via a `repo: org/name` line (see
        ``dispatch.parse_repo_from_description``).
        """
        r = await self._client.get(f"/api/project/{project_id}")
        if r.status_code == 401:
            raise KaneoUnauthorized("bearer rejected — re-pair via scripts/pair-kaneo.py")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}

    async def list_workspaces(self) -> list[dict[str, Any]]:
        """Kaneo calls workspaces 'organizations' under the hood."""
        r = await self._client.get("/api/auth/organization/list")
        if r.status_code == 401:
            raise KaneoUnauthorized("bearer rejected — re-pair via scripts/pair-kaneo.py")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("organizations", [])

    async def list_projects(self, workspace_id: str) -> list[dict[str, Any]]:
        r = await self._client.get(f"/api/project?workspaceId={workspace_id}")
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else data.get("projects", [])

    # ----- Writes -----

    async def update_task_status(self, task_id: str, status: str) -> None:
        r = await self._client.patch(
            f"/api/task/status/{task_id}",
            json={"status": status},
        )
        if r.status_code >= 400:
            raise KaneoError(f"update_task_status({task_id}, {status}) → HTTP {r.status_code}")

    async def create_comment(self, task_id: str, body: str) -> None:
        r = await self._client.post(
            f"/api/comment/{task_id}",
            json={"content": body},
        )
        if r.status_code >= 400:
            raise KaneoError(f"create_comment({task_id}) → HTTP {r.status_code}")

    async def list_comments(self, task_id: str) -> list[dict[str, Any]]:
        """Return comments on a task ordered oldest → newest."""
        r = await self._client.get(f"/api/comment/{task_id}")
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
