import asyncio
import hashlib
import json
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from team_sync import schemas as s
from team_sync.core import WRITE_ROLES, Core, DomainError
from team_sync.settings import Settings

security = HTTPBearer(auto_error=False)
Collection = Literal[
    "actors",
    "challenges",
    "tasks",
    "findings",
    "flags",
    "verifications",
    "submissions",
    "artifacts",
]


def credential(auth: Annotated[HTTPAuthorizationCredentials | None, Depends(security)]):
    if auth is None:
        raise DomainError(401, "invalid_credential", "Bearer credential required")
    return auth.credentials


Token = Annotated[str, Depends(credential)]


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    if not settings.bootstrap_token or len(settings.bootstrap_token) < 16:
        raise ValueError("TEAM_SYNC_BOOTSTRAP_TOKEN must contain at least 16 characters")
    core = Core(settings)

    @asynccontextmanager
    async def lifespan(app):
        settings.artifact_dir.mkdir(parents=True, exist_ok=True)
        yield
        core.engine.dispose()

    app = FastAPI(title="CTF Team Sync", version="0.1.0", lifespan=lifespan)
    app.state.core = core

    @app.middleware("http")
    async def browser_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path not in {"/docs", "/redoc", "/openapi.json", "/health"}:
            response.headers["Cache-Control"] = "no-cache"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            )
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            status_code=exc.status,
            content={"code": exc.code, "detail": exc.detail},
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )

    @app.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "detail": json.loads(exc.json(include_input=False)),
            },
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.post("/api/v1/projects", status_code=201)
    def initialize(data: s.ProjectCreate, token: Token):
        return core.initialize(token, data)

    @app.post("/api/v1/projects/{project_id}/actors", status_code=201)
    def enroll(project_id: s.ID, data: s.ActorCreate, token: Token):
        return core.enroll(project_id, token, data)

    @app.post("/api/v1/projects/{project_id}/actors/{actor_id}/revoke")
    def revoke(project_id: s.ID, actor_id: s.ID, token: Token):
        return core.revoke(project_id, token, actor_id)

    @app.get("/api/v1/projects/{project_id}/snapshot")
    def snapshot(project_id: s.ID, token: Token):
        return core.snapshot(project_id, token)

    @app.get("/api/v1/projects/{project_id}/collections/{collection}")
    def listing(
        project_id: s.ID,
        collection: Collection,
        token: Token,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        return {"items": core.listing(project_id, token, collection, offset, limit)}

    @app.post("/api/v1/projects/{project_id}/commands")
    def command(project_id: s.ID, data: s.AgentAction, token: Token):
        return core.execute(
            project_id,
            token,
            data.operation,
            data.arguments,
            data.resource_id,
            data.idempotency_key,
        )

    @app.get("/api/v1/projects/{project_id}/inbox")
    def inbox(
        project_id: s.ID,
        token: Token,
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        return {"items": core.inbox(project_id, token, offset, limit)}

    @app.get("/api/v1/projects/{project_id}/events")
    def events(
        project_id: s.ID,
        token: Token,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ):
        return core.events(project_id, token, after, limit)

    async def wait_batch(request, project_id, token, after, timeout):
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            batch = await run_in_threadpool(core.events, project_id, token, after)
            if batch["items"] or asyncio.get_running_loop().time() >= deadline:
                return batch
            if await request.is_disconnected():
                return batch
            await asyncio.sleep(min(0.25, max(0, deadline - asyncio.get_running_loop().time())))

    @app.get("/api/v1/projects/{project_id}/events/wait")
    async def wait_events(
        request: Request,
        project_id: s.ID,
        token: Token,
        after: Annotated[int, Query(ge=0)] = 0,
        timeout: Annotated[int, Query(ge=0, le=25)] = 25,
    ):
        return await wait_batch(request, project_id, token, after, timeout)

    @app.get("/api/v1/projects/{project_id}/events/stream")
    async def stream_events(
        request: Request,
        project_id: s.ID,
        token: Token,
        after: Annotated[int, Query(ge=0)] = 0,
        last_event_id: Annotated[int | None, Header(ge=0)] = None,
    ):
        cursor = last_event_id if last_event_id is not None else after
        # Authenticate and validate the cursor before sending response headers.
        await run_in_threadpool(core.events, project_id, token, cursor, 1)

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                batch = await wait_batch(request, project_id, token, cursor, 10)
                if not batch["items"]:
                    yield ": keepalive\n\n"
                for event in batch["items"]:
                    yield f"id: {event['seq']}\nevent: change\ndata: {json.dumps(event)}\n\n"
                    cursor = event["seq"]

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/v1/projects/{project_id}/artifacts", status_code=201)
    async def upload(
        request: Request,
        project_id: s.ID,
        token: Token,
        idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
        name: Annotated[str, Query(min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1f]+$")],
    ):
        def authorize():
            with core.context(project_id, token, roles=WRITE_ROLES):
                pass

        await run_in_threadpool(authorize)
        directory = settings.artifact_dir / project_id
        directory.mkdir(parents=True, exist_ok=True)
        file_hash, size = hashlib.sha256(), 0
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
            temp_path = Path(temporary.name)
            try:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_artifact_bytes:
                        raise DomainError(413, "artifact_too_large", "Artifact exceeds size limit")
                    file_hash.update(chunk)
                    await run_in_threadpool(temporary.write, chunk)
                sha256 = file_hash.hexdigest()
                destination = directory / sha256

                def publish_bytes():
                    temporary.flush()
                    os.fsync(temporary.fileno())
                    try:
                        os.link(temp_path, destination)
                    except FileExistsError:
                        pass
                    # Persist both the blob entry and a newly created project directory.
                    for durable_directory in (directory, settings.artifact_dir):
                        directory_fd = os.open(durable_directory, os.O_RDONLY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)

                await run_in_threadpool(publish_bytes)
                return await run_in_threadpool(
                    core.add_artifact, project_id, token, name, sha256, size, idempotency_key
                )
            finally:
                temp_path.unlink(missing_ok=True)

    @app.get("/api/v1/projects/{project_id}/artifacts/{artifact_id}/content")
    def download(project_id: s.ID, artifact_id: s.ID, token: Token):
        artifact = core.artifact(project_id, token, artifact_id)
        return FileResponse(
            settings.artifact_dir / project_id / artifact["sha256"],
            filename=artifact["name"],
            media_type="application/octet-stream",
            headers={"X-Content-SHA256": artifact["sha256"]},
        )

    app.mount("/", StaticFiles(directory=Path(__file__).with_name("web"), html=True), name="web")
    return app
