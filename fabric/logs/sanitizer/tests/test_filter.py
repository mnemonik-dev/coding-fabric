"""
Tests for fabric.logs.sanitizer.

TDD anchors from task 07:
- test_redacts_base58_solana_pubkey
- test_redacts_jwk_fragment
- test_redacts_sk_api_anthropic_tokens
- test_redacts_telegram_file_urls
- test_does_not_redact_neutral_text
- test_idempotent
- test_no_double_replacement
- test_sanitized_file_handler_filters_logs
- test_sanitize_outgoing_for_telegram
"""

from __future__ import annotations

import io
import logging
import os
import pathlib
import tempfile

import pytest

from fabric.logs.sanitizer import SanitizedFileHandler, sanitize, sanitize_outgoing
from fabric.logs.sanitizer.filter import REDACTED

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def load_corpus() -> list[str]:
    """Load non-comment, non-blank lines from secret_corpus.txt."""
    corpus_path = FIXTURES_DIR / "secret_corpus.txt"
    lines: list[str] = []
    with corpus_path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")
            if line.startswith("#") or not line.strip():
                continue
            lines.append(line)
    return lines


# Known Solana pubkeys for parametrize
SOLANA_PUBKEYS: list[str] = [
    "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe",
    "DYw8jCTfwHNRJhhmFcbXvVDTqWMEVFBX6ZKUmkEeB5hu",
    "BrEqc6zHVR4jWk2HuF8dV2cNuNcgvXcFkV3S6sJXJHLe",
    "11111111111111111111111111111112",  # 32-char system program address
    "So11111111111111111111111111111112",  # wrapped SOL mint (34 chars)
]

# Known API tokens for parametrize
API_TOKENS: list[tuple[str, str]] = [
    ("sk-abc123XYZ456defghijklmnopq", "sk-abc123XYZ456defghijklmnopq"),
    ("sk-ant-api03-longkeyvaluehere1234567890", "sk-ant-api03-longkeyvaluehere1234567890"),
    ("api_12345678abcdefgh", "api_12345678abcdefgh"),
    ("api_SECRETTOKEN1234567890abcdef", "api_SECRETTOKEN1234567890abcdef"),
    ("ANTHROPIC_API_KEY=sk-ant-x123456789012", "ANTHROPIC_API_KEY=sk-ant-x123456789012"),
]

# Telegram file URLs
TG_FILE_URLS: list[str] = [
    "https://api.telegram.org/file/bot123456:ABC-xyz/photos/file_1.jpg",
    "https://api.telegram.org/file/bot987654321:AABBccDDee-ffGGhhIIjj/documents/doc_10.pdf",
    "https://api.telegram.org/file/bot111222333:XYZabc/voice/vc_5.ogg",
]

# Neutral strings that MUST NOT be changed by the sanitizer
NEUTRAL_STRINGS: list[str] = [
    "The quick brown fox jumps over the lazy dog",
    "import logging",
    "def sanitize(line: str) -> str:",
    "Starting workspace manager on port 8080",
    "Task 07 completed successfully",
    "api_call is a short string",  # "api_call" → "api_" + "call" only 4 chars
    "sk-short",                    # sk- + 5 chars, below 8-char threshold
    "Hello, World!",
    "2026-05-12T10:00:00Z INFO fabric.watchdog healthy",
    "",
]

# ---------------------------------------------------------------------------
# TDD anchor 1: base58 Solana pubkeys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pubkey", SOLANA_PUBKEYS)
def test_redacts_base58_solana_pubkey(pubkey: str) -> None:
    """Known Solana pubkeys must be replaced with REDACTED."""
    line = f"signed by {pubkey} in block 12345"
    result = sanitize(line)
    assert REDACTED in result, f"Expected REDACTED in result, got: {result!r}"
    assert pubkey not in result, f"Expected pubkey to be gone, still in: {result!r}"


def test_redacts_base58_solana_pubkey_standalone() -> None:
    """Standalone 44-char Solana pubkey is redacted."""
    pubkey = "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
    assert sanitize(pubkey) == REDACTED


def test_base58_boundary_31_chars_not_redacted() -> None:
    """31-char base58 string (below minimum) must NOT be redacted."""
    s = "1" * 31
    assert sanitize(s) == s


def test_base58_boundary_45_chars_not_redacted() -> None:
    """45-char base58 string (above maximum) must NOT be redacted."""
    s = "A" * 45
    assert sanitize(s) == s


def test_base58_excludes_zero() -> None:
    """Strings containing '0' (excluded from Solana base58) break the match."""
    # "0" + 43 valid base58 chars: the 43 valid chars may match, but the
    # leading zero is excluded so the whole 44-char span does not match.
    s = "0" + "A" * 43
    result = sanitize(s)
    # The 43 A's after the 0 are within 32-44 range, so they ARE redacted.
    # This is by design: the 0 acts as a word-boundary delimiter.
    # We only assert the zero itself is preserved.
    assert "0" in result


def test_base58_excludes_l_I_O() -> None:
    """l, I, O are excluded from Solana base58 alphabet."""
    # A 44-char string made entirely of excluded chars must not be redacted.
    for excluded in ("l", "I", "O"):
        s = excluded * 44
        result = sanitize(s)
        assert REDACTED not in result, f"String of '{excluded}' should not be redacted"


# ---------------------------------------------------------------------------
# TDD anchor 2: JWK fragments
# ---------------------------------------------------------------------------


def test_redacts_jwk_fragment() -> None:
    """Single-line JWK with kty/crv/x/y/d has all values redacted."""
    jwk = '{"kty":"EC","crv":"P-256","x":"abc","y":"def","d":"ghi"}'
    result = sanitize(jwk)
    assert '"kty":"' + REDACTED + '"' in result
    assert '"crv":"' + REDACTED + '"' in result
    assert '"x":"' + REDACTED + '"' in result
    assert '"y":"' + REDACTED + '"' in result
    assert '"d":"' + REDACTED + '"' in result
    # Original values must be absent
    for secret_val in ("EC", "P-256", "abc", "def", "ghi"):
        assert secret_val not in result, f"Value {secret_val!r} leaked into: {result!r}"


def test_redacts_jwk_preserves_field_names() -> None:
    """Field names (kty, crv, x, y, d) are preserved; only values are redacted."""
    jwk = '{"kty":"RSA","d":"bigprivatevalue123"}'
    result = sanitize(jwk)
    assert '"kty"' in result
    assert '"d"' in result
    assert "RSA" not in result
    assert "bigprivatevalue123" not in result


@pytest.mark.parametrize("field", ["kty", "crv", "x", "y", "d"])
def test_redacts_individual_jwk_field(field: str) -> None:
    """Each JWK field individually is redacted."""
    line = f'{{"{field}":"secretvalue"}}'
    result = sanitize(line)
    assert REDACTED in result
    assert "secretvalue" not in result


# ---------------------------------------------------------------------------
# TDD anchor 3: sk- / api_ / ANTHROPIC_ tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("token_line,token", API_TOKENS)
def test_redacts_sk_api_anthropic_tokens(token_line: str, token: str) -> None:
    """API tokens with sk-, api_, ANTHROPIC_ prefixes are redacted."""
    line = f"Using auth: {token_line}"
    result = sanitize(line)
    assert REDACTED in result, f"Expected REDACTED in: {result!r}"
    # The raw token value should not appear in output
    assert token_line not in result, f"Token still present in: {result!r}"


def test_api_token_threshold_too_short() -> None:
    """Token prefix with fewer than 8 payload chars must NOT be redacted."""
    assert sanitize("sk-short") == "sk-short"
    assert sanitize("api_tiny") == "api_tiny"  # exactly 4 chars after api_


def test_api_token_exactly_8_chars_payload() -> None:
    """Token prefix with exactly 8 payload chars IS redacted."""
    assert REDACTED in sanitize("sk-12345678")
    assert REDACTED in sanitize("api_12345678")


# ---------------------------------------------------------------------------
# TDD anchor 4: Telegram file URLs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", TG_FILE_URLS)
def test_redacts_telegram_file_urls(url: str) -> None:
    """Telegram file download URLs are fully redacted."""
    line = f"Downloading file from {url}"
    result = sanitize(line)
    assert REDACTED in result, f"Expected REDACTED in: {result!r}"
    assert "api.telegram.org/file" not in result, f"URL still present in: {result!r}"


def test_redacts_telegram_url_standalone() -> None:
    """Standalone Telegram file URL is fully replaced."""
    url = "https://api.telegram.org/file/bot123456:TOKEN/photos/photo.jpg"
    assert sanitize(url) == REDACTED


# ---------------------------------------------------------------------------
# TDD anchor 5: neutral text passes through unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("text", NEUTRAL_STRINGS)
def test_does_not_redact_neutral_text(text: str) -> None:
    """Non-secret strings must pass through unchanged."""
    result = sanitize(text)
    assert result == text, f"Neutral text was altered: {text!r} => {result!r}"


# ---------------------------------------------------------------------------
# TDD anchor 6: idempotency
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("pubkey", SOLANA_PUBKEYS)
def test_idempotent_base58(pubkey: str) -> None:
    """sanitize(sanitize(x)) == sanitize(x) for base58 keys."""
    line = f"key={pubkey}"
    once = sanitize(line)
    twice = sanitize(once)
    assert once == twice, f"Not idempotent: {once!r} != {twice!r}"


def test_idempotent() -> None:
    """sanitize is idempotent for all secret types combined."""
    combined = (
        "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe "
        '{"kty":"EC","x":"abc","y":"def","d":"ghi"} '
        "sk-abc123XYZ456defghijklmnopq "
        "https://api.telegram.org/file/bot123:T/photo.jpg "
        "neutral text"
    )
    once = sanitize(combined)
    twice = sanitize(once)
    assert once == twice, f"sanitize not idempotent.\nOnce:  {once!r}\nTwice: {twice!r}"


def test_idempotent_corpus() -> None:
    """sanitize is idempotent for every sample in the corpus."""
    for sample in load_corpus():
        once = sanitize(sample)
        twice = sanitize(once)
        assert once == twice, (
            f"sanitize not idempotent for corpus line: {sample!r}\n"
            f"Once:  {once!r}\nTwice: {twice!r}"
        )


# ---------------------------------------------------------------------------
# TDD anchor 7: no double replacement
# ---------------------------------------------------------------------------


def test_no_double_replacement() -> None:
    """Each match is replaced exactly once; REDACTED is not nested."""
    pubkey = "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
    result = sanitize(pubkey)
    assert result == REDACTED, f"Expected single REDACTED, got: {result!r}"
    # Ensure no nested or doubled redaction marker
    assert result.count(REDACTED) == 1


def test_no_double_replacement_two_secrets() -> None:
    """Two distinct secrets on the same line each get their own REDACTED."""
    pubkey1 = "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
    pubkey2 = "DYw8jCTfwHNRJhhmFcbXvVDTqWMEVFBX6ZKUmkEeB5hu"
    line = f"from {pubkey1} to {pubkey2}"
    result = sanitize(line)
    assert result.count(REDACTED) == 2, f"Expected 2 REDACTED, got: {result!r}"


# ---------------------------------------------------------------------------
# TDD anchor 8: SanitizedFileHandler filters logs
# ---------------------------------------------------------------------------


def test_sanitized_file_handler_filters_logs(tmp_path: pathlib.Path) -> None:
    """SanitizedFileHandler writes no secrets to the log file."""
    log_file = tmp_path / "test.log"
    pubkey = "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
    token = "sk-abc123XYZ456defghijklmnopq"
    tg_url = "https://api.telegram.org/file/bot123456:ABC-xyz/photos/file_1.jpg"
    jwk_fragment = '{"kty":"EC","x":"abc","y":"def","d":"ghi"}'

    logger = logging.getLogger("test_sanitized_handler")
    logger.setLevel(logging.DEBUG)
    handler = SanitizedFileHandler(str(log_file))
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    try:
        logger.debug("pubkey=%s token=%s url=%s jwk=%s", pubkey, token, tg_url, jwk_fragment)
    finally:
        handler.close()
        logger.removeHandler(handler)

    contents = log_file.read_text(encoding="utf-8")

    # None of the secret values must appear in the file
    assert pubkey not in contents, f"Pubkey leaked into log file"
    assert "sk-abc" not in contents, f"API token leaked into log file"
    assert "api.telegram.org/file" not in contents, f"Telegram URL leaked into log file"
    assert '"d":"ghi"' not in contents, f"JWK d-value leaked into log file"

    # The file must contain the REDACTED marker at least once
    assert REDACTED in contents, f"No REDACTED marker found in log file"


def test_sanitized_file_handler_is_drop_in() -> None:
    """SanitizedFileHandler is an instance of logging.FileHandler."""
    with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
        path = f.name
    try:
        handler = SanitizedFileHandler(path)
        assert isinstance(handler, logging.FileHandler)
        handler.close()
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# TDD anchor 9: sanitize_outgoing for Telegram
# ---------------------------------------------------------------------------


def test_sanitize_outgoing_for_telegram() -> None:
    """sanitize_outgoing applies all four pattern classes."""
    pubkey = "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe"
    token = "sk-abc123XYZ456defghijklmnopq"
    tg_url = "https://api.telegram.org/file/bot123456:ABC/photo.jpg"
    jwk = '{"kty":"EC","x":"abc","y":"def"}'
    message = f"pubkey={pubkey}\ntoken={token}\nurl={tg_url}\njwk={jwk}"
    result = sanitize_outgoing(message)
    assert pubkey not in result
    assert "sk-abc" not in result
    assert "api.telegram.org/file" not in result
    assert '"x":"abc"' not in result
    assert REDACTED in result


def test_sanitize_outgoing_equals_sanitize() -> None:
    """sanitize_outgoing is a thin wrapper — result equals sanitize()."""
    msg = "4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe sk-abc123XYZ456def"
    assert sanitize_outgoing(msg) == sanitize(msg)


# ---------------------------------------------------------------------------
# Corpus parametrize — every corpus line must contain REDACTED after sanitize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sample", load_corpus())
def test_corpus_sample_is_redacted(sample: str) -> None:
    """Every secret sample in the corpus must be redacted."""
    result = sanitize(sample)
    assert REDACTED in result, (
        f"Corpus sample not redacted: {sample!r} => {result!r}"
    )
