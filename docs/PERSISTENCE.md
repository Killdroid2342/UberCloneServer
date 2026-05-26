# Persistence

RideOps Server runs local-first by default. If `DATABASE_URL` is empty, runtime
state lives in Python dictionaries and is lost when the API process stops. When
`DATABASE_URL` points at PostgreSQL, the API keeps the same in-memory runtime
model but snapshots it to PostgreSQL after state-changing requests.

