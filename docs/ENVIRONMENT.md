# Environment Configuration

The API reads configuration from environment variables via `python-dotenv`.
Commit `.env.example` only; keep your real `.env` file local.

## Files

| File | Purpose |
| --- | --- |
| `.env.example` | Safe local development template |
| `.env` | Your local values; ignored by Git |

## Common Variables

| Variable | Notes |
| --- | --- |
| `MYUBER_ENV` | Usually `development` locally |
| `MYUBER_SECRET_KEY` | JWT signing secret; change the example value |
| `MYUBER_ALLOWED_ORIGINS` | Comma-separated browser origins allowed by CORS |
| `MYUBER_ADMIN_EMAIL` / `MYUBER_ADMIN_PASSWORD` | Bootstrap admin login |
| `DATABASE_URL` | Optional durable database connection; leave blank for in-memory local state |
| `REDIS_URL` | Optional Redis URL for cache, rate limits, and websocket fanout |
| `MYUBER_SEED_DEMO_DATA` | Seeds demo accounts when enabled |
| `ROUTING_PROVIDER` | `osrm` by default; `graphhopper` can be configured separately |

## Seed Data

Seed a local database with:

```powershell
python scripts/seed_demo_data.py --env-file .env --require-database
```

For in-memory local demos, set `MYUBER_SEED_DEMO_DATA=true` before starting the
API. Demo credentials use the password `myuber-demo-pass`:

| Role | Email |
| --- | --- |
| Rider | `rider@myuber.local` |
| Driver | `driver@myuber.local` |
| Pending driver | `driver.pending@myuber.local` |
| Operations admin | `ops@myuber.local` |
