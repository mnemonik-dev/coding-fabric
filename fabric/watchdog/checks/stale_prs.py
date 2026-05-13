"""
fabric.watchdog.checks.stale_prs
===================================
Alert class: stale_prs

Queries the GitHub API for open pull requests older than stale_pr_days
(default 7) across all configured repos.

Config keys consumed:
    github_repos     (list[str]) — list of "owner/repo" strings
    github_token     (str)       — GitHub personal access token (Bearer)
    stale_pr_days    (int)       — age threshold in days, default 7
    gh_api_timeout   (float)     — HTTP timeout in seconds, default 15
"""

from __future__ import annotations

import json as _json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Any

from fabric.watchdog.models import Alert, Severity

logger = logging.getLogger(__name__)

_DEFAULT_DAYS = 7
_DEFAULT_TIMEOUT = 15.0
_GH_API = "https://api.github.com"


def check(config: dict[str, Any]) -> Alert | None:
    """Return an Alert if stale open PRs are found, else None."""
    try:
        return _check(config)
    except Exception as exc:
        logger.error("stale_prs check failed: %s", exc)
        return None


def _gh_get(url: str, token: str, timeout: float) -> list[dict]:
    """Fetch a paginated GitHub API list endpoint, returning all items."""
    items: list[dict] = []
    next_url: str | None = url
    while next_url:
        req = urllib.request.Request(next_url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = _json.loads(resp.read())
                items.extend(data if isinstance(data, list) else [data])
                # Handle pagination via Link header
                link_header = resp.headers.get("Link", "")
                next_url = _parse_next_link(link_header)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
            logger.warning("stale_prs: GitHub request failed for %s: %s", next_url, exc)
            break
    return items


def _parse_next_link(link_header: str) -> str | None:
    """Extract the 'next' URL from a GitHub Link header."""
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            if url_part.startswith("<") and url_part.endswith(">"):
                return url_part[1:-1]
    return None


def _check(config: dict[str, Any]) -> Alert | None:
    repos = config.get("github_repos", [])
    token = config.get("github_token", "")
    stale_days = int(config.get("stale_pr_days", _DEFAULT_DAYS))
    timeout = float(config.get("gh_api_timeout", _DEFAULT_TIMEOUT))

    if not repos:
        logger.debug("github_repos not configured; skipping stale_prs check")
        return None

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    stale = []

    for repo in repos:
        url = f"{_GH_API}/repos/{repo}/pulls?state=open&per_page=100"
        prs = _gh_get(url, token, timeout)
        for pr in prs:
            created_at_str = pr.get("created_at", "")
            if not created_at_str:
                continue
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if created_at < cutoff:
                age_days = (datetime.now(timezone.utc) - created_at).days
                stale.append(
                    f"{repo}#{pr.get('number')} '{pr.get('title', '')[:40]}' ({age_days}d old)"
                )

    if not stale:
        return None

    evidence = f"{len(stale)} stale open PR(s) >{stale_days} days: {'; '.join(stale[:5])}"
    if len(stale) > 5:
        evidence += f" ... and {len(stale) - 5} more"
    logger.warning("stale_prs: %s", evidence)
    return Alert(
        alert_class="stale_prs",
        severity=Severity.WARNING,
        evidence=evidence,
        extra={"stale_prs": stale, "threshold_days": stale_days},
    )
