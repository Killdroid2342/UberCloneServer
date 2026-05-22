# RideOps Server Docs

This directory is the detailed documentation set for the FastAPI backend. The
root [README](../README.md) covers quick local setup; this file is the map for
API, architecture, environment, realtime, data-model, and code-structure
references.

| Document | Covers |
| --- | --- |
| [API documentation](API.md) | REST endpoints, auth, validation, rate limiting, abuse detection, platform readiness, admin APIs, rides, payments, notifications |
| [Swagger and OpenAPI](OPENAPI.md) | Interactive Swagger/ReDoc docs and generated OpenAPI contract |
| [WebSocket API docs](WEBSOCKETS.md) | Server WebSocket endpoints, messages, auth, Redis fanout |
| [DB schema diagram](DB_SCHEMA.md) | Runtime JSONB state, PostGIS driver-location index, logical domain model |
| [Ride lifecycle diagram](RIDE_LIFECYCLE.md) | Ride state transitions, dispatch behavior, payment/cancellation side effects |
| [Architecture diagram](ARCHITECTURE.md) | Full client/server system overview and external dependencies |
| [Development tooling](DEVELOPMENT.md) | Linting, formatting, pre-commit hooks, and OpenAPI export |
| [Environment configuration](ENVIRONMENT.md) | Local environment and seed-data configuration |

Related client behavior lives in `../MyUberClient/docs` from the workspace root.
