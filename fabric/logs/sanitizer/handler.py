"""
fabric.logs.sanitizer.handler
==============================
Drop-in replacements for ``logging.FileHandler`` and
``logging.StreamHandler`` that pass every log record through ``sanitize()``
before writing it to disk or a stream.

Usage::

    import logging
    from fabric.logs.sanitizer.handler import SanitizedFileHandler, SanitizedStreamHandler

    # File handler (secrets stripped from disk)
    handler = SanitizedFileHandler("/var/log/fabric/agent.log")
    logging.getLogger().addHandler(handler)

    # Stream handler (secrets stripped from stderr / stdout / journal)
    stderr_handler = SanitizedStreamHandler()
    logging.getLogger().addHandler(stderr_handler)

Every record emitted through these handlers has its formatted string
sanitized before any bytes reach the destination.
"""

import logging
import sys

from fabric.logs.sanitizer.filter import sanitize


class SanitizedFileHandler(logging.FileHandler):
    """A ``logging.FileHandler`` subclass that redacts secrets from every record.

    All four secret pattern classes (base58 Solana keys, JWK fragments,
    API tokens, Telegram file URLs) are stripped via :func:`sanitize` before
    the record is written to disk.

    This class is a drop-in replacement: it accepts the same constructor
    arguments as :class:`logging.FileHandler`, including ``delay=True``.

    Note: ``logging.Formatter.format()`` may set ``record.exc_text`` as a
    side-effect when the record carries exception info.  This is identical
    to the behaviour of the stdlib ``StreamHandler``.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Format and emit a record, sanitizing the formatted message first.

        Delegates to the parent ``FileHandler.emit()`` after replacing the
        pre-formatted message on a temporary copy, so that ``delay=True``
        stream-open logic and lock acquisition are handled by the parent.

        Args:
            record: The log record to emit.
        """
        try:
            formatted = self.format(record)
            sanitized = sanitize(formatted)
            # Write via parent stream, handling delay=True (stream=None) and
            # the acquire/release lock the same way FileHandler.emit() does.
            if self.stream is None:
                self.stream = self._open()
            self.acquire()
            try:
                self.stream.write(sanitized + self.terminator)
                self.flush()
            finally:
                self.release()
        except (OSError, ValueError):
            self.handleError(record)


class SanitizedStreamHandler(logging.StreamHandler):
    """A ``logging.StreamHandler`` subclass that redacts secrets from every record.

    Suitable for stderr, stdout, and journal output.  Attach this handler
    instead of a plain ``StreamHandler`` to ensure all output paths are
    sanitized.

    Usage::

        import logging, sys
        from fabric.logs.sanitizer.handler import SanitizedStreamHandler

        handler = SanitizedStreamHandler(sys.stderr)
        logging.getLogger().addHandler(handler)
    """

    def __init__(self, stream=None) -> None:
        super().__init__(stream=stream if stream is not None else sys.stderr)

    def emit(self, record: logging.LogRecord) -> None:
        """Format, sanitize, then emit to the stream.

        Args:
            record: The log record to emit.
        """
        try:
            formatted = self.format(record)
            sanitized = sanitize(formatted)
            stream = self.stream
            self.acquire()
            try:
                stream.write(sanitized + self.terminator)
                self.flush()
            finally:
                self.release()
        except (OSError, ValueError):
            self.handleError(record)
