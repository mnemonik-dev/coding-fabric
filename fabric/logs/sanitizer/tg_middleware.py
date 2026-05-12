"""
fabric.logs.sanitizer.tg_middleware
=====================================
Thin adapter for the Telegram bot send path.

Usage::

    from fabric.logs.sanitizer.tg_middleware import sanitize_outgoing

    safe_text = sanitize_outgoing(message_text)
    await bot.send_message(chat_id=chat_id, text=safe_text)

This module deliberately has no Telegram-specific imports so it can be used
from any component without introducing coupling to the bot library.
"""

from fabric.logs.sanitizer.filter import sanitize


def sanitize_outgoing(message: str) -> str:
    """Strip secrets from an outgoing Telegram message before sending.

    Applies the same four pattern classes as :func:`~fabric.logs.sanitizer.filter.sanitize`:
    base58 Solana keys, JWK fragments, API tokens, and Telegram file URLs.

    This function is idempotent: calling it twice yields the same result as
    calling it once.

    Args:
        message: The raw outgoing message text.

    Returns:
        The sanitized message safe to send over Telegram.
    """
    return sanitize(message)
