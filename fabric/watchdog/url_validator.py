"""
fabric.watchdog.url_validator
==============================
URL allowlist enforcement for outbound HTTP calls made by watchdog checks.

Security policy:
- Only http:// and https:// schemes are permitted (file://, ftp://, etc. blocked).
- Loopback and link-local addresses are permitted for local service checks
  (Kaneo, MCP, local Solana devnet proxy).
- The following public hostnames are explicitly allowlisted for devnet/testnet
  use; mainnet RPC endpoints (mainnet-beta.solana.com, etc.) are rejected to
  prevent accidental interaction with production infrastructure.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Allowlisted public hostnames for devnet/testnet external services.
_ALLOWED_PUBLIC_HOSTS: frozenset[str] = frozenset([
    "api.devnet.solana.com",
    "devnet.irys.xyz",
    "api.telegram.org",
])

# Regex for a valid UUID alert_id (loose — just to sanity-check input)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_url(url: str, context: str = "") -> None:
    """Raise ValueError if *url* does not pass the watchdog allowlist policy.

    Args:
        url: The URL to validate.
        context: Optional label used in error messages (e.g. "solana_rpc_url").

    Raises:
        ValueError: If the URL scheme is not http/https, or if it resolves to
            a disallowed public host/IP.
    """
    prefix = f"{context}: " if context else ""
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"{prefix}URL scheme {parsed.scheme!r} is not allowed "
            "(only http and https are permitted)"
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"{prefix}URL has no hostname: {url!r}")

    # Well-known local hostnames are always permitted.
    if hostname in ("localhost", "127.0.0.1", "::1"):
        return

    # Check if the hostname is an IP address literal.
    try:
        addr = ipaddress.ip_address(hostname)
        # Loopback, link-local, and private (RFC 1918 + RFC 6598 etc.) are allowed
        # for local and Tailscale services.  We also accept is_private and
        # networks that ipaddress considers shared (100.64.0.0/10 = CGNAT / Tailscale).
        _PRIVATE_NETS = [
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
            ipaddress.ip_network("100.64.0.0/10"),  # CGNAT / Tailscale
            ipaddress.ip_network("fd00::/8"),
        ]
        if addr.is_loopback or addr.is_link_local or addr.is_private:
            return
        if any(addr in net for net in _PRIVATE_NETS):
            return
        # Remaining public IP — not allowed; use an allowlisted hostname.
        raise ValueError(
            f"{prefix}direct public IP address {hostname!r} is not allowed; "
            "use an allowlisted hostname"
        )
    except ValueError as ip_exc:
        # Not a valid IP address literal — treat as hostname.
        if "not allowed" in str(ip_exc) or "permitted" in str(ip_exc):
            raise

    # Hostname allowlist check.
    if hostname not in _ALLOWED_PUBLIC_HOSTS:
        raise ValueError(
            f"{prefix}hostname {hostname!r} is not in the watchdog URL allowlist. "
            f"Allowed public hosts: {sorted(_ALLOWED_PUBLIC_HOSTS)}"
        )
