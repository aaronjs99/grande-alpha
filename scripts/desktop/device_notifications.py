"""Local inbox and terminal notices. No email, webhook, or external transport."""

import json
import sys

from grande_alpha.persistence.store import AuditStore


def display(notification: dict) -> None:
    # Escape provider-originated control characters; never execute terminal sequences.
    print("GRANDE ALERT " + json.dumps(notification, ensure_ascii=True), file=sys.stderr, flush=True)


def emit_pending(store: AuditStore, after_id: int = 0) -> int:
    rows = store.device_notifications(after_id=after_id)
    for row in rows:
        display(row)
    return rows[-1]["id"] if rows else after_id


def command_notifications(args) -> int:
    store = AuditStore()
    try:
        if args.ack is not None:
            store.acknowledge_notification(args.ack)
        print(json.dumps(store.device_notifications(after_id=args.after, limit=args.limit,
                                                    unread_only=args.unread), indent=2))
    finally:
        store.close()
    return 0
