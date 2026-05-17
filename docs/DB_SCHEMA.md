# DB Schema Diagram

MyUber uses runtime dictionaries as the primary domain store. When
`DATABASE_URL` is configured, the server persists those dictionaries into a
single JSONB row and maintains a separate PostGIS-backed driver location index
for nearest-driver queries.

Schema creation happens at FastAPI startup in `initialize_database()`.

## Physical PostgreSQL Schema

```mermaid
erDiagram
    MYUBER_RUNTIME_STATE {
        text id PK
        jsonb data
        timestamptz updated_at
    }

    MYUBER_DRIVER_LOCATIONS {
        text driver_id PK
        geography_point location
        double lat
        double lng
        text availability
        text current_ride_id
        text account_status
        timestamptz updated_at
    }
```

### `myuber_runtime_state`

Stores one row with `id = "runtime"`.

```sql
CREATE TABLE IF NOT EXISTS myuber_runtime_state (
    id TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

The `data` JSON object has these top-level keys:

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
- `audit_logs`

### `myuber_driver_locations`

Stores a query-optimized projection of driver locations.

```sql
CREATE TABLE IF NOT EXISTS myuber_driver_locations (
    driver_id TEXT PRIMARY KEY,
    location GEOGRAPHY(Point, 4326) NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    lng DOUBLE PRECISION NOT NULL,
    availability TEXT NOT NULL,
    current_ride_id TEXT,
    account_status TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS myuber_driver_locations_geo_idx
ON myuber_driver_locations
USING GIST (location);
```

This table is rebuilt from the in-memory `drivers` dictionary during persistence.
If PostGIS setup fails, the API falls back to in-memory Haversine sorting.

## Logical Domain Model

The runtime JSONB document stores the following logical entities.

```mermaid
erDiagram
    RIDER ||--o{ RIDE : requests
    RIDER ||--|| WALLET : funds
    DRIVER ||--o{ RIDE : accepts
    RIDE ||--|| PAYMENT : authorizes
    RIDE ||--o{ RATING : receives
    RIDE ||--o{ ISSUE_REPORT : has
    RIDE ||--o| TRIP_SHARE : exposes
    RIDE ||--o| FRAUD_EVENT : flags
    RIDER ||--o{ NOTIFICATION : receives
    RIDER ||--o{ REFRESH_TOKEN : owns
    RIDER ||--o{ FRAUD_EVENT : triggers
    DRIVER ||--o{ NOTIFICATION : receives
    DRIVER ||--o{ REFRESH_TOKEN : owns
    ADMIN ||--o{ REFRESH_TOKEN : owns
    ADMIN ||--o{ AUDIT_LOG : writes
    DRIVER ||--o| DRIVER_LOCATION_INDEX : projects
    ADMIN ||--o{ RIDER : manages
    ADMIN ||--o{ DRIVER : manages

    RIDER {
        string id PK
        string email
        string password_hash
        string name
        string phone
        string role
        string account_status
        object wallet
        string created_at
    }

    WALLET {
        string currency
        number balance
        array transactions
    }

    REFRESH_TOKEN {
        string id PK
        string token_hash
        string family_id
        string rotated_from
        string user_id
        string role
        string expires_at
        string revoked_at
    }

    FRAUD_EVENT {
        string id PK
        string ride_id
        string rider_id
        number risk_score
        string risk_level
        array signals
        string action
        string created_at
    }

    AUDIT_LOG {
        string id PK
        string action
        string outcome
        object actor
        string target_type
        string target_id
        object metadata
        string request_id
        string client_ip
        string user_agent
        string created_at
    }

    DRIVER {
        string id PK
        string email
        string password_hash
        string name
        string phone
        string role
        string account_status
        object vehicle
        string vehicle_type
        string license_number
        string onboarding_status
        object document_verification
        string availability
        object location
        string current_ride_id
        object stats
        string created_at
    }

    ADMIN {
        string id PK
        string email
        string password_hash
        string name
        string phone
        string role
        string admin_role
        array permissions
        string account_status
        string created_at
    }

    RIDE {
        string id PK
        string rider_id FK
        string driver_id FK
        object pickup
        object destination
        string vehicle_type
        string vehicle_type_label
        string promo_code
        string scheduled_for
        number distance_km
        number duration_min
        string currency
        number fare
        object fare_breakdown
        object cancellation_fee
        string status
        object driver_location
        object rider_location
        string dispatch_expires_at
        number dispatch_timeout_seconds
        number queue_position
        number queue_size
        string queued_at
        array declined_driver_ids
        array status_history
        string share_token
        string created_at
        string updated_at
    }

    PAYMENT {
        string id PK
        string ride_id FK
        number amount
        number authorized_amount
        number original_amount
        number released_amount
        string currency
        string method
        string status
        string authorization_code
        string authorized_at
        string captured_at
        string voided_at
        string refunded_at
        string receipt_number
        object receipt
        object fare_breakdown
        object cancellation_fee
        object refund
    }

    RATING {
        string id PK
        string ride_id FK
        number score
        string comment
        string from_user_id
        string from_role
        string to_user_id
        string to_role
        string created_at
        string updated_at
    }

    ISSUE_REPORT {
        string id PK
        string ride_id FK
        string category
        string description
        string status
        string reporter_user_id
        string reporter_role
        string created_at
        string updated_at
    }

    NOTIFICATION {
        string id PK
        string user_id
        string role
        string kind
        string title
        string body
        string ride_id
        string read_at
        string created_at
    }

    PUSH_SUBSCRIPTION {
        string id PK
        string user_id
        string role
        string endpoint
        object keys
        string created_at
        string updated_at
        string last_delivery_at
        string last_error
    }

    TRIP_SHARE {
        string token PK
        string ride_id FK
        string created_by_user_id
        string created_by_role
        string created_at
        string revoked_at
    }

    DRIVER_LOCATION_INDEX {
        string driver_id PK
        geography location
        number lat
        number lng
        string availability
        string current_ride_id
        string account_status
        string updated_at
    }
```

Driver document verification is stored inside each `DRIVER.document_verification`
object. It contains mock records for required license, insurance, and vehicle
registration documents plus aggregate counts and a `status` of
`pending_review`, `verified`, or `rejected`.

## Persistence Flow

```mermaid
flowchart TD
    Mutation["API mutation updates runtime dictionaries"]
    Persist["persist_runtime_state()"]
    RuntimeRow["Upsert myuber_runtime_state runtime JSONB row"]
    SyncIndex["sync_driver_location_index()"]
    DeleteIndex["Delete existing driver location projection"]
    InsertIndex["Insert active driver location rows"]
    Match["Nearest-driver matching query"]

    Mutation --> Persist
    Persist --> RuntimeRow
    Persist --> SyncIndex
    SyncIndex --> DeleteIndex
    DeleteIndex --> InsertIndex
    InsertIndex --> Match
```

## Operational Notes

- There are no migration files in the current repo.
- PostgreSQL is optional; without it, state remains in memory for the process
  lifetime.
- PostGIS is optional; without it, matching uses in-memory distance sorting.
- Redis is not part of durable storage. It is used for route/location caching
  and websocket fanout across API instances.
