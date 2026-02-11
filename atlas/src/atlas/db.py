import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from atlas.config import settings

logger = logging.getLogger("atlas.db")


class DatabaseManager:
    _instance: Optional["DatabaseManager"] = None
    _pool: Optional[AsyncConnectionPool] = None

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not hasattr(self, "dsn"):
            self.dsn = str(settings.DATABASE_URL)

    async def initialize(self) -> None:
        if self._pool is None:
            logger.info("Atlas: Connecting to Database...")
            self._pool = AsyncConnectionPool(
                self.dsn,
                min_size=0,
                max_size=20,
                timeout=30.0,
                open=True,
            )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Atlas: DB Connection Closed.")

    async def health_check(self) -> bool:
        try:
            async with self.get_connection() as conn:
                result = await conn.execute("SELECT 1")
                return result is not None
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[AsyncConnection, None]:
        if self._pool is None:
            await self.initialize()
        assert self._pool is not None, "Pool initialization failed"
        async with self._pool.connection() as conn:
            yield conn

    async def setup_test_schema(self) -> None:
        """
        Initialize database schema for testing.
        """
        async with self.get_connection() as conn:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;")

            import atlas

            schema_path = os.path.join(os.path.dirname(atlas.__file__), "schema.sql")

            if not os.path.exists(schema_path):
                raise FileNotFoundError(f"Could not find schema.sql at {schema_path}")

            with open(schema_path, "r") as f:
                sql_script = f.read()

                filtered_lines = []
                for line in sql_script.split("\n"):
                    if not line.strip().upper().startswith("CREATE EXTENSION"):
                        filtered_lines.append(line)

                filtered_script = "\n".join(filtered_lines)
                await conn.execute(filtered_script)

            logger.info("Test schema initialized successfully")

    async def reset_for_test(self) -> None:
        """
        Reset database state for testing.
        """
        async with self.get_connection() as conn:
            await conn.execute(
                """
                DO $$ 
                DECLARE 
                    r RECORD; 
                BEGIN 
                    -- Iterate over all tables in public schema
                    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP 
                        -- TRUNCATE is faster than DELETE
                        -- RESTART IDENTITY resets serials (id=1)
                        -- CASCADE handles foreign keys
                        EXECUTE 'TRUNCATE TABLE public.' || quote_ident(r.tablename) || ' RESTART IDENTITY CASCADE'; 
                    END LOOP; 
                END $$;
            """
            )
            logger.debug("Database reset for test")


db = DatabaseManager()
