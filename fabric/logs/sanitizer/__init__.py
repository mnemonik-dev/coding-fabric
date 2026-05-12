"""
fabric.logs.sanitizer
======================
Single point of trust for log redaction across every fabric component.

Public API::

    from fabric.logs.sanitizer import sanitize, SanitizedFileHandler, sanitize_outgoing

- :func:`sanitize` — redact secrets from any string
- :class:`SanitizedFileHandler` — drop-in ``logging.FileHandler`` that auto-sanitizes
- :func:`sanitize_outgoing` — sanitize a Telegram outgoing message
"""

from fabric.logs.sanitizer.filter import sanitize
from fabric.logs.sanitizer.handler import SanitizedFileHandler
from fabric.logs.sanitizer.tg_middleware import sanitize_outgoing

__all__ = ["sanitize", "SanitizedFileHandler", "sanitize_outgoing"]
