"""
Basic usage examples for Atlas infrastructure library.
"""
import asyncio
from typing import List

from atlas import AlertChannel, AlertLevel, db, events, notifier, settings, vault


async def database_example() -> None:
    """Demonstrate database operations."""
    print("Database Example:")
    
    is_healthy = await db.health_check()
    print(f"  Database health: {'OK' if is_healthy else 'FAILED'}")
    
    async with db.get_connection() as conn:
        result = await conn.execute(
            "SELECT COUNT(*) FROM system_events"
        )
        async for row in result:
            print(f"  Total events: {row[0]}")


async def storage_example() -> None:
    """Demonstrate storage operations."""
    print("\nStorage Example:")
    print(f"  Active vault: {settings.VAULT_PROVIDER}")
    
    test_data = {
        "video_id": "test123",
        "title": "Example Video",
        "views": 1000
    }
    
    vault.store_json("test/example.json", test_data)
    print("  Stored test data")
    
    retrieved = vault.fetch_json("test/example.json")
    if retrieved:
        print(f"  Retrieved: {retrieved['title']}")


async def events_example() -> None:
    """Demonstrate event emission."""
    print("\nEvents Example:")
    
    await events.emit(
        event_type="test.event",
        entity_id="example123",
        payload={
            "action": "demo",
            "status": "success"
        }
    )
    print("  Event emitted successfully")


async def notification_example() -> None:
    """Demonstrate notifications."""
    print("\nNotification Example:")
    
    await notifier.send(
        title="Test Alert",
        description="This is a test notification from Atlas",
        channel=AlertChannel.OPS,
        level=AlertLevel.INFO,
        fields={
            "Environment": settings.ENV,
            "Compliance": str(settings.COMPLIANCE_MODE)
        }
    )
    print("  Notification sent")


async def main() -> None:
    """Run all examples."""
    try:
        print("=" * 60)
        print("Atlas Infrastructure Library - Usage Examples")
        print("=" * 60)
        
        await database_example()
        await storage_example()
        await events_example()
        await notification_example()
        
        print("\n" + "=" * 60)
        print("All examples completed successfully!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\nError: {e}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())


