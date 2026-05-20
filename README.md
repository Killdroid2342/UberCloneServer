# MyUber Server

FastAPI backend for the MyUber demo app.

## Run Locally

Install dependencies from `requirements.txt`, configure `.env` as needed, then run
the FastAPI app with Uvicorn.

```powershell
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Copy `.env.example` to `.env` for local development. To seed demo accounts in a
database-backed environment, run:

```powershell
python scripts/seed_demo_data.py --env-file .env --require-database
```

## Documentation

Use [docs/README.md](docs/README.md) as the full server documentation index.

Key references:

- [API documentation](docs/API.md)
- [Swagger/OpenAPI](docs/OPENAPI.md)
- [Environment configuration](docs/ENVIRONMENT.md)
- [Architecture overview](docs/ARCHITECTURE.md)
