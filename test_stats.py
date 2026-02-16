"""Quick test script to debug the stats endpoint."""
import asyncio
from app.db import init_pool, get_connection, close_pool

async def test_stats():
    await init_pool()

    try:
        async with get_connection() as conn:
            print("[OK] Database connection successful\n")

            # Test 1: Check if entities table exists
            print("Test 1: Check entities table exists")
            table_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT FROM pg_tables WHERE tablename = 'entities')"
            )
            print(f"  Entities table exists: {table_exists}\n")

            if not table_exists:
                print("[ERROR] entities table doesn't exist! Run schema.sql first.")
                return

            # Test 2: Entity count
            print("Test 2: Get entity count")
            total = await conn.fetchval("SELECT COUNT(*) FROM entities")
            print(f"  Total entities: {total}\n")

            # Test 3: Type counts
            print("Test 3: Get counts by type")
            type_counts = await conn.fetch(
                """
                SELECT type, COUNT(*) as count
                FROM entities
                GROUP BY type
                ORDER BY count DESC
                """
            )
            print(f"  Found {len(type_counts)} entity types:")
            for row in type_counts:
                print(f"    - {row['type']}: {row['count']}")
            print()

            # Test 4: Time range
            print("Test 4: Get time range")
            time_range = await conn.fetchrow(
                """
                SELECT
                    MIN(t_start) as oldest,
                    MAX(COALESCE(t_end, t_start)) as newest
                FROM entities
                """
            )
            print(f"  Oldest: {time_range['oldest']}")
            print(f"  Newest: {time_range['newest']}\n")

            # Test 5: Database stats
            print("Test 5: Get database size stats")
            db_stats = await conn.fetchrow(
                """
                SELECT
                    pg_database_size(current_database()) / (1024.0 * 1024.0) as size_mb,
                    pg_total_relation_size('entities') / (1024.0 * 1024.0) as table_size_mb,
                    pg_indexes_size('entities') / (1024.0 * 1024.0) as index_size_mb
                """
            )
            print(f"  Database size: {db_stats['size_mb']:.2f} MB")
            print(f"  Table size: {db_stats['table_size_mb']:.2f} MB")
            print(f"  Index size: {db_stats['index_size_mb']:.2f} MB\n")

            print("[OK] All tests passed! Stats endpoint should work.")

    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await close_pool()

if __name__ == "__main__":
    asyncio.run(test_stats())
