import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol, Tuple

from psycopg import AsyncConnection

logger = logging.getLogger("atlas.adapters")


class ConnectionProvider(Protocol):
    @asynccontextmanager
    async def get_connection(self) -> AsyncIterator[AsyncConnection]: ...


class DatabaseAdapterProtocol(Protocol):
    async def _execute(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> None: ...

    async def _fetch_all(
        self, query: str, params: Optional[Tuple[Any, ...]] = None
    ) -> List[Dict[str, Any]]: ...

    async def _execute_many(self, query: str, params_list: List[Tuple[Any, ...]]) -> None: ...


class DatabaseAdapter:
    def __init__(self, db_pool: Optional[ConnectionProvider] = None) -> None:
        if db_pool is None:
            from atlas.db import db

            db_pool = db
        self._db = db_pool

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[AsyncConnection]:
        async with self._db.get_connection() as conn:
            yield conn

    @asynccontextmanager
    async def _cursor(self) -> AsyncIterator[Any]:
        async with self._db.get_connection() as conn:
            async with conn.cursor() as cur:
                yield cur

    async def _execute(self, query: str, params: Optional[Tuple[Any, ...]] = None) -> None:
        async with self._connection() as conn:
            await conn.execute(query, params or ())
            await conn.commit()

    async def _fetch_one(
        self, query: str, params: Optional[Tuple[Any, ...]] = None
    ) -> Optional[Dict[str, Any]]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            row = await cur.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in cur.description] if cur.description else []
            return dict(zip(columns, row))

    async def _fetch_all(
        self, query: str, params: Optional[Tuple[Any, ...]] = None
    ) -> List[Dict[str, Any]]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchall()
            return [dict(zip(columns, row)) for row in rows]

    async def _fetch_many(
        self, query: str, params: Optional[Tuple[Any, ...]], limit: int
    ) -> List[Dict[str, Any]]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchmany(limit)
            return [dict(zip(columns, row)) for row in rows]

    async def _execute_many(self, query: str, params_list: List[Tuple[Any, ...]]) -> None:
        async with self._cursor() as cur:
            await cur.executemany(query, params_list)
            await cur.connection.commit()

    async def _fetch_scalar(
        self, query: str, params: Optional[Tuple[Any, ...]] = None
    ) -> Optional[Any]:
        async with self._cursor() as cur:
            await cur.execute(query, params or ())
            row = await cur.fetchone()
            return row[0] if row else None
