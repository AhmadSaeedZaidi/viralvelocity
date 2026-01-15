import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from atlas.db import db

logger = logging.getLogger("atlas.adapters")


class DatabaseAdapter:
    """
    Base class for database adapters.
    Provides common patterns for executing queries and handling results.
    """

    @asynccontextmanager
    async def _connection(self):
        async with db.get_connection() as conn:
            yield conn

    @asynccontextmanager
    async def _cursor(self):
        async with db.get_connection() as conn:
            async with conn.cursor() as cur:
                yield cur

    async def _execute(self, query: str, params: Optional[Tuple] = None) -> None:
        async with self._connection() as conn:
            await conn.execute(query, params or ())

    async def _fetch_one(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Dict[str, Any]]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            row = await cur.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cur.description] if cur.description else []
            return dict(zip(columns, row))

    async def _fetch_all(
        self, query: str, params: Optional[Tuple] = None
    ) -> List[Dict[str, Any]]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def _fetch_many(
        self, query: str, params: Optional[Tuple], limit: int
    ) -> List[Dict[str, Any]]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchmany(limit)
            return [dict(zip(columns, row)) for row in rows]

    async def _execute_many(self, query: str, params_list: List[Tuple]) -> None:
        async with self._cursor() as cur:
            await cur.executemany(query, params_list)

    async def _fetch_scalar(
        self, query: str, params: Optional[Tuple] = None
    ) -> Optional[Any]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            row = await cur.fetchone()
            return row[0] if row else None
