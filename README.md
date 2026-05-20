# MyUber Server

FastAPI backend for the MyUber demo app.

## Docs

- [Server docs](docs/README.md)
- [API documentation](docs/API.md)
- [Swagger/OpenAPI](docs/OPENAPI.md)
- [Database schema](docs/DB_SCHEMA.md)
- [Ride lifecycle](docs/RIDE_LIFECYCLE.md)
- [WebSocket API](docs/WEBSOCKETS.md)
- [Architecture overview](docs/ARCHITECTURE.md)

## Run Locally

Install dependencies from `requirements.txt`, configure `.env` as needed, then
run the FastAPI app with Uvicorn.

Copy `.env.example` to `.env` for local development. To seed demo accounts in a
database-backed environment, run:

```powershell
python scripts/seed_demo_data.py --env-file .env --require-database
```
