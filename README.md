# RideOps Server

FastAPI backend for the RideOps full-stack portfolio demo. It provides auth,
ride lifecycle workflows, realtime updates, mock payments, notifications,
operations dashboards, optional PostgreSQL/Redis integrations, and local-first
fallback behavior.

## Run Locally

Create a virtual environment, install dependencies, then run the API:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Copy `.env.example` to `.env` for local development and adjust the values you
need. When this repo is checked out inside the wider RideOps workspace, the
optional parent `setup.ps1` helper can prepare and start both repos together:

```powershell
powershell -ExecutionPolicy Bypass -File ..\setup.ps1 -Start
```

To seed demo accounts in a database-backed environment, run:

```powershell
python scripts/seed_demo_data.py --env-file .env --require-database
```

For repeatable local review/tests:

```powershell
python scripts/seed_demo_data.py --env-file .env --reset --deterministic --base-time 2026-05-23T12:00:00+00:00
```

`MYUBER_TEST_MODE=true` also enables the local-only
`POST /test/reset-demo-data` reset hook. Keep it disabled in production.

## Documentation

Use [docs/README.md](docs/README.md) as the full server documentation index.

Key references:

- [API documentation](docs/API.md)
- [Swagger/OpenAPI](docs/OPENAPI.md)
- [Environment configuration](docs/ENVIRONMENT.md)
- [Architecture overview](docs/ARCHITECTURE.md)
- [Development tooling](docs/DEVELOPMENT.md)
- [Persistence setup](docs/PERSISTENCE.md)
