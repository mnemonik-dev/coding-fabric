# fabric/logs/sanitizer

Single point of trust for log redaction across every fabric component.
Strips four classes of secrets from any string before it reaches disk or Telegram.

## Patterns

### 1. Base58 Solana keys (32–44 chars)

**Rationale:** Solana public keys and wallet addresses are base58-encoded 32-byte values
(~43–44 characters).  The Solana base58 alphabet deliberately excludes the four visually
ambiguous characters: `0` (zero), `O` (capital-oh), `I` (capital-eye), and `l` (lower-ell).
The pattern is length-anchored to 32–44 characters to avoid false positives on long
alphanumeric strings in general prose.

**Regex anchor:** `(?<![base58])([base58]{32,44})(?![base58])`

**False-positive risk:** Any 32–44 character base58 string will be redacted, including
non-secret identifiers that happen to use only base58 characters.  This is a deliberate
conservative choice for a security-critical path.

**Example:**
```
Input:  signed by 4Nd1mBQtrMJVYVfKf2PX98fDUiZmJpmEJBYYLiMF1TLe
Output: signed by ***REDACTED***
```

### 2. JWK fragments

**Rationale:** JSON Web Key objects carry private key material in the `"d"` field,
public key coordinates in `"x"` and `"y"`, curve name in `"crv"`, and key type in `"kty"`.
Logging a single-line JWK leaks the full private key.

**Pattern:** Matches `"field":"value"` for fields `kty`, `crv`, `x`, `y`, `d`.
Field names are preserved in the output; only values are redacted.

**Limitation:** The sanitizer operates per-line.  Multi-line pretty-printed JSON will
NOT be fully redacted.  Callers MUST compact JSON before logging:

```python
import json
# Bad:  logger.debug(json.dumps(jwk_obj, indent=2))
# Good: logger.debug(json.dumps(jwk_obj, separators=(',', ':')))
```

**Example:**
```
Input:  {"kty":"EC","crv":"P-256","x":"abc","y":"def","d":"ghi"}
Output: {"kty":"***REDACTED***","crv":"***REDACTED***","x":"***REDACTED***","y":"***REDACTED***","d":"***REDACTED***"}
```

### 3. API tokens (sk- / api_ / ANTHROPIC_ prefixes)

**Rationale:** Three common prefixes indicate long-lived secret credentials.
A minimum payload length of 8 characters prevents redacting short identifiers like
`api_call` or `sk-short`.

**Note:** `ANTHROPIC_*` environment variable names followed by a value will be redacted
if the combined payload is 8+ characters.  This is intentional: any `ANTHROPIC_` env var
with a long value is a potential key leak.

**Example:**
```
Input:  auth=sk-abc123XYZ456defghijklmnopq
Output: auth=***REDACTED***
```

### 4. Telegram file download URLs

**Rationale:** Telegram Bot API file URLs embed the bot token in the path:
`https://api.telegram.org/file/bot<TOKEN>/<file_path>`.  Logging such a URL leaks
the bot token, granting full bot control to anyone who reads the log.

**Example:**
```
Input:  Downloading https://api.telegram.org/file/bot123456:ABC-xyz/photos/file_1.jpg
Output: Downloading ***REDACTED***
```

## Usage

### In any Python module

```python
from fabric.logs.sanitizer import sanitize

safe_line = sanitize(raw_log_line)
```

### As a logging handler (drop-in for FileHandler)

```python
import logging
from fabric.logs.sanitizer import SanitizedFileHandler

handler = SanitizedFileHandler("/var/log/fabric/agent.log")
logging.getLogger("fabric").addHandler(handler)
```

Every record written through `SanitizedFileHandler` is automatically sanitized
before reaching the file.  It accepts the same constructor arguments as
`logging.FileHandler`.

### For outgoing Telegram messages

```python
from fabric.logs.sanitizer import sanitize_outgoing

safe_text = sanitize_outgoing(user_facing_message)
await bot.send_message(chat_id=chat_id, text=safe_text)
```

## Caveats

- **Per-line operation:** The sanitizer processes one string at a time.  Multi-line
  log records or pretty-printed JSON must be compacted before sanitization.
- **Unicode normalization:** Patterns operate on Python `str` (Unicode code points).
  Full-width or homoglyph variants of ASCII characters are not matched.
- **JWK multi-line:** A JSON Web Key spread across multiple lines will only be
  partially redacted (the lines containing `"x":`, `"d":` etc. individually).
  Always log JWK objects as compact single-line JSON.

## Running tests

```bash
cd fabric/logs/sanitizer
pytest tests/ -v
```

Requires Python 3.12+ and `pytest>=8.0` (no other dependencies).
