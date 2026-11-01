# Working on Team Sync

Read `docs/ARCHITECTURE.md` and `docs/PROTOCOL.md` before changing the domain.
Preserve project isolation, transactional event writes, command idempotency,
lease/version checks, provenance, and immutable published artifacts.

- Domain mutations belong in `Core`; transports use the same command layer.
- A new related entity needs a project-scoped composite foreign key.
- Do not hold DB locks while transferring files, waiting on a model, or calling a remote API.
- Credentials must not appear in events, logs, checked-in examples, or shared state.
- Schema changes require Alembic migrations and updated exported contracts.
- Keep old command/event consumers in mind; explain incompatible changes in an ADR.
- A host-specific wake-up feature must declare its actual capability and fallback.

Use `uv run ruff check .`, `uv run ruff format --check .`, and `uv run pytest -q`.
Integration tests require `TEAM_SYNC_TEST_DATABASE_URL` and real PostgreSQL.
Regenerate contracts with `uv run team-sync-contracts`.
