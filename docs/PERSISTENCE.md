# Persistence

RideOps Server runs local-first by default. If `DATABASE_URL` is empty, runtime
state lives in Python dictionaries and is lost when the API process stops. When
`DATABASE_URL` points at PostgreSQL, the API keeps the same in-memory runtime
model but snapshots it to PostgreSQL after state-changing requests.

## How PostgreSQL Persistence Works

On FastAPI startup, `RuntimeStateStore` in `app/runtime_store.py` opens an
`asyncpg` pool when `DATABASE_URL` is configured. It creates the required schema,
loads the existing runtime row, then persists the current in-memory state back
to the database.

Every API mutation that changes riders, drivers, rides, wallets, notifications,
tokens, admin records, audit logs, abuse events, or realtime fallback state
calls `persist_runtime_state()`. That compatibility wrapper delegates to
`RuntimeStateStore.persist()`, which upserts one JSONB document in
`rideops_runtime_state` with `id = 'runtime'`.

PostGIS is used as an optional query projection for nearby-driver matching. If
`CREATE EXTENSION postgis` or the location table setup fails, the app keeps
running and falls back to in-memory Haversine distance sorting.

Redis is not durable storage. It is used for cache, realtime fanout,
Redis-backed idempotency records, and cross-instance domain-event publication.

