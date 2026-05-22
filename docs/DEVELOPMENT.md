# Development Tooling

## Linting And Formatting

Install developer dependencies from the server directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Run linting:

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Apply formatting:

```powershell
.\.venv\Scripts\python.exe -m ruff format .
```

Run smoke tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

## Pre-Commit Hooks

Install hooks inside the server Git repository:

```powershell
.\.venv\Scripts\pre-commit.exe install
```

Run the hook suite manually:

```powershell
.\.venv\Scripts\pre-commit.exe run --all-files
```

## Typed API Contract

Export the FastAPI OpenAPI schema for generated clients and contract review:

```powershell
.\.venv\Scripts\python.exe scripts\export_openapi.py --output openapi.json
```

The browser client keeps its route-level request and response contract in
`MyUberClient\src\api-contract.ts`; compare it against this schema when adding
or changing routes.
