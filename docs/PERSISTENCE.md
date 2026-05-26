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

## Physical Schema

The durable runtime snapshot:

```sql
CREATE TABLE IF NOT EXISTS rideops_runtime_state (
    id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rideops_runtime_state_updated_at_idx
ON rideops_runtime_state (updated_at DESC);

CREATE INDEX IF NOT EXISTS rideops_runtime_state_data_gin_idx
ON rideops_runtime_state
USING GIN (data jsonb_path_ops);
```

The optional PostGIS driver-location projection:

```sql
CREATE TABLE IF NOT EXISTS rideops_driver_locations (
    driver_id TEXT PRIMARY KEY,
    location GEOGRAPHY(Point, 4326) NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    availability TEXT NOT NULL,
    current_ride_id TEXT,
    account_status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS rideops_driver_locations_geo_idx
ON rideops_driver_locations
USING GIST (location);

CREATE INDEX IF NOT EXISTS rideops_driver_locations_available_geo_idx
ON rideops_driver_locations
USING GIST (location)
WHERE availability = 'available'
  AND current_ride_id IS NULL
  AND account_status = 'active';

CREATE INDEX IF NOT EXISTS rideops_driver_locations_status_idx
ON rideops_driver_locations (availability, account_status, current_ride_id);

CREATE INDEX IF NOT EXISTS rideops_driver_locations_updated_at_idx
ON rideops_driver_locations (updated_at DESC);
```

The JSONB document currently contains these top-level stores:

- `riders`
- `drivers`
- `admins`
- `rides`
- `issue_reports`
- `notifications`
- `push_subscriptions`
- `trip_shares`
- `refresh_tokens`
- `fraud_events`
- `abuse_events`
- `audit_logs`
- `domain_events`
- `idempotency_records`
- `realtime_event_sequences`
- `realtime_dead_letters`

The detailed logical model is in [DB_SCHEMA.md](DB_SCHEMA.md).

