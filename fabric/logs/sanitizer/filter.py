"""
fabric.logs.sanitizer.filter
============================
Single point of truth for log redaction.  Strips four classes of secrets from
any string before it reaches disk or Telegram.

Patterns are compiled once at module load — never per-call.

CAVEAT: Sanitizer operates per-line (str must not contain embedded newlines for
the JWK pattern to be reliable).  Callers that log multi-line pretty-printed
JSON must call ``json.dumps(obj, separators=(',', ':'))`` first.

CAVEAT: Patterns operate on the Python ``str`` layer.  Unicode-normalised
variants of ASCII characters (e.g. full-width digits) are **not** stripped.
"""

import re

# ---------------------------------------------------------------------------
# Replacement sentinel
# ---------------------------------------------------------------------------

REDACTED: str = "***REDACTED***"

# ---------------------------------------------------------------------------
# Pattern 1 — Base58 Solana keypair / pubkey
#
# Rationale: Solana public keys and private keys are base58check-encoded
# 32-byte (pubkey, ~43 chars) or 64-byte (keypair, ~87 chars) values.
# The Solana base58 alphabet deliberately EXCLUDES the four visually
# ambiguous characters: 0 (zero), O (capital-oh), I (capital-eye), l
# (lower-ell).  We anchor at 32–44 chars to catch pubkeys and short
# keypair prefixes without producing false positives on arbitrary long
# alphanumeric strings.
#
# Example match: "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
# ---------------------------------------------------------------------------

BASE58_ALPHABET = r"[1-9A-HJ-NP-Za-km-z]"

_PATTERN_BASE58 = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])"  # negative look-behind: no preceding base58 char
    r"(" + BASE58_ALPHABET + r"{32,44})"
    r"(?![1-9A-HJ-NP-Za-km-z])",  # negative look-ahead: no following base58 char
)

# ---------------------------------------------------------------------------
# Pattern 2 — JWK key fragments
#
# Rationale: JSON Web Key objects carry private key material in the "d",
# public key coordinates in "x" and "y", curve name in "crv", and key type
# in "kty".  A single-line JWK leaks the full private key when logged.
# We match JSON string field assignments for these five field names.
#
# Limitation: multi-line pretty-printed JSON will not be fully redacted
# because the pattern is anchored to a single-line match.  Callers MUST
# compact JSON before logging.
#
# Example match: "kty":"EC"  →  "kty":"***REDACTED***"
# ---------------------------------------------------------------------------

_PATTERN_JWK = re.compile(
    r'"(kty|crv|x|y|d)"\s*:\s*"([^"]*)"',
)

# ---------------------------------------------------------------------------
# Pattern 3 — API tokens
#
# Rationale: Three common prefixes indicate long-lived secrets:
#   - sk-        (OpenAI, Anthropic-style secret keys)
#   - api_       (generic API token prefix used by many services)
#   - ANTHROPIC_ (Anthropic environment variables including ANTHROPIC_API_KEY)
#
# We require at least 8 chars of payload after the prefix so that short
# dictionary words like "api_call" or "ANTHROPIC_MODEL" are NOT redacted.
# The payload must consist of non-whitespace characters ([\S]{8,}).
#
# Example matches:
#   sk-abc123XYZ456  →  ***REDACTED***
#   api_12345678     →  ***REDACTED***
#   ANTHROPIC_API_KEY=sk-ant-... →  ANTHROPIC_***REDACTED***
# ---------------------------------------------------------------------------

_PATTERN_API_TOKEN = re.compile(
    r"\b(sk-|api_|ANTHROPIC_)\S{8,}",
)

# ---------------------------------------------------------------------------
# Pattern 4 — Telegram file download URLs
#
# Rationale: Telegram Bot API file URLs contain the bot token in the path:
#   https://api.telegram.org/file/bot<TOKEN>/<file_path>
# Logging such a URL leaks the bot token.
#
# Example match:
#   https://api.telegram.org/file/bot123:ABC-xyz/photos/file_1.jpg
# ---------------------------------------------------------------------------

_PATTERN_TG_FILE_URL = re.compile(
    r"https://api\.telegram\.org/file/\S+",
)

# Public ordered list used by sanitize() — order is intentional:
# JWK before base58 so that base64url coords inside JWK aren't double-hit.
# JWK replacement keeps the field name (group 1) but redacts the value.
# The replacement string uses a single backslash before the group digit so
# that re.sub interprets \1 as a backreference to group 1.
_JWK_REPLACEMENT: str = '"\\1":"' + REDACTED + '"'

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PATTERN_JWK, _JWK_REPLACEMENT),
    (_PATTERN_BASE58, REDACTED),
    (_PATTERN_API_TOKEN, REDACTED),
    (_PATTERN_TG_FILE_URL, REDACTED),
)


def sanitize(line: str) -> str:
    """Return *line* with all recognised secret patterns replaced by ``***REDACTED***``.

    This function is idempotent: ``sanitize(sanitize(x)) == sanitize(x)``.
    It operates on a single string; callers are responsible for splitting
    multi-line log records and passing each line individually, or for
    compacting multi-line JSON before logging.

    Args:
        line: A single log line or message string.

    Returns:
        The sanitized string with secrets replaced.
    """
    result = line
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
