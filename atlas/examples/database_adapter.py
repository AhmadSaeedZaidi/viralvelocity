"""
Example: Using DatabaseAdapter to create a service-specific data access layer.
This shows how to build a clean abstraction like MaiaDB for downstream services.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from atlas import DatabaseAdapter


class MaiaDB(DatabaseAdapter):
    """
    Data Access Object for Maia service.
    Encapsulates all SQL logic for the Hunter and Tracker agents.
    """

    async def fetch_hunter_batch(self, batch_size: int) -> List[Dict[str, Any]]:
        """Fetch search targets using SKIP LOCKED for concurrency."""
        query = """
            SELECT id, query_term, next_page_token, last_searched_at, priority
            FROM search_queue
            WHERE status = 'active'
            ORDER BY priority DESC, mention_count DESC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """
        return await self._fetch_all(query, (batch_size,))

    async def update_topic_state(
        self, topic_id: int, next_token: Optional[str], count: int
    ) -> None:
        """Update search_queue after successful hunt."""
        query = """
            UPDATE search_queue
            SET next_page_token = %s,
                last_searched_at = %s,
                result_count_total = COALESCE(result_count_total, 0) + %s
            WHERE id = %s
        """
        now = datetime.now(timezone.utc)
        await self._execute(query, (next_token, now, count, topic_id))

    async def add_to_search_queue(self, terms: List[str]) -> int:
        """
        Add terms to search queue (snowball effect).
        Returns number of terms processed.
        """
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

    async def fetch_tracker_targets(
        self, limits: Dict[str, Any], batch_size: int
    ) -> List[Dict[str, Any]]:
        """Fetch videos needing updates (3-Zone Logic)."""
        query = """
            WITH candidates AS (
                SELECT id, title, published_at, last_updated_at, 1 as priority
                FROM videos
                WHERE published_at >= %(z1_cutoff)s
                  AND (last_updated_at IS NULL OR last_updated_at < %(z1_thresh)s)
                
                UNION ALL
                
                SELECT id, title, published_at, last_updated_at, 2 as priority
                FROM videos
                WHERE published_at < %(z1_cutoff)s AND published_at >= %(z2_cutoff)s
                  AND (last_updated_at IS NULL OR last_updated_at < %(z2_thresh)s)

                UNION ALL
                
                SELECT id, title, published_at, last_updated_at, 3 as priority
                FROM videos
                WHERE published_at < %(z2_cutoff)s
                  AND (last_updated_at IS NULL OR last_updated_at < %(z3_thresh)s)
            )
            SELECT * FROM candidates
            ORDER BY priority ASC, last_updated_at ASC NULLS FIRST
            LIMIT %(limit)s
        """
        params = {**limits, "limit": batch_size}
        return await self._fetch_all(query, params)

    async def update_video_stats(self, updates: List[Dict[str, Any]]) -> None:
        """Update video stats and last_updated_at timestamp."""
        timestamp_query = "UPDATE videos SET last_updated_at = %s WHERE id = %s"
        log_query = """
            INSERT INTO video_stats_log (video_id, views, likes, comment_count, timestamp)
            VALUES (%s, %s, %s, %s, %s)
        """
        
        now = datetime.now(timezone.utc)
        
        async with self._cursor() as cur:
            for update in updates:
                vid = update["id"]
                stats = update["statistics"]
                
                await cur.execute(timestamp_query, (now, vid))
                await cur.execute(
                    log_query,
                    (
                        vid,
                        stats.get("viewCount"),
                        stats.get("likeCount"),
                        stats.get("commentCount"),
                        now,
                    ),
                )


async def main():
    """Example usage of MaiaDB adapter."""
    maia_db = MaiaDB()
    
    # Fetch hunter batch
    batch = await maia_db.fetch_hunter_batch(batch_size=10)
    print(f"Fetched {len(batch)} search targets")
    
    # Add terms to snowball queue
    terms = ["machine learning", "artificial intelligence", "deep learning"]
    count = await maia_db.add_to_search_queue(terms)
    print(f"Added {count} terms to search queue")
    
    # Fetch tracker targets
    limits = {
        "z1_cutoff": datetime.now(timezone.utc),
        "z1_thresh": datetime.now(timezone.utc),
        "z2_cutoff": datetime.now(timezone.utc),
        "z2_thresh": datetime.now(timezone.utc),
        "z3_thresh": datetime.now(timezone.utc),
    }
    targets = await maia_db.fetch_tracker_targets(limits, batch_size=100)
    print(f"Found {len(targets)} videos needing updates")


if __name__ == "__main__":
    asyncio.run(main())

