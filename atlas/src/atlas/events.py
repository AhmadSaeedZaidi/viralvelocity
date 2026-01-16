import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

import orjson

from atlas.db import db

logger = logging.getLogger("atlas.events")


class EventBus:
    async def emit(self, event_type: str, entity_id: str, payload: Dict[str, Any]) -> None:
        event_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc)

        payload_json = orjson.dumps(payload).decode("utf-8")

        query = """
        INSERT INTO system_events (id, event_type, entity_id, payload, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """

        try:
            async with db.get_connection() as conn:
                await conn.execute(
                    query, (event_id, event_type, entity_id, payload_json, created_at)
                )
            logger.debug(f"Event emitted: {event_type} -> {entity_id}")
        except Exception as e:
            logger.error(f"Event bus failure: {e}")


events = EventBus()
