# Copilot instructions for this repository
<!-- Copy to <repo>/.github/copilot-instructions.md, fill in, commit. -->

## What this repo is
<one or two sentences: service/app purpose, who uses it>

## Stack
- Language/runtime: <e.g. Python 3.12 / Node 22 / Java 21>
- Frameworks: <e.g. FastAPI, SQLAlchemy / React, Vite>
- Datastore: <e.g. PostgreSQL via Alembic migrations>

## Commands (exact, copy-pasteable)
- Install deps: `<...>`
- Run tests: `<...>`
- Run a single test: `<...>`
- Lint/format: `<...>`
- Build: `<...>`
- Run locally: `<...>`

## Project layout
- `<src/api/>`: <what lives here>
- `<src/services/>`: <business logic>
- `<tests/>`: <test layout and naming>

## Conventions
- <naming, error handling, logging style>
- <how to add an endpoint / component / migration: point at an example file>
- <dependency rules: do not add new libraries without a reason>

## Testing rules
- Every behavior change needs a test. Prefer <unit/integration> tests.
- Do not mock <the database / internal services> unless <reason>.

## Never do
- Never commit secrets, tokens, or .env files.
- Never edit generated files: <paths>.
- Never change <public API / DB schema> without being asked.
- Never push, merge, or force-push.
