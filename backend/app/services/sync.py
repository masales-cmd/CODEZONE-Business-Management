from __future__ import annotations
import json
from uuid import uuid4
from sqlalchemy.orm import Session
from app.models.models import SyncOutbox


def queue_sync_event(db: Session, entity_type: str, entity_id: int | None, action: str, payload: dict) -> SyncOutbox:
    """Queue a compact, durable change event for devices/cloud sync."""
    event = SyncOutbox(
        event_id=str(uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        payload_json=json.dumps(payload, default=str, separators=(',', ':')),
    )
    db.add(event)
    return event
