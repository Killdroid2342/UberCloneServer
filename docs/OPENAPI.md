# Swagger And OpenAPI

The FastAPI app publishes an OpenAPI 3 schema and two interactive API reference
views.

## Local URLs

Start the API from `RideOpsServer`:

```powershell
uvicorn app.main:app --reload
```

Then open:

| URL | Purpose |
| --- | --- |
| `http://localhost:8000/docs` | Swagger UI for interactive requests |
| `http://localhost:8000/redoc` | ReDoc reference view |
| `http://localhost:8000/openapi.json` | Machine-readable OpenAPI contract |

Swagger UI keeps bearer-token authorization between requests. Use a login
endpoint first, then authorize with the returned access token.

## Contract Shape

The generated contract includes:

- API metadata, version, contact, license, and local/deployment server entries
- Versioned `/v1` server entry, supported API version extension, and current
  region extension
- Tag groups for system, auth, wallet, notifications, admin, driver, routing,
  ride, and sharing workflows
- OAuth2 bearer-token security scheme generated from the FastAPI dependency
- Typed request schemas for signup, login, refresh, rides, route estimates,
  geofence evaluation, road snapping, route alternatives, distance matrices,
  traffic simulation, wallet, push subscriptions, admin actions, ratings, and
  issue reports
