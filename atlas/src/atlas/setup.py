import asyncio
import logging
import os
from pathlib import Path

from atlas.db import db

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("atlas.setup")


async def provision_schema() -> None:
    schema_path = Path(__file__).parent / "schema.sql"

    if not schema_path.exists():
        logger.error(f"Schema file missing at: {schema_path}")
        raise FileNotFoundError(f"Schema file not found: {schema_path}")

    logger.info("Provisioning database schema...")

    sql_script = schema_path.read_text()

    try:
        async with db.get_connection() as conn:
            await conn.execute(sql_script)
        logger.info("Schema provisioned successfully")
    except Exception as e:
        logger.error(f"Provisioning failed: {e}")
        raise
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(provision_schema())
