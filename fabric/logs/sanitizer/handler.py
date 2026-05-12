"""
fabric.logs.sanitizer.handler
==============================
A drop-in replacement for ``logging.FileHandler`` that passes every log
record through ``sanitize()`` before writing it to disk.

Usage::

    import logging
    from fabric.logs.sanitizer.handler import SanitizedFileHandler

    handler = SanitizedFileHandler("/var/log/fabric/agent.log")
    logging.getLogger().addHandler(handler)

Every record emitted through this handler has its formatted string sanitized
before any bytes reach the log file.
"""

import logging

from fabric.logs.sanitizer.filter import sanitize


class SanitizedFileHandler(logging.FileHandler):
    """A ``logging.FileHandler`` subclass that redacts secrets from every record.

    All four secret pattern classes (base58 Solana keys, JWK fragments,
    API tokens, Telegram file URLs) are stripped via :func:`sanitize` before
    the record is written to disk.

    This class is a drop-in replacement: it accepts the same constructor
    arguments as :class:`logging.FileHandler`.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Format and emit a record, sanitizing the formatted message first.

        The original record is not mutated; we create a copy with the
        sanitized message to avoid side-effects for other handlers that may
        process the same record.

        Args:
            record: The log record to emit.
        """
        try:
            formatted = self.format(record)
            sanitized = sanitize(formatted)
            # Write directly to the stream, bypassing the parent emit()
            # to avoid re-formatting.
            stream = self.stream
            stream.write(sanitized + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)
