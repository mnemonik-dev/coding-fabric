#!/usr/bin/env python3
"""Brute-force delete all forum topics in a Telegram supergroup.

Bot API does not expose listForumTopics, so we iterate thread_ids over a
range and call deleteForumTopic for each. Telegram returns 200 for an
existing topic and 400/404 for absent — both are fine, we ignore errors.

After this script finishes the chat has zero topics (the General/main
topic, thread_id=1, is not deletable and is skipped). Run the
telegram-init Ansible role to recreate the 8 canonical topics.

Usage:
    export TELEGRAM_BOT_TOKEN="123:abc"
    export TELEGRAM_FORUM_CHAT_ID="-1003450829353"
    python3 nuke-telegram-topics.py --max-thread-id 1000 [--dry-run]

The bot must be an admin of the chat with "Manage topics" permission.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from urllib import error, parse, request


def delete_topic(token: str, chat_id: str, thread_id: int, timeout: int = 10) -> tuple[bool, str]:
    """Call deleteForumTopic. Return (deleted, status_msg)."""
    url = f"https://api.telegram.org/bot{token}/deleteForumTopic"
    body = parse.urlencode({"chat_id": chat_id, "message_thread_id": thread_id}).encode()
    req = request.Request(url, data=body, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode()
            return True, f"200 {payload[:80]}"
    except error.HTTPError as e:
        msg = e.read().decode()[:120]
        return False, f"{e.code} {msg}"
    except Exception as e:  # noqa: BLE001
        return False, f"err {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-thread-id", type=int, default=1000,
                    help="Highest thread_id to attempt (default 1000)")
    ap.add_argument("--min-thread-id", type=int, default=2,
                    help="Lowest thread_id to attempt (default 2; "
                         "thread_id=1 is the General topic, not deletable)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print intended deletions without calling the API")
    ap.add_argument("--rate-limit", type=float, default=0.4,
                    help="Seconds between API calls to avoid 429 (default 0.4)")
    args = ap.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_FORUM_CHAT_ID")
    if not token or not chat_id:
        print("ERROR: set TELEGRAM_BOT_TOKEN and TELEGRAM_FORUM_CHAT_ID env vars",
              file=sys.stderr)
        return 2

    print(f"Target chat: {chat_id}")
    print(f"Thread ID range: {args.min_thread_id}..{args.max_thread_id}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'DELETE'}")
    print()

    deleted = 0
    skipped = 0
    for tid in range(args.min_thread_id, args.max_thread_id + 1):
        if args.dry_run:
            print(f"[dry-run] would attempt delete thread_id={tid}")
            continue
        ok, status = delete_topic(token, chat_id, tid)
        if ok:
            deleted += 1
            print(f"[ok ] thread_id={tid}: {status}")
        else:
            skipped += 1
            # Only log unexpected errors (not the boring "topic not found")
            if "TOPIC_NOT_MODIFIED" in status or "not found" in status.lower() \
                    or status.startswith("400") or status.startswith("404"):
                pass
            else:
                print(f"[skip] thread_id={tid}: {status}")
        time.sleep(args.rate_limit)

    print()
    print(f"Done. Deleted {deleted} topic(s); skipped {skipped} non-existent IDs.")
    print("Next: run telegram-init Ansible role to recreate canonical topics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
