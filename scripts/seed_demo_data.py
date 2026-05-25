import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed RideOps demo accounts and rides.")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Load environment variables from a specific .env file before connecting.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing runtime state before seeding.",
    )
    parser.add_argument(
        "--deterministic",
        action="store_true",
        help="Use fixed seed timestamps and deterministic password hashes for repeatable review/test fixtures.",
    )
    parser.add_argument(
        "--base-time",
        default=None,
        help="ISO-8601 base timestamp for deterministic relative seed times.",
    )
    parser.add_argument(
        "--require-database",
        action="store_true",
        help="Fail when DATABASE_URL is not configured or reachable.",
    )
    return parser.parse_args()


async def run() -> int:
    args = parse_args()
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    else:
        load_dotenv(ROOT_DIR / ".env")

    sys.path.insert(0, str(ROOT_DIR))

    from app import main as rideops
    from app.seed import seed_demo_data

    await rideops.initialize_database()
    if args.require_database and not rideops.database_pool:
        print("DATABASE_URL is not configured or PostgreSQL is unavailable.", file=sys.stderr)
        return 1

    summary = seed_demo_data(
        rideops,
        reset=args.reset,
        deterministic=args.deterministic,
        base_time=args.base_time,
    )
    await rideops.persist_runtime_state()
    if rideops.database_pool:
        await rideops.close_database()

    print(json.dumps(summary, indent=2, sort_keys=True))
    if not rideops.DATABASE_URL:
        print(
            "No DATABASE_URL was configured; this run seeded only the current Python process. "
            "Use MYUBER_SEED_DEMO_DATA=true on API startup for in-memory demos.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
