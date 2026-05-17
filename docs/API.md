# API Documentation

The API is implemented in `MyUberServer/app/main.py` with FastAPI. Most data is
held in runtime dictionaries and can optionally be persisted to PostgreSQL.

## Basics

- Base URL: `http://localhost:8000`
- Content type: `application/json`
- Authenticated requests use `Authorization: Bearer <token>`.
- Login endpoints return a short-lived JWT access token plus a rotating opaque
  refresh token. The default access-token lifetime is 15 minutes; refresh tokens
  default to 30 days.
- Responses include an `X-Request-ID` header. Send `X-Request-ID` on the
  request to preserve your own correlation ID in API logs and error bodies.
- Error responses preserve the `detail` field and include `request_id`.
- Rate-limited responses return `429`, `Retry-After`, and `X-RateLimit-*`
  headers. Normal responses include the current `X-RateLimit-*` window headers.
- Common error codes:
  - `400`: validation or unsupported input
  - `401`: missing or invalid token
  - `403`: authenticated user cannot perform the action
  - `404`: target resource was not found
  - `409`: valid request conflicts with current ride or account state
  - `429`: request rate limit exceeded

## Core Types

### `LatLng`

```json
{
  "lat": 51.5074,
  "lng": -0.1278
}
```

### `UserProfile`

Returned for riders, drivers, and admins. `password_hash` is never returned.

```json
{
  "id": "uuid",
  "email": "rider@example.com",
  "name": "Rider Name",
  "phone": "+15551234567",
  "role": "rider",
  "account_status": "active",
  "wallet": {
    "currency": "USD",
    "balance": 50,
    "available_balance": 50,
    "authorized_hold": 0,
    "transactions": []
  },
  "average_rating": null,
  "rating_count": 0
}
```

Driver profiles also include:

```json
{
  "vehicle": {
    "make": "Toyota",
    "model": "Prius",
    "year": 2022,
    "color": "Black",
    "plate": "MYU-123",
    "type": "standard",
    "type_label": "Standard"
  },
  "license_number": "LIC-123",
  "onboarding_status": "pending_review",
  "document_verification": {
    "status": "pending_review",
    "required": 3,
    "verified": 0,
    "pending": 3,
    "rejected": 0,
    "documents": {
      "license": {
        "type": "license",
        "label": "Driver license",
        "required": true,
        "status": "pending_review"
      }
    }
  },
  "availability": "offline",
  "location": null,
  "current_ride_id": null,
  "stats": {
    "accepted": 0,
    "rejected": 0
  }
}
```

### `Ride`

```json
{
  "id": "uuid",
  "rider_id": "uuid",
  "pickup": { "lat": 51.5074, "lng": -0.1278 },
  "destination": { "lat": 51.5155, "lng": -0.1419 },
  "vehicle_type": "standard",
  "vehicle_type_label": "Standard",
  "promo_code": "SAVE10",
  "scheduled_for": null,
  "distance_km": 3.2,
  "duration_min": 12,
  "currency": "USD",
  "fare": 9.4,
  "fare_breakdown": {},
  "payment": {},
  "fraud_assessment": {
    "risk_score": 20,
    "risk_level": "low",
    "signals": [],
    "review_required": false,
    "blocked": false
  },
  "cancellation_fee": null,
  "status": "pending_driver",
  "driver_id": "uuid",
  "driver": {},
  "driver_distance_km": 1.1,
  "driver_location": { "lat": 51.506, "lng": -0.12 },
  "dispatch_expires_at": "2026-05-17T00:00:30+00:00",
  "dispatch_timeout_seconds": 30,
  "rider_location": null,
  "queue_position": null,
  "queue_size": null,
  "status_history": [],
  "created_at": "2026-05-17T00:00:00+00:00",
  "updated_at": "2026-05-17T00:00:00+00:00"
}
```

### `Receipt`

Generated when a paid ride or cancellation fee is captured.

```json
{
  "id": "receipt_uuid",
  "receipt_number": "RCPT-20260517-ABC12345",
  "ride_id": "uuid",
  "payment_id": "mock_uuid",
  "type": "ride_fare",
  "currency": "USD",
  "subtotal": 9.4,
  "total": 9.4,
  "status": "paid",
  "generated_at": "2026-05-17T00:00:00+00:00",
  "paid_at": "2026-05-17T00:00:00+00:00",
  "line_items": [
    { "label": "Base fare", "amount": 3.5, "currency": "USD" }
  ],
  "email_delivery": {
    "channel": "email",
    "status": "sent",
    "provider": "smtp",
    "sent_at": "2026-05-17T00:00:00+00:00"
  }
}
```

Ride statuses:

- `scheduled`
- `matching`
- `pending_driver`
- `accepted`
- `arrived`
- `in_progress`
- `completed`
- `cancelled`
- `no_drivers_available`

## Health

### `GET /`

Returns the API status and storage configuration.

Auth: none

Response:

```json
{
  "status": "MyUber API running",
  "storage": {
    "postgres": { "configured": true, "connected": true },
    "postgis": { "enabled": true },
    "redis": {
      "configured": true,
      "connected": true,
      "cache_ttl_seconds": 900
    },
    "routing": {},
    "security": {
      "rate_limiting": {
        "enabled": true,
        "default_per_minute": 180,
        "read_per_minute": 300,
        "login_per_minute": 6,
        "ride_requests_per_minute": 12
      },
      "auth": {
        "access_token_expire_minutes": 15,
        "refresh_token_expire_days": 30
      },
      "fraud": {
        "review_score": 60,
        "block_score": 90,
        "recent_window_minutes": 15
      }
    }
  }
}
```

### `GET /health/storage`

Returns the storage, routing, and security configuration status object.

Auth: none

### `GET /vehicle-types`

Returns supported ride and driver vehicle categories.

Auth: none

Response:

```json
[
  {
    "type": "standard",
    "label": "Standard",
    "description": "Everyday cars for up to 4 riders",
    "capacity": 4,
    "fare_multiplier": 1.0
  }
]
```

## Authentication

### `POST /riders/signup`

Creates a rider account.

Auth: none

Request:

```json
{
  "email": "rider@example.com",
  "password": "secret",
  "name": "Rider Name",
  "phone": "+15551234567"
}
```

Response: `UserProfile`

### `POST /riders/login`

Authenticates a rider.

Auth: none

Request:

```json
{
  "email": "rider@example.com",
  "password": "secret"
}
```

Response:

```json
{
  "token": "access-jwt",
  "access_token": "access-jwt",
  "refresh_token": "opaque-refresh-token",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_expires_in": 2592000,
  "user": {}
}
```

### `POST /drivers/signup`

Creates a driver account. New drivers start `offline` with no location and
`pending_review` document verification.

Auth: none

Request:

```json
{
  "email": "driver@example.com",
  "password": "secret",
  "name": "Driver Name",
  "phone": "+15557654321",
  "vehicle_make": "Toyota",
  "vehicle_model": "Prius",
  "vehicle_year": 2022,
  "vehicle_color": "Black",
  "vehicle_plate": "MYU-123",
  "vehicle_type": "standard",
  "license_number": "LIC-123"
}
```

Response: `UserProfile`

Notes:

- The API creates mock required document records for license, insurance, and
  vehicle registration.
- Drivers cannot go online or receive dispatches until an admin marks documents
  `verified`.

### `POST /drivers/login`

Authenticates a driver.

Auth: none

Request:

```json
{
  "email": "driver@example.com",
  "password": "secret"
}
```

Response:

```json
{
  "token": "access-jwt",
  "access_token": "access-jwt",
  "refresh_token": "opaque-refresh-token",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_expires_in": 2592000,
  "user": {}
}
```

### `POST /admin/login`

Authenticates an admin. The default admin is created at startup from
`MYUBER_ADMIN_EMAIL` and `MYUBER_ADMIN_PASSWORD`.

Auth: none

Request:

```json
{
  "email": "admin@myuber.local",
  "password": "<MYUBER_ADMIN_PASSWORD>"
}
```

Response:

```json
{
  "token": "access-jwt",
  "access_token": "access-jwt",
  "refresh_token": "opaque-refresh-token",
  "token_type": "bearer",
  "expires_in": 900,
  "refresh_expires_in": 2592000,
  "user": {}
}
```

### `POST /auth/refresh`

Rotates a refresh token and returns a fresh access-token/refresh-token pair.
The presented refresh token is revoked after a successful rotation.

Auth: none, refresh token required

Request:

```json
{
  "refresh_token": "opaque-refresh-token"
}
```

Response: same token envelope as login.

### `POST /auth/logout`

Revokes the supplied refresh token. Existing access tokens continue to expire
naturally, so clients should clear local tokens after calling this endpoint.

Auth: none, refresh token required

Request:

```json
{
  "refresh_token": "opaque-refresh-token"
}
```

Response:

```json
{
  "status": "logged_out"
}
```

### `GET /auth/me`

Returns the current authenticated user.

Auth: rider, driver, or admin

Response: `UserProfile`

## Security Controls

Rate limiting is enabled by default. The middleware uses Redis counters when
Redis is connected and in-memory per-process counters otherwise. Main defaults:

- Login endpoints: 6 requests per minute per client IP
- Signup endpoints: 10 requests per hour per client IP
- Refresh/logout endpoints: 30 requests per minute per client IP
- Ride creation: 12 requests per minute per client IP
- GET requests: 300 requests per minute per client IP
- Other writes: 180 requests per minute per client IP

Environment knobs:

- `MYUBER_RATE_LIMIT_ENABLED`
- `MYUBER_RATE_LIMIT_DEFAULT_PER_MINUTE`
- `MYUBER_RATE_LIMIT_READ_PER_MINUTE`
- `MYUBER_RATE_LIMIT_LOGIN_PER_MINUTE`
- `MYUBER_RATE_LIMIT_SIGNUP_PER_HOUR`
- `MYUBER_RATE_LIMIT_REFRESH_PER_MINUTE`
- `MYUBER_RATE_LIMIT_RIDE_REQUESTS_PER_MINUTE`

Basic fraud detection runs during `POST /rides` and scores signals such as
rapid request velocity, overlapping active rides, repeated cancellations,
distant pickup jumps, high-value trips, and repeated promo usage. Thresholds are
configured with `MYUBER_FRAUD_REVIEW_SCORE`, `MYUBER_FRAUD_BLOCK_SCORE`, and
`MYUBER_FRAUD_RECENT_WINDOW_MINUTES`.

Role-based access uses JWT roles plus server-side permission checks. Riders and
drivers receive fixed role permissions for their own resources. Admins receive
an `admin_role` and `permissions`; the default admin is `super_admin` and can
read operations, fraud, audit logs, observability, and manage users/drivers.

## Notifications

### `GET /notifications`

Returns notifications for the current user.

Auth: rider, driver, or admin

Response:

```json
{
  "unread_count": 1,
  "notifications": [
    {
      "id": "note_id",
      "user_id": "uuid",
      "role": "rider",
      "kind": "ride_accepted",
      "title": "Ride accepted",
      "body": "Driver Name is on the way to your pickup.",
      "ride_id": "ride_id",
      "read_at": null,
      "created_at": "2026-05-17T00:00:00+00:00"
    }
  ]
}
```

### `POST /notifications/{notification_id}/read`

Marks one notification as read.

Auth: notification owner

Response:

```json
{
  "notification": {},
  "unread_count": 0,
  "notifications": []
}
```

## Wallet

Rider payments use the built-in wallet. New rider accounts receive the
configured starter balance from `MYUBER_DEFAULT_WALLET_BALANCE` (default `50`).
Active authorized rides reduce `available_balance` until they are paid, voided,
refunded, or settled as a cancellation fee.

### `GET /wallet`

Returns the current rider wallet.

Auth: rider

Response:

```json
{
  "currency": "USD",
  "balance": 50,
  "available_balance": 41.5,
  "authorized_hold": 8.5,
  "transactions": []
}
```

### `POST /wallet/top-up`

Adds mock funds to the current rider wallet.

Auth: rider

Request:

```json
{
  "amount": 25
}
```

Response: wallet payload

### `POST /notifications/read-all`

Marks all current user's notifications as read.

Auth: rider, driver, or admin

Response:

```json
{
  "unread_count": 0,
  "notifications": []
}
```

### `GET /notifications/push-config`

Returns browser push configuration for the client.

Auth: none

Response:

```json
{
  "enabled": true,
  "provider_ready": true,
  "public_key": "<VAPID public key>"
}
```

### `POST /notifications/push-subscriptions`

Stores or updates the current user's browser push subscription.

Auth: rider, driver, or admin

Request: standard `PushSubscription.toJSON()` payload.

Response:

```json
{
  "subscription": {
    "id": "push_id",
    "role": "rider",
    "endpoint_hash": "a1b2c3d4e5f6",
    "created_at": "2026-05-17T00:00:00+00:00",
    "updated_at": "2026-05-17T00:00:00+00:00"
  },
  "push": {}
}
```

Notes:

- Web Push delivery requires `pywebpush` plus `MYUBER_VAPID_PUBLIC_KEY` and
  `MYUBER_VAPID_PRIVATE_KEY`.
- Without VAPID config, the client can still show local browser alerts while
  the app is open.

## Admin

### `GET /admin/dashboard`

Returns operational totals, revenue, demand, demand heatmap cells, analytics,
basic fraud/risk signals, access-control details, audit summary, observability
summary, active rides, recent rides, drivers, users, and open issues.

Auth: admin with `admin.dashboard.read`

Response: `AdminDashboard`

Demand heatmap cells are grouped pickup-demand buckets:

```json
{
  "id": "51.5100:-0.1300",
  "lat": 51.51,
  "lng": -0.13,
  "count": 3,
  "intensity": 1,
  "queued_count": 1,
  "pending_count": 2,
  "vehicle_types": { "standard": 2, "premium": 1 },
  "status_counts": { "pending_driver": 2, "no_drivers_available": 1 },
  "latest_request_at": "2026-05-17T00:00:00+00:00"
}
```

Fraud summary:

```json
{
  "total_events": 3,
  "open_reviews": 1,
  "blocked_requests": 0,
  "risk_counts": { "low": 0, "medium": 2, "high": 1 },
  "top_signals": [{ "code": "rapid_requests", "count": 2 }],
  "recent_events": []
}
```

### `GET /admin/fraud-events`

Returns the full basic fraud event stream and the same summary used by the
admin dashboard.

Auth: admin with `admin.fraud.read`

Events include the rider summary, related ride summary when a ride was created,
`risk_score`, `risk_level`, signal codes, action (`allowed`, `review_required`,
or `blocked`), and review timestamps.

### `GET /admin/audit-logs?limit=50`

Returns recent security and operations audit events plus action/outcome counts.
Events include actor role/user summary, target type/id, safe metadata, request
ID, client IP, user agent, outcome, and timestamp.

Auth: admin with `admin.audit.read`

### `GET /admin/observability`

Returns in-memory API observability for the current process: uptime, request
counts, latency averages and p95, status/method counts, route health, recent
requests, websocket connection counts, background job status, and storage health.

Auth: admin with `admin.observability.read`

### `GET /admin/access-control`

Returns the current admin role, effective permissions, permission labels, and
available admin role definitions.

Auth: admin with `admin.dashboard.read`

### `PATCH /admin/users/{role}/{user_id}/status`

Sets a rider or driver account status.

Auth: admin with `admin.users.manage`

Path:

- `role`: `rider` or `driver`
- `user_id`: target user id

Request:

```json
{
  "status": "suspended"
}
```

Response: `AdminUserSummary`

Notes:

- Status must be `active` or `suspended`.
- Suspending a driver tries to set them offline.

### `POST /admin/drivers/{driver_id}/force-offline`

Forces a driver offline.

Auth: admin with `admin.drivers.manage`

Response: `AdminDriverSummary`

Notes:

- If the driver has an active non-pending ride, the API returns `409`.
- If the driver has a pending ride request, it is released back to matching.

### `PATCH /admin/drivers/{driver_id}/documents`

Reviews all required mock documents for a driver.

Auth: admin with `admin.drivers.manage`

Request:

```json
{
  "status": "verified"
}
```

Response: `AdminDriverSummary`

Notes:

- `status` must be `pending_review`, `verified`, or `rejected`.
- A driver with unverified or rejected documents cannot go online or receive
  dispatches.
- If a pending dispatch belongs to a driver whose documents are moved away from
  `verified`, that dispatch is released back to matching.

## Drivers

### `POST /drivers/location`

Updates the authenticated driver's location.

Auth: driver

Request:

```json
{
  "location": { "lat": 51.5074, "lng": -0.1278 }
}
```

Response: `UserProfile`

Side effects:

- Updates the PostGIS driver index when database persistence is enabled.
- Broadcasts active ride updates if the driver is on a ride.
- Makes the driver `available` when online and not busy.
- Triggers matching for waiting rides.

### `POST /drivers/availability`

Sets driver online/offline state.

Auth: driver

Request:

```json
{
  "online": true
}
```

Response: `UserProfile`

Rules:

- Online is blocked until all required driver documents are verified.
- Online drivers without a location get the default London location.
- Offline is blocked for accepted, arrived, or in-progress rides.
- Offline during a pending ride request declines that request and rematches it.

### `GET /drivers/me/ride-request`

Returns the current non-terminal ride assigned to the driver, or `null`.

Auth: driver

Response: `Ride | null`

### `GET /drivers/me/earnings`

Returns driver earnings from completed rides.

Auth: driver

Response:

```json
{
  "currency": "USD",
  "platform_fee_rate": 0.2,
  "gross_total": 100,
  "platform_fee_total": 20,
  "net_total": 80,
  "today_net": 40,
  "today_rides": 2,
  "completed_rides": 5,
  "acceptance_rate": 80,
  "rides": []
}
```

## Search And Routing

### `GET /search/locations?q={query}`

Searches OpenStreetMap Nominatim for up to five locations.

Auth: none

Response:

```json
[
  {
    "name": "London, Greater London, England, United Kingdom",
    "lat": 51.5074456,
    "lng": -0.1277653
  }
]
```

Notes:

- Queries shorter than two characters return `[]`.
- Redis caches successful searches when configured.

### `POST /routes/estimate`

Estimates distance, duration, fare, route geometry, and turn-by-turn steps.

Auth: none

Request:

```json
{
  "pickup": { "lat": 51.5074, "lng": -0.1278 },
  "destination": { "lat": 51.5155, "lng": -0.1419 },
  "vehicle_type": "premium",
  "promo_code": "SAVE10"
}
```

Response:

```json
{
  "distance_km": 3.2,
  "duration_min": 12,
  "currency": "USD",
  "fare": 9.4,
  "vehicle_type": "premium",
  "vehicle_type_label": "Premium",
  "promo_code": "SAVE10",
  "fare_breakdown": {
    "vehicle_type": "premium",
    "vehicle_type_label": "Premium",
    "vehicle_multiplier": 1.65,
    "vehicle_charge": 4.12,
    "total_before_promo": 13.52,
    "promo_code": "SAVE10",
    "promo_label": "10% off",
    "promo_discount": 1.35
  },
  "route": [{ "lat": 51.5074, "lng": -0.1278 }],
  "steps": [
    {
      "instruction": "Head toward the destination",
      "distance_km": 3.2,
      "duration_min": 12,
      "location": { "lat": 51.5074, "lng": -0.1278 }
    }
  ],
  "source": "osrm"
}
```

Notes:

- Provider is selected by `ROUTING_PROVIDER`: `osrm` or `graphhopper`.
- Redis caches successful route payloads when configured.
- `vehicle_type` defaults to `standard` and changes the fare multiplier.
- Supported demo promo codes are `SAVE10`, `MYUBER5`, and `WELCOME20`.
- If the external provider fails, the API returns a fallback straight-line
  estimate adjusted for driving distance.

## Rides

### `POST /rides`

Creates a ride request for the authenticated rider.

Auth: rider

Request:

```json
{
  "rider_id": "current-rider-id",
  "pickup": { "lat": 51.5074, "lng": -0.1278 },
  "destination": { "lat": 51.5155, "lng": -0.1419 },
  "vehicle_type": "xl",
  "promo_code": "SAVE10",
  "scheduled_for": "2026-05-18T09:30:00+00:00"
}
```

Response: `Ride`

Side effects:

- Creates a wallet-backed mock payment in `authorized` status and places the
  fare amount on wallet hold.
- Scores the request with basic fraud controls before placing a wallet hold.
  Medium/high-risk requests are recorded for admin review; very high-risk
  requests are blocked with `403`.
- Calculates route, duration, fare, and fare breakdown.
- Applies a valid promo code to the fare before authorization.
- Starts immediate rides at `matching`.
- Starts future rides at `scheduled`; due scheduled rides enter the matching
  queue automatically.
- Assigns the nearest available driver of the requested vehicle type.
- Driver dispatch offers expire after `MYUBER_DISPATCH_TIMEOUT_SECONDS` seconds
  and are automatically rematched if the driver does not respond.
- If no matching driver is available, keeps the ride in the queue with
  `queue_position` and `queue_size` and marks it `no_drivers_available`.

### `POST /rides/{ride_id}/rider-location`

Updates rider location for a non-terminal ride.

Auth: owning rider

Request:

```json
{
  "location": { "lat": 51.5074, "lng": -0.1278 }
}
```

Response: `Ride`

### `GET /rides/history`

Returns rides for the current rider or driver, newest first.

Auth: rider or driver

Response: `Ride[]`

### `GET /rides/{ride_id}`

Returns a ride the current user may view.

Auth: owning rider, assigned driver, or admin

Response: `Ride`

### `GET /rides/{ride_id}/receipt`

Returns the generated receipt for a paid or refunded ride.

Auth: owning rider, assigned driver, or admin

Response: `Receipt`

Rules:

- Receipt is available after payment capture.
- Completion and cancellation-fee receipts are generated automatically.

### `POST /rides/{ride_id}/receipt/email`

Sends the receipt email again for the ride.

Auth: owning rider or admin

Response:

```json
{
  "receipt": {},
  "email_delivery": {
    "status": "sent",
    "provider": "smtp"
  }
}
```

Notes:

- Configure `MYUBER_SMTP_HOST`, `MYUBER_SMTP_PORT`,
  `MYUBER_SMTP_USERNAME`, `MYUBER_SMTP_PASSWORD`, and
  `MYUBER_RECEIPT_EMAIL_FROM` for real SMTP delivery.
- Without SMTP config, the API records a `logged` delivery for local testing.

### `POST /rides/{ride_id}/share`

Creates or reuses a share token for a ride.

Auth: user who can view the ride

Response:

```json
{
  "token": "share-token",
  "url_path": "/?share=share-token",
  "created_at": "2026-05-17T00:00:00+00:00",
  "ride": {}
}
```

### `GET /shares/{share_token}`

Returns a redacted shared ride payload.

Auth: none, share token required

Response:

```json
{
  "token": "share-token",
  "created_at": "2026-05-17T00:00:00+00:00",
  "ride": {}
}
```

### `POST /rides/{ride_id}/ratings`

Adds or replaces the current user's rating for a completed ride.

Auth: owning rider or assigned driver

Request:

```json
{
  "score": 5,
  "comment": "Great trip"
}
```

Rules:

- Ride must be `completed`.
- Score must be from 1 to 5.
- Comment is capped at 280 characters.

Response: `Ride`

### `POST /rides/{ride_id}/issues`

Reports an issue on a ride.

Auth: user who can view the ride

Request:

```json
{
  "category": "safety",
  "description": "Issue details"
}
```

Rules:

- Description is required and capped at 600 characters.
- Category defaults to `other` and is capped at 40 characters.

Response: `Ride`

### `POST /rides/{ride_id}/accept`

Accepts a pending ride request.

Auth: assigned driver

Response: `Ride`

Rules and side effects:

- Ride must be assigned to the driver and in `pending_driver`.
- Expired pending requests return `409` and are rematched by dispatch.
- Driver availability becomes `busy`.
- Driver accepted count increments.
- Rider receives a notification.

### `POST /rides/{ride_id}/reject`

Rejects a pending ride request.

Auth: assigned driver

Response: `Ride`

Rules and side effects:

- Ride must be assigned to the driver and in `pending_driver`.
- Driver is added to the ride's declined set.
- Driver availability becomes `available`.
- Pending dispatch expiry is cleared.
- Ride returns to `matching` and immediately attempts rematching.

### `POST /rides/{ride_id}/status`

Progresses an assigned ride.

Auth: assigned driver

Request:

```json
{
  "status": "arrived"
}
```

Allowed values:

- `arrived`
- `in_progress`
- `completed`

The ride transition graph still applies, so drivers must progress in order:
`accepted -> arrived -> in_progress -> completed`.

Response: `Ride`

Side effects:

- `arrived`: notifies rider.
- `in_progress`: notifies rider.
- `completed`: captures mock payment, releases driver, notifies rider and driver.

### `POST /rides/{ride_id}/refund`

Simulates a refund for a completed paid ride.

Auth: owning rider

Response: `Ride`

Rules:

- Ride must be `completed`.
- Payment must be `paid`.

### `POST /rides/{ride_id}/cancel`

Cancels a ride.

Auth: owning rider or assigned driver

Response: `Ride`

Rules:

- Riders may cancel `scheduled`, `matching`, `pending_driver`, `accepted`,
  `arrived`, or `no_drivers_available`.
- Drivers may cancel `accepted` or `arrived`.
- Rider cancellation after `accepted` or `arrived` charges a cancellation fee:
  25% of the authorized fare, with a `$3` minimum and `$10` maximum.
- Other pre-trip cancellations void the authorized wallet payment.
- Cancel releases the driver when one is assigned.
