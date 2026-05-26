# WebSocket Documentation

The server exposes three realtime sockets. The TypeScript client wraps them in
`RideOpsClient/src/socket.ts` and typed helpers in `RideOpsClient/src/api.ts`.

## Client Behavior

- WebSocket base URL: `ws://localhost:8000`
- Authenticated sockets append the short-lived access JWT as `?token=<jwt>`.
- The browser client refreshes the access token through `/auth/refresh` and
  reconnects when an authorization close happens after token expiry.
- A heartbeat sends `{ "type": "ping", "sent_at": "<iso>" }` every 25 seconds.
- The server replies with `{ "type": "pong", "sent_at": "<same value>" }`.
- Reconnects use exponential backoff from 800 ms up to 10 seconds with jitter.
- Reconnects pause while the browser is offline and retry immediately on
  `online`, `pageshow`, or visible `visibilitychange`.
- Heartbeats close the socket if a `pong` is not received within 10 seconds.
- Up to 30 outbound messages are queued while disconnected.
- Realtime messages include `event_id`, `sequence`, and `delivery` metadata.
  The transport is at-least-once; clients use `event_id` to apply messages once.
- If the socket is reconnecting, the browser polls
  `/realtime/{target}/{target_id}/events` for missed ordered events.
- Location updates are also persisted in the client offline queue so the latest
  point can flush after reload or network recovery.
- Invalid auth or unauthorized resource access closes with code `1008`.

## Socket Endpoints

| Endpoint | Auth | Purpose |
| --- | --- | --- |
| `/ws/rides/{ride_id}?token=<jwt>` | owning rider, assigned driver, or admin | Live private ride updates |
| `/ws/drivers/{driver_id}?token=<jwt>` | matching driver account only | Driver assignment, availability, ride, location, and notification events |
| `/ws/shares/{share_token}` | valid share token | Redacted public trip tracking |

## `/ws/rides/{ride_id}`

Private ride room for users who can view the ride.

### On Connect

The server immediately sends the current ride:

```json
{
  "type": "ride_update",
  "ride": {}
}
```

### Client To Server

#### `ping`

```json
{
  "type": "ping",
  "sent_at": "2026-05-17T00:00:00.000Z"
}
```

#### `rider_location_update`

Only riders can send this message.

```json
{
  "type": "rider_location_update",
  "location": { "lat": 51.5074, "lng": -0.1278 }
}
```

Validation failures return:

```json
{
  "type": "error",
  "detail": "Invalid location"
}
```

### Server To Client

```json
{
  "type": "ride_update",
  "ride": {}
}
```

```json
{
  "type": "notification",
  "notification": {},
  "unread_count": 1
}
```

```json
{
  "type": "pong",
  "sent_at": "2026-05-17T00:00:00.000Z"
}
```

## `/ws/drivers/{driver_id}`

Driver control and dispatch socket.

### On Connect

If the driver has an active ride, the server sends:

```json
{
  "type": "ride_request",
  "ride": {}
}
```

or:

```json
{
  "type": "ride_update",
  "ride": {}
}
```

The initial event is `ride_request` when the active ride is `pending_driver`;
otherwise it is `ride_update`.

### Client To Server

#### `ping`

```json
{
  "type": "ping",
  "sent_at": "2026-05-17T00:00:00.000Z"
}
```

#### `availability_update`

```json
{
  "type": "availability_update",
  "online": true
}
```

If `online` is not a boolean, the server sends:

```json
{
  "type": "error",
  "detail": "Invalid availability"
}
```

If business rules reject the availability change, `detail` contains the FastAPI
error message, such as `Complete or cancel the active ride before going offline`.

#### `driver_location_update`

```json
{
  "type": "driver_location_update",
  "location": { "lat": 51.5074, "lng": -0.1278 }
}
```

Validation failures return:

```json
{
  "type": "error",
  "detail": "Invalid location"
}
```

### Server To Client

```json
{
  "type": "ride_request",
  "ride": {}
}
```

```json
{
  "type": "ride_update",
  "ride": {}
}
```

```json
{
  "type": "ride_cleared",
  "ride_id": "uuid"
}
```

`ride_cleared` is sent when a driver rejects, goes offline during a pending
request, has a dispatch request time out, or has documents moved out of
verified status while a pending request is assigned.

```json
{
  "type": "availability_update",
  "availability": "available",
  "online": true
}
```

```json
{
  "type": "driver_location_update",
  "location": { "lat": 51.5074, "lng": -0.1278 },
  "ride": {}
}
```

```json
{
  "type": "notification",
  "notification": {},
  "unread_count": 1
}
```

```json
{
  "type": "pong",
  "sent_at": "2026-05-17T00:00:00.000Z"
}
```

## `/ws/shares/{share_token}`

Public socket for redacted shared trip tracking. No JWT is required, but the
share token must exist and must not be revoked.

### On Connect

```json
{
  "type": "share_update",
  "ride": {}
}
```

### Client To Server

Only heartbeat is handled:

```json
{
  "type": "ping",
  "sent_at": "2026-05-17T00:00:00.000Z"
}
```

### Server To Client

```json
{
  "type": "share_update",
  "ride": {}
}
```

```json
{
  "type": "pong",
  "sent_at": "2026-05-17T00:00:00.000Z"
}
```

## Redis Fanout

When `REDIS_URL` is configured, each API instance publishes realtime events on
`REDIS_CHANNEL`, defaulting to `rideops:realtime`.

Internal event shape:

```json
{
  "id": "rt_abc123",
  "origin": "api-instance-uuid",
  "target": "ride",
  "target_id": "ride-or-driver-or-share-id",
  "sequence": 42,
  "created_at": "2026-05-17T00:00:00.000Z",
  "message": {
    "type": "ride_update",
    "ride": {},
    "event_id": "rt_abc123",
    "sequence": 42,
    "delivery": {
      "mode": "at_least_once",
      "dedupe": "event_id",
      "ordering": "per_target_sequence"
    }
  }
}
```

Targets:

- `ride`: fanout to `/ws/rides/{ride_id}`
- `driver`: fanout to `/ws/drivers/{driver_id}`
- `share`: fanout to `/ws/shares/{share_token}`

The publishing instance also dispatches locally before publishing. Subscriber
instances ignore events with their own `origin` to avoid duplicate delivery.

## Delivery Guarantees

- Server fanout is at-least-once. Redis publishes are retried with exponential
  backoff using `MYUBER_REALTIME_REDIS_PUBLISH_ATTEMPTS` and
  `MYUBER_REALTIME_REDIS_RETRY_BASE_MS`.
- Client application is effectively exactly-once per stream because
  `RideOpsClient/src/socket.ts` drops duplicate `event_id` values.
- Ordering is per target, using a monotonically increasing `sequence` for each
  `ride`, `driver`, or `share` stream.
- Failed Redis publishes are stored in the in-process realtime dead letter queue
  after retries. Counts appear under `/admin/observability` in
  `realtime.delivery`.
- A bounded in-process event log is kept per target for WebSocket fallback
  polling.

## Fallback Events

The fallback endpoint returns ordered events newer than the caller's last
sequence:

```http
GET /realtime/ride/{ride_id}/events?after_sequence=41&limit=50
GET /realtime/driver/{driver_id}/events?after_sequence=41&limit=50
GET /realtime/share/{share_token}/events?after_sequence=41&limit=50
```

Ride and driver streams use the same permissions as their WebSocket endpoints.
Share streams remain public to holders of a valid share token. The response
contains `fallback_poll_seconds` and an `events` array whose `message` values
match the WebSocket server-to-client payloads.
