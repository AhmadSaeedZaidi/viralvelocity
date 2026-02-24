import asyncio
import logging

from atlas.db import db, load_schema_sql

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("atlas.setup")


async def provision_schema() -> None:
    logger.info("Provisioning database schema...")

    sql_script = load_schema_sql()

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
