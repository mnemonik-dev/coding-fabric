"""
fabric.logs.sanitizer.filter
============================
Single point of truth for log redaction.  Strips four classes of secrets from
any string before it reaches disk or Telegram.

Patterns are compiled once at module load — never per-call.

CAVEAT: Sanitizer operates per-line.  The JWK pattern tolerates whitespace
(including newlines) between the key, colon, and value; only the value
contents are bounded to non-quote characters.  Callers that log multi-line
pretty-printed JSON should still compact with
``json.dumps(obj, separators=(',', ':'))`` for maximum reliability.

CAVEAT: Patterns operate on the Python ``str`` layer.  Unicode-normalised
variants of ASCII characters (e.g. full-width digits) are **not** stripped.
"""

import re

__all__ = ["sanitize", "REDACTED"]

# ---------------------------------------------------------------------------
# Replacement sentinel
# ---------------------------------------------------------------------------

REDACTED: str = "***REDACTED***"

# ---------------------------------------------------------------------------
# Pattern 1 — Base58 Solana keypair / pubkey
#
# Rationale: Solana public keys and private keys are base58check-encoded
# 32-byte (pubkey, 43–44 chars) or 64-byte (keypair, 87–88 chars) values.
# The Solana base58 alphabet deliberately EXCLUDES the four visually
# ambiguous characters: 0 (zero), O (capital-oh), I (capital-eye), l
# (lower-ell).  We match runs of 32+ contiguous base58 chars bounded by
# non-base58 chars (negative look-behind / look-ahead) so that:
#   - 43-char pubkeys are caught
#   - 87–88-char keypairs are caught
#   - A pubkey padded by an adjacent base58 char is still caught in full
#
# The payload is capped at 256 chars to limit ReDoS exposure on adversarial
# input lines.
#
# Example match: "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
# ---------------------------------------------------------------------------

_BASE58_ALPHABET = r"[1-9A-HJ-NP-Za-km-z]"

# Keep a public alias for consumers that built regex on top of this constant.
# Prefer the private form for new code.
BASE58_ALPHABET = _BASE58_ALPHABET

_PATTERN_BASE58 = re.compile(
    r"(?<![1-9A-HJ-NP-Za-km-z])"  # no preceding base58 char
    r"([1-9A-HJ-NP-Za-km-z]{32,256})"
    r"(?![1-9A-HJ-NP-Za-km-z])",  # no following base58 char
)

# ---------------------------------------------------------------------------
# Pattern 2 — JWK key fragments
#
# Rationale: JSON Web Key objects carry private key material in the "d",
# public key coordinates in "x" and "y", curve name in "crv", and key type
# in "kty".  A single-line JWK leaks the full private key when logged.
# We match JSON string field assignments for these five field names.
#
# The value capture handles JSON escape sequences (e.g. \") so that a JWK
# value containing an escaped quote does not stop matching prematurely.
#
# Limitation: multi-line pretty-printed JSON is tolerated because the value
# pattern [^"\\] accepts newlines; behaviour is correct as long as field name
# and value appear in the same log record string.  Callers should still
# compact JSON before logging for reliability.
#
# Example match: "kty":"EC"  ->  "kty":"***REDACTED***"
# ---------------------------------------------------------------------------

_PATTERN_JWK = re.compile(
    r'"(kty|crv|x|y|d)"\s*:\s*"((?:[^"\\]|\\.)*)"',
)

# ---------------------------------------------------------------------------
# Pattern 3a — sk- / api_ API tokens
#
# Rationale: Two common prefixes indicate long-lived secrets:
#   - sk-   (OpenAI, Anthropic-style secret keys)
#   - api_  (generic API token prefix)
#
# To reduce false positives (SKUs, endpoint identifiers, short version tags),
# the payload must:
#   1. Consist of [A-Za-z0-9_-] — excludes punctuation/structural chars
#   2. Be at least 16 chars
#   3. Contain at least one digit AND at least one letter
#   4. Be capped at 256 chars to limit ReDoS exposure
#
# Not redacted:
#   - api_v2_endpoint   (10 chars, below 16-char threshold)
#   - SKU-12345         (prefix SK- is uppercase, word boundary fails sk-)
#
# Example matches:
#   sk-abc123XYZ456defghij  ->  ***REDACTED***
#   api_12345678abcdefgh    ->  ***REDACTED***
# ---------------------------------------------------------------------------

_PATTERN_SK_API_TOKEN = re.compile(
    r"\b(sk-|api_)"
    r"(?=[A-Za-z0-9_-]*\d)"        # must contain at least one digit
    r"(?=[A-Za-z0-9_-]*[A-Za-z])"  # must contain at least one letter
    r"[A-Za-z0-9_-]{16,256}",
)

# ---------------------------------------------------------------------------
# Pattern 3b — ANTHROPIC_ environment variables
#
# Rationale: ANTHROPIC_API_KEY and similar env vars carry long-lived secrets.
# We match any ANTHROPIC_-prefixed non-whitespace sequence of 8+ chars that
# contains at least one digit (to exclude bare variable NAMES like
# ANTHROPIC_MODEL_NAME, ANTHROPIC_VERSION which are all-alpha).
#
# The payload uses \S (any non-whitespace) rather than [A-Za-z0-9_-] so that
# an assignment like ANTHROPIC_API_KEY=sk-ant-... is caught in full (the
# =, hyphen, and alphanumeric chars are all consumed).
#
# Not redacted:
#   - ANTHROPIC_MODEL_NAME  (no digit anywhere in payload)
#   - ANTHROPIC_VERSION     (no digit in "VERSION" alone)
#
# Redacted (acceptable over-redaction):
#   - ANTHROPIC_MODEL=claude-opus-4-5  (assignment contains digit)
# ---------------------------------------------------------------------------

_PATTERN_ANTHROPIC_TOKEN = re.compile(
    r"\bANTHROPIC_"
    r"(?=\S*\d)"   # must contain at least one digit in the payload
    r"\S{8,256}",
)

# ---------------------------------------------------------------------------
# Pattern 4 — Telegram file download URLs
#
# Rationale: Telegram Bot API file URLs contain the bot token in the path:
#   https://api.telegram.org/file/bot<TOKEN>/<file_path>
# Logging such a URL leaks the bot token.
#
# The URL character class stops at whitespace and at structural chars used in
# JSON, HTML, and Markdown (", ', <, >) to avoid consuming surrounding
# context and producing malformed output.
#
# Example match:
#   https://api.telegram.org/file/bot123:ABC-xyz/photos/file_1.jpg
# ---------------------------------------------------------------------------

_PATTERN_TG_FILE_URL = re.compile(
    r"https://api\.telegram\.org/file/[^\s\"'<>]+",
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
    (_PATTERN_SK_API_TOKEN, REDACTED),
    (_PATTERN_ANTHROPIC_TOKEN, REDACTED),
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
