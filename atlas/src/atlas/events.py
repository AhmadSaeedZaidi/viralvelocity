import uuid
import logging
import orjson
from datetime import datetime
from atlas.db import db

logger = logging.getLogger("atlas.events")

class EventBus:
    """
    System-wide Event Emitter.
    Writes to the immutable 'system_events' log.
    """
    async def emit(self, event_type: str, entity_id: str, payload: dict):
        event_id = str(uuid.uuid4())
        created_at = datetime.utcnow()
        
        # Use orjson for speed on massive payloads
        payload_json = orjson.dumps(payload).decode('utf-8')
        
        query = """
        INSERT INTO system_events (id, event_type, entity_id, payload, created_at)
        VALUES (%s, %s, %s, %s, %s)
        """
        
        try:
            async with db.get_connection() as conn:
                await conn.execute(query, (
                    event_id, event_type, entity_id, payload_json, created_at
                ))
            logger.debug(f"Event Emitted: {event_type} -> {entity_id}")
        except Exception as e:
            logger.error(f"Event Bus Failure: {e}")
            # Swallow error to protect pipeline uptime

events = EventBus()