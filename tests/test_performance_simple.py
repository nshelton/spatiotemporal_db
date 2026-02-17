"""Simple performance tests that work with existing database.

Run with: python -m pytest tests/test_performance_simple.py -v -s
"""

import asyncio
import os
import time
from datetime import datetime, timezone

import asyncpg


async def run_performance_tests():
    """Run performance tests on the existing database."""

    # Use the DATABASE_URL from environment
    db_url = os.getenv("DATABASE_URL", "postgresql://daruma:n3k0postgres@localhost:5432/daruma")

    print("\n" + "="*80)
    print("PERFORMANCE TEST SUITE")
    print("="*80)
    print(f"Database: {db_url.split('@')[1] if '@' in db_url else db_url}")

    # Connect to database
    conn = await asyncpg.connect(db_url)

    try:
        # Get current data stats
        total_count = await conn.fetchval("SELECT COUNT(*) FROM entities")
        gps_count = await conn.fetchval("SELECT COUNT(*) FROM entities WHERE type = 'location.gps'")

        print(f"\nCurrent Database Stats:")
        print(f"  Total entities: {total_count:,}")
        print(f"  GPS entities: {gps_count:,}")

        # Test 1: Baseline query (ORDER BY t_start)
        print("\n" + "="*80)
        print("TEST 1: Baseline Query (ORDER BY t_start DESC)")
        print("="*80)

        start = time.time()

        result = await conn.fetch(
            """
            SELECT id, type, t_start, t_end,
                   ST_Y(geom) AS lat,
                   ST_X(geom) AS lon
            FROM entities
            WHERE type = ANY($1)
              AND geom IS NOT NULL
              AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
              AND t_range && tstzrange($6, $7, '[]')
            ORDER BY t_start DESC
            LIMIT $8
            """,
            ["location.gps"],
            -118.35184109736056,  # min_lon
            34.0579790212214,     # min_lat
            -118.27100741338162,  # max_lon
            34.08843668922782,    # max_lat
            datetime(2023, 3, 5, 12, 46, 47, tzinfo=timezone.utc),
            datetime(2027, 6, 25, 4, 20, 21, tzinfo=timezone.utc),
            5000,
        )

        elapsed = time.time() - start

        print(f"[OK] Query completed in {elapsed:.3f}s")
        print(f"  Returned {len(result):,} entities")
        print(f"  Rate: {len(result)/elapsed:.0f} entities/second")

        # Test 2: Random ordering query (reproducing the slow query)
        print("\n" + "="*80)
        print("TEST 2: Random Ordering Query (ORDER BY RANDOM())")
        print("="*80)
        print("[!] This is expected to be slow!")

        queries = [
            {
                "name": "Query 1 (Large bbox)",
                "bbox": [-118.35184109736056, 34.0579790212214, -118.27100741338162, 34.08843668922782],
            },
            {
                "name": "Query 2 (Large bbox)",
                "bbox": [-118.34861697215173, 34.059580898843706, -118.27513180714514, 34.087269687093794],
            },
            {
                "name": "Query 3 (Small bbox)",
                "bbox": [-118.3202714282635, 34.07357087894343, -118.31124407466619, 34.0769723339472],
            },
        ]

        times = []

        for q in queries:
            print(f"\n{q['name']}:")
            print(f"  BBox: {q['bbox']}")

            start = time.time()

            result = await conn.fetch(
                """
                SELECT id, type, t_start, t_end,
                       ST_Y(geom) AS lat,
                       ST_X(geom) AS lon
                FROM entities
                WHERE type = ANY($1)
                  AND geom IS NOT NULL
                  AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
                  AND t_range && tstzrange($6, $7, '[]')
                ORDER BY RANDOM()
                LIMIT $8
                """,
                ["location.gps"],
                q['bbox'][0], q['bbox'][1], q['bbox'][2], q['bbox'][3],
                datetime(2023, 3, 5, 12, 46, 47, tzinfo=timezone.utc),
                datetime(2027, 6, 25, 4, 20, 21, tzinfo=timezone.utc),
                5000,
            )

            elapsed = time.time() - start
            times.append(elapsed)

            print(f"  [!] Completed in {elapsed:.3f}s")
            print(f"  Returned {len(result):,} entities")

        avg_time = sum(times) / len(times)
        print(f"\n[STATS] Average RANDOM query time: {avg_time:.3f}s")
        print(f"[STATS] Min: {min(times):.3f}s, Max: {max(times):.3f}s")

        # Test 3: EXPLAIN ANALYZE
        print("\n" + "="*80)
        print("TEST 3: Query Execution Plans (EXPLAIN ANALYZE)")
        print("="*80)

        print("\nBaseline query plan:")
        print("-" * 80)
        result = await conn.fetch(
            """
            EXPLAIN ANALYZE
            SELECT id, type, t_start, t_end,
                   ST_Y(geom) AS lat,
                   ST_X(geom) AS lon
            FROM entities
            WHERE type = ANY($1)
              AND geom IS NOT NULL
              AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
              AND t_range && tstzrange($6, $7, '[]')
            ORDER BY t_start DESC
            LIMIT $8
            """,
            ["location.gps"],
            -118.35184109736056, 34.0579790212214, -118.27100741338162, 34.08843668922782,
            datetime(2023, 3, 5, 12, 46, 47, tzinfo=timezone.utc),
            datetime(2027, 6, 25, 4, 20, 21, tzinfo=timezone.utc),
            5000,
        )
        for row in result:
            print(row[0])

        print("\n" + "-" * 80)
        print("RANDOM ordering query plan:")
        print("-" * 80)
        result = await conn.fetch(
            """
            EXPLAIN ANALYZE
            SELECT id, type, t_start, t_end,
                   ST_Y(geom) AS lat,
                   ST_X(geom) AS lon
            FROM entities
            WHERE type = ANY($1)
              AND geom IS NOT NULL
              AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
              AND t_range && tstzrange($6, $7, '[]')
            ORDER BY RANDOM()
            LIMIT $8
            """,
            ["location.gps"],
            -118.35184109736056, 34.0579790212214, -118.27100741338162, 34.08843668922782,
            datetime(2023, 3, 5, 12, 46, 47, tzinfo=timezone.utc),
            datetime(2027, 6, 25, 4, 20, 21, tzinfo=timezone.utc),
            5000,
        )
        for row in result:
            print(row[0])

        # Test 4: Check indexes
        print("\n" + "="*80)
        print("TEST 4: Index Information")
        print("="*80)

        result = await conn.fetch(
            """
            SELECT
                indexname,
                pg_size_pretty(pg_relation_size(indexname::regclass)) as size
            FROM pg_indexes
            WHERE tablename = 'entities'
            ORDER BY indexname;
            """
        )

        print("\nIndexes on 'entities' table:")
        for row in result:
            print(f"  - {row['indexname']}: {row['size']}")

        table_size = await conn.fetchval(
            "SELECT pg_size_pretty(pg_total_relation_size('entities'))"
        )
        print(f"\nTotal table size (including indexes): {table_size}")

        # Summary
        print("\n" + "="*80)
        print("SUMMARY & RECOMMENDATIONS")
        print("="*80)

        if avg_time > 1.0:
            print(f"\n[!] PERFORMANCE ISSUE CONFIRMED!")
            print(f"  Average query time with RANDOM(): {avg_time:.3f}s")
            print(f"  This is {avg_time:.1f}x slower than acceptable (< 1s target)")

            print(f"\n[TIP] Recommended Solutions:")
            print(f"  1. Remove ORDER BY RANDOM() - use application-level sampling instead")
            print(f"  2. Use TABLESAMPLE for approximate random sampling")
            print(f"  3. Use two-step approach: COUNT + random OFFSET")
            print(f"  4. Pre-fetch larger dataset and sample in application code")
        else:
            print(f"\n[OK] Performance is acceptable")
            print(f"  Average query time: {avg_time:.3f}s")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run_performance_tests())
