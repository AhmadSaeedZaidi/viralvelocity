import logging
import os
import asyncio
from atlas.db import db

# Configure minimal logging for the script
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("atlas.setup")

async def provision_schema():
    """
    Reads schema.sql and applies it to the DB.
    Idempotent (safe to run multiple times).
    """
    # Locate SQL file relative to this script
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    
    if not os.path.exists(schema_path):
        logger.error(f"Schema file missing at: {schema_path}")
        return

    logger.info("Atlas: Provisioning Database Schema...")
    
    with open(schema_path, "r") as f:
        sql_script = f.read()

    try:
        async with db.get_connection() as conn:
            await conn.execute(sql_script)
        logger.info("Atlas: Schema Provisioned Successfully.")
    except Exception as e:
        logger.error(f"Atlas: Provisioning Failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(provision_schema())