import logging
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
        async with self._pool.connection() as conn:
            yield conn


db = DatabaseManager()
