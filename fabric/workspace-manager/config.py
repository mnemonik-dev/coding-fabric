"""
Configuration via Pydantic Settings — all values come from environment variables.
The service must bind to BIND_IP, never 0.0.0.0.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Network — must be set at deploy time (Ansible-templated from OpenTofu output)
    bind_ip: str = "127.0.0.1"
    bind_port: int = 8080

    # Workspace directories
    worktrees_root: Path = Path.home() / "code" / "mnemonic-workspaces"
    repos_root: Path = Path.home() / "code" / "mnemonic-masters"

    # State persistence
    state_file: Path = Path.home() / ".fabric" / "workspace-manager" / "state.json"

    # Capacity cap (tech-spec D10)
    capacity_total: int = 10

    # sccache shared cache directory
    sccache_dir: Path = Path.home() / ".cache" / "sccache"

    # Bitwarden CLI session token path (loaded via systemd LoadCredential)
    bw_session_credential: Path = Path("/run/credentials/workspace-manager.service/bw-session")

    # Logging
    log_file: Path = Path.home() / ".fabric" / "logs" / "sanitized" / "workspace-manager.log"
    log_level: str = "INFO"

    @field_validator("bind_ip")
    @classmethod
    def reject_wildcard(cls, v: str) -> str:
        if v in ("0.0.0.0", "::"):
            raise ValueError(
                "bind_ip must not be a wildcard address; "
                "set BIND_IP to the tailnet IP (e.g. 100.x.x.x)"
            )
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
