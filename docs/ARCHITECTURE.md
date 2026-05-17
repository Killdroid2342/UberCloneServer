# Architecture Diagram

MyUber is a small ride-hailing demo application with a TypeScript browser
client, a FastAPI backend, optional PostgreSQL/PostGIS persistence, optional
Redis cache/pubsub, and external map/routing providers.

## System Diagram

```mermaid
flowchart LR
    Browser["Browser client\nMyUberClient"]
    Static["Static client files"]
    API["FastAPI app\nMyUberServer"]
    Runtime["Runtime dictionaries\nriders drivers rides notifications"]
    Security["Security controls\nrate limits refresh tokens fraud events"]
    Postgres["PostgreSQL + PostGIS\noptional durable runtime state\nand driver location index"]
    Redis["Redis\noptional cache and realtime pubsub"]
    Nominatim["OpenStreetMap Nominatim\nlocation search"]
    Routing["OSRM or GraphHopper\nroute estimates"]

    Browser -->|HTTP static files| Static
    Browser -->|REST JSON| API
    Browser <-->|WebSockets| API
    API --> Runtime
    API --> Security
    API -->|upsert JSONB and driver index| Postgres
    API <-->|cache and pubsub| Redis
    API -->|GET /search| Nominatim
    API -->|route requests| Routing
```

## Backend Responsibilities

The FastAPI app handles:

- account signup/login for riders, drivers, and admins
- short-lived JWT auth, rotating refresh tokens, and role checks
- rate limiting for sensitive and high-volume HTTP endpoints
- basic fraud/risk scoring during ride creation and admin risk reporting
- account suspension and admin operations
- driver document verification gates availability and dispatch eligibility
- location search through Nominatim
- route and fare estimates through OSRM or GraphHopper
- ride creation, matching, progression, cancellation, rating, issues, sharing,
  refunds, and history
- dispatch timeout processing and admin demand heatmap aggregation
- driver availability, location updates, and earnings
- notification creation and unread counts
- WebSocket fanout for rides, drivers, and public share links

## Frontend Responsibilities

The TypeScript client handles:

- access and refresh token storage in `localStorage`, plus access-token refresh
  and retry for authenticated API calls
- REST API calls through `src/api.ts`
- realtime socket connection, heartbeat, reconnect, and message queuing through
  `src/socket.ts`
- location publishing and route refresh intervals from `src/config.ts`
- map, directions, ride, and UI state modules under `src/`

## Request Ride Flow

```mermaid
sequenceDiagram
    participant RiderClient as Rider Client
    participant API as FastAPI API
    participant Routing as OSRM/GraphHopper
    participant Store as Runtime State
    participant DriverClient as Driver Client
    participant Redis as Redis PubSub

    RiderClient->>API: POST /rides
    API->>Routing: route estimate
    Routing-->>API: distance, duration, geometry
    API->>Store: create ride and authorized mock payment
    API->>Store: find nearest available driver
    API-->>DriverClient: ride_request websocket event
    API-->>Redis: publish driver event when configured
    API-->>RiderClient: Ride response
```

## Realtime Architecture

```mermaid
flowchart TD
    Mutation["Ride or driver mutation"]
    Broadcast["broadcast_ride() or broadcast_driver()"]
    LocalSockets["Local websocket connections"]
    RedisPub["Redis publish\nmyuber:realtime"]
    OtherAPI["Other API instances"]
    OtherSockets["Other instance websocket connections"]

    Mutation --> Broadcast
    Broadcast --> LocalSockets
    Broadcast --> RedisPub
    RedisPub --> OtherAPI
    OtherAPI --> OtherSockets
```

If Redis is unavailable or not configured, realtime still works for clients
connected to the same API process.

## Data And Failure Behavior

- If `DATABASE_URL` is missing or PostgreSQL connection fails, the app uses
  in-memory state only.
- If PostGIS setup fails, nearest-driver matching falls back to Haversine
  sorting in Python.
- If Redis is missing or unavailable, caching and cross-instance websocket
  fanout are skipped.
- If the configured routing provider fails, route estimates fall back to an
  adjusted straight-line estimate.
- A default admin is created at startup from `MYUBER_ADMIN_EMAIL` and
  `MYUBER_ADMIN_PASSWORD`.
- JWT signing uses `MYUBER_SECRET_KEY`. If it is missing, the API generates a
  temporary in-memory secret and logs a warning.
- Access tokens default to 15 minutes. Refresh tokens default to 30 days, are
  stored server-side only as hashes, and rotate on every refresh.
- Rate limiting uses Redis counters when Redis is connected and in-memory
  counters otherwise.
