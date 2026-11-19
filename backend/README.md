# FastAPI backend

## Run locally

From the project root, create and activate a Python virtual environment, then install dependencies:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\Activate.ps1
python -m pip install -r backend\requirements-dev.txt
npm run dev:backend
```

The `dev` and `start` scripts use the active environment's `python` command and work on Windows, macOS, and Linux.

The API reads `MONGODB_URI`, `MONGODB_DATABASE`, `JWT_SECRET`, `CORS_ORIGINS`, and `MAX_FILE_SIZE` from the root `.env`.

- Swagger UI: `http://127.0.0.1:8003/docs`
- Liveness: `GET http://127.0.0.1:8003/api/health`
- Readiness: `GET http://127.0.0.1:8003/api/ready` (performs a read-only MongoDB ping)

The app does not seed data or create indexes at startup.

## Authentication

The FastAPI API exposes `POST /api/auth/register`, `POST /api/auth/login`, and `GET /api/auth/me`. Login accepts `{ "email": "...", "password": "..." }`; usernames are not login identifiers. It preserves the existing response fields, bcrypt password hashes (`password`, `passwordHash`, or `password_hash`), and HS256 JWT claims (`id`, `email`, and `role`). Case-insensitive email values and MongoDB ObjectId user IDs are supported. Public registration always assigns the `CUSTOMER` role. Logout is client-side token removal because this API uses stateless JWTs and has no token revocation store.

Set `JWT_SECRET` in the root `.env`; this API uses that configured value for both JWT signing and verification. Share it with another service only if that service actually validates these tokens. Development falls back to the legacy `dev_secret`; production startup requires `JWT_SECRET`. `JWT_EXPIRES_IN` defaults to `7d`.

FastAPI does not seed user accounts; registration creates `CUSTOMER` users. Admin/staff access requires a user whose stored role is `ADMIN` or `STAFF`. Authentication tests use a fake MongoDB client and do not write to the configured database.

Country and customer routes preserve the existing collection names, ObjectId country IDs, response envelopes, duplicate checks, and reference-protected deletion. Country mutations require the `ADMIN` role; all routes require a valid JWT. CRUD tests use a fake MongoDB client and do not change the original database.

The API also applies the Express limits of 1,000 requests per 15 minutes and 10 login requests per 15 minutes. These counters are process-local; use shared rate-limit storage before running multiple FastAPI workers or replicas.

## Tests

Run from the project root with the virtual environment activated:

```powershell
python -m pytest backend\tests -q
```

See [the migration plan](../docs/fastapi-migration-plan.md) for audited feature coverage, API mapping, data safeguards, and phase status.
