"""
Bitwarden CLI (bw) wrapper for per-worktree .env materialisation.

Session token is read from the systemd LoadCredential path (never from an
environment variable on disk).  The decrypted vault content is NEVER logged
— only structured metadata such as {"topic": "docs", "key_count": 5} is
emitted.

Per-worktree .env file:
  - Written at <worktree_path>/.env
  - Mode 0600, owner of the running process (op in production)
  - Deleted on DELETE /worktree/{task_id}

Bitwarden query: `bw get item mnemonic/topic/<topic>` — expects a secure-note
or login item whose notes field contains KEY=VALUE lines, one per line.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_MODE = 0o600


class VaultError(Exception):
    pass


class VaultUnreachableError(VaultError):
    pass


def _load_bw_session(credential_path: Path) -> str:
    """Read bw session token from systemd credential file."""
    try:
        return credential_path.read_text().strip()
    except OSError as exc:
        raise VaultUnreachableError(
            f"Cannot read bw session credential at {credential_path}: {exc}"
        ) from exc


def _bw_get_item(item_name: str, session_token: str) -> dict:
    """
    Run `bw get item <item_name>` and return the parsed JSON object.
    Raises VaultUnreachableError on CLI failure.
    """
    cmd = ["bw", "get", "item", item_name, "--session", session_token]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError as exc:
        raise VaultUnreachableError("bw CLI not found in PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise VaultUnreachableError("bw CLI timed out") from exc

    if result.returncode != 0:
        # Do not log result.stderr — may contain token fragments.
        logger.warning(
            "bw get item failed",
            extra={"item": item_name, "returncode": result.returncode},
        )
        raise VaultUnreachableError(
            f"bw returned {result.returncode} for item {item_name!r}"
        )

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VaultUnreachableError("bw output was not valid JSON") from exc


def _extract_env_lines(item: dict, topic: str) -> list[str]:
    """
    Extract KEY=VALUE lines from a Bitwarden item.

    For a secure-note item the notes field contains the env content.
    For a login item the notes field is used first; fields[] are checked
    as a fallback for SCCACHE_DIR and similar config keys.
    """
    lines: list[str] = []

    notes: str = item.get("notes") or ""
    for raw_line in notes.splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#") and "=" in line:
            lines.append(line)

    # Inject SCCACHE_DIR if not already present (always needed per task spec).
    keys = {ln.split("=", 1)[0] for ln in lines}
    if "SCCACHE_DIR" not in keys:
        lines.append("SCCACHE_DIR=/home/op/.cache/sccache")

    logger.info(
        "vault: extracted env lines",
        extra={"topic": topic, "key_count": len(lines)},
    )
    return lines


def check_reachable(credential_path: Path) -> bool:
    """
    Non-intrusive vault reachability check: attempt to read the session
    credential file and run `bw status`.  Returns True if the vault is
    reachable and the session appears valid.
    """
    try:
        session_token = _load_bw_session(credential_path)
    except VaultUnreachableError:
        return False

    try:
        result = subprocess.run(
            ["bw", "status", "--session", session_token],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return False
        data = json.loads(result.stdout)
        return data.get("status") == "unlocked"
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, ValueError):
        return False


def materialise_env(
    task_id: str,
    topic: str,
    worktree_path: Path,
    credential_path: Path,
) -> Path:
    """
    Fetch secrets for <topic> from Vaultwarden and write <worktree_path>/.env.

    Returns the path to the .env file.
    Raises VaultUnreachableError if bw cannot be reached.
    """
    session_token = _load_bw_session(credential_path)

    item_name = f"mnemonic/topic/{topic}"
    item = _bw_get_item(item_name, session_token)

    env_lines = _extract_env_lines(item, topic)

    env_path = worktree_path / ".env"

    # Write atomically: tempfile + rename within same directory + dir fsync.
    dir_ = worktree_path
    dir_.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(dir_), prefix=".env-tmp-")
    try:
        os.fchmod(fd, ENV_MODE)
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(env_lines) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, str(env_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    # fsync the containing directory so the rename is durable on crash.
    dir_fd = os.open(str(dir_), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    logger.info(
        "vault: .env materialised",
        extra={"task_id": task_id, "topic": topic},
    )
    return env_path


def remove_env(env_path: Path) -> None:
    """Delete the per-worktree .env file.  No-op if already absent."""
    try:
        env_path.unlink()
        logger.info("vault: .env removed", extra={"path": str(env_path)})
    except FileNotFoundError:
        logger.debug("vault: .env not found, skipping removal: %s", env_path)
