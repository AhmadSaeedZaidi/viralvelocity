"""
Example: Using the MaiaDAO adapter for Maia Agent workflows.

This demonstrates how to use the specialized database adapter provided by Atlas
for the Maia surveillance agent (Hunter and Tracker operations).

For creating your own custom DAO, see docs/contributing.md
"""

import asyncio

from atlas.adapters.maia import MaiaDAO


async def hunter_workflow_example():
    """Demonstrates the Hunter workflow using MaiaDAO."""
    dao = MaiaDAO()

    print("=== Hunter Workflow ===")

    batch = await dao.fetch_hunter_batch(batch_size=10)
    print(f"Fetched {len(batch)} search targets from queue")

    if batch:
        topic = batch[0]
        print(f"Processing topic: {topic['query_term']}")

        await dao.update_search_state(
            topic_id=topic["id"],
            next_token="NEXT_PAGE_TOKEN_HERE",
            result_count=25,
            status="active",
        )
        print(f"Updated search state for topic {topic['id']}")

    terms = ["machine learning", "neural networks", "computer vision"]
    added = await dao.add_to_search_queue(terms)
    print(f"Snowballed {added} new terms into search queue")


async def tracker_workflow_example():
    """Demonstrates the Tracker workflow using MaiaDAO."""
    dao = MaiaDAO()

    print("\n=== Tracker Workflow ===")

    targets = await dao.fetch_tracker_targets(batch_size=50)
    print(f"Found {len(targets)} videos needing stats updates")

    if targets:
        updates = [
            {
                "id": targets[0]["id"],
                "statistics": {"viewCount": 12345, "likeCount": 567, "commentCount": 89},
            }
        ]

        await dao.update_video_stats_batch(updates)
        print(f"Updated stats for {len(updates)} videos")


async def ingestion_example():
    """Demonstrates video metadata ingestion."""
    dao = MaiaDAO()

    print("\n=== Ingestion Example ===")

    video_data = {
        "id": "dQw4w9WgXcQ",
        "snippet": {
            "channelId": "UCuAXFkgsw1L7xaCfnd5JJOw",
            "channelTitle": "Example Channel",
            "title": "Example Video",
            "publishedAt": "2024-01-01T00:00:00Z",
            "tags": ["example", "demo", "tutorial"],
            "categoryId": "28",
            "defaultLanguage": "en",
        },
    }

    await dao.ingest_video_metadata(video_data)
    print(f"Ingested video: {video_data['snippet']['title']}")


async def main():
    """Run all workflow examples."""
    await hunter_workflow_example()
    await tracker_workflow_example()
    await ingestion_example()

    print("\n All examples completed successfully")


if __name__ == "__main__":
    asyncio.run(main())
