# MyUber Server Docs

These docs belong with the FastAPI backend repository.

| Document | Covers |
| --- | --- |
| [API documentation](API.md) | REST endpoints, auth, rate limiting, fraud controls, admin APIs, rides, payments, notifications |
| [WebSocket API docs](WEBSOCKETS.md) | Server WebSocket endpoints, messages, auth, Redis fanout |
| [DB schema diagram](DB_SCHEMA.md) | Runtime JSONB state, PostGIS driver-location index, logical domain model |
| [Ride lifecycle diagram](RIDE_LIFECYCLE.md) | Ride state transitions, dispatch behavior, payment/cancellation side effects |
| [Architecture diagram](ARCHITECTURE.md) | Full client/server system overview and external dependencies |

Related client behavior lives in `../MyUberClient/docs` from the workspace root.
