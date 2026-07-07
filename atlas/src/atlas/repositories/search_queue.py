import logging
from datetime import UTC, datetime

from atlas.adapters import DatabaseAdapter
from atlas.models.search_queue import SearchQueueItem

logger = logging.getLogger("atlas.repositories.search_queue")


class SearchQueueRepository(DatabaseAdapter):
    async def fetch_batch(self, batch_size: int = 10) -> list[SearchQueueItem]:
        rows = await self._fetch_all(
            """
            SELECT *
            FROM search_queue
            WHERE status = 'active'
            ORDER BY priority DESC, mention_count DESC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (batch_size,),
        )
        return [SearchQueueItem.model_validate(r) for r in rows]

    async def update_state(
        self,
        topic_id: int,
        next_token: str | None,
        result_count: int,
        status: str = "active",
    ) -> None:
        now = datetime.now(UTC)
        await self._execute(
            """
            UPDATE search_queue
            SET next_page_token = %s,
                last_searched_at = %s,
                result_count_total = COALESCE(result_count_total, 0) + %s,
                status = %s
            WHERE id = %s
            """,
            (next_token, now, result_count, status, topic_id),
        )

    async def add_terms(self, terms: list[str]) -> int:
        if not terms:
            return 0

        unique_terms = list(set(terms))
        query = """
            INSERT INTO search_queue (query_term, priority, mention_count)
            VALUES (%s, 0, 1)
            ON CONFLICT (query_term)
            DO UPDATE SET mention_count = search_queue.mention_count + 1
        """
        params_list = [(term,) for term in unique_terms]
        await self._execute_many(query, params_list)
        return len(unique_terms)
