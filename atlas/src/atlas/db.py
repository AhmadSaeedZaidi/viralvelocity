import logging
from contextlib import asynccontextmanager
from psycopg_pool import AsyncConnectionPool
from atlas.config import settings

logger = logging.getLogger("atlas.db")

class DatabaseManager:
    """
    Singleton connection pool for Neon (Serverless Postgres).
    """
    _pool: AsyncConnectionPool = None

    def __init__(self):
        self.dsn = str(settings.DATABASE_URL)
        
    async def initialize(self):
        """
        Initializes the pool with serverless-friendly settings.
        min_size=0 allow Neon to scale to zero when idle.
        """
        if self._pool is None:
            logger.info("Atlas: Connecting to Database...")
            self._pool = AsyncConnectionPool(
                self.dsn,
                min_size=0, 
                max_size=20,
                timeout=30.0,
                kwargs={"autocommit": True}
            )

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("Atlas: DB Connection Closed.")

    @asynccontextmanager
    async def get_connection(self):
        """
        Async context manager for acquiring a connection.
        """
        if self._pool is None:
            await self.initialize()
        async with self._pool.connection() as conn:
            yield conn

# Global Singleton
db = DatabaseManager()