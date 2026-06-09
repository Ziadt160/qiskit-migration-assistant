"""FastAPI surface for the migration assistant.

POST /migrate enqueues a job and returns its id immediately; GET /jobs/{id} polls
for status and the structured result. Hardened with a per-client rate limit on the
expensive endpoint and Prometheus metrics at /metrics. Built by a factory so tests
can inject a store, run jobs eagerly (no Redis), and tune the rate limit.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.config import get_settings
from src.db.db import JobStore
from src.migration.models import MigrationResult
from src.migration.validate_input import InputValidationError, validate_input
from src.observability import MIGRATE_REQUESTS, metrics_payload
from src.worker.queue import enqueue_migration

# Bundled web UI lives at src/app/web (this file is src/api/main.py).
_WEB_DIR = Path(__file__).resolve().parent.parent / "app" / "web"


class MigrateRequest(BaseModel):
    code: str = Field(..., description="Old Qiskit code to migrate.")
    source_version: str | None = Field(default=None, description="Optional source version hint.")


class MigrateResponse(BaseModel):
    job_id: str
    status: str


class JobResponse(BaseModel):
    id: str
    status: str
    result: MigrationResult | None = None
    error: str | None = None


class _FixedWindowLimiter:
    """Simple per-key fixed-window limiter (single-process; fine for one VM)."""

    def __init__(self, limit: int, window_s: int = 60):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str) -> bool:
        now = time.time()
        cutoff = now - self.window_s
        hits = [t for t in self._hits[key] if t > cutoff]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True


def create_app(
    store: JobStore | None = None,
    eager: bool | None = None,
    rate_limit_per_min: int | None = None,
) -> FastAPI:
    settings = get_settings()
    store = store or JobStore()
    store.create_all()
    eager = settings.queue_eager if eager is None else eager
    limit = settings.rate_limit_per_min if rate_limit_per_min is None else rate_limit_per_min
    limiter = _FixedWindowLimiter(limit)

    app = FastAPI(title="Qiskit Migration Assistant", version="0.1.0")

    def get_store() -> JobStore:
        return store

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path == "/migrate" and request.method == "POST":
            client = request.client.host if request.client else "unknown"
            if not limiter.allow(client):
                return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        return await call_next(request)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> dict:
        try:
            store.recent(limit=1)
            return {"status": "ready"}
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"db not ready: {e}") from e

    @app.get("/metrics")
    def metrics() -> Response:
        if not settings.enable_metrics:
            raise HTTPException(status_code=404, detail="metrics disabled")
        body, content_type = metrics_payload()
        return Response(content=body, media_type=content_type)

    @app.post("/migrate", response_model=MigrateResponse, status_code=202)
    def migrate(req: MigrateRequest, store: JobStore = Depends(get_store)) -> MigrateResponse:
        try:
            validate_input(req.code)
        except InputValidationError as e:
            MIGRATE_REQUESTS.labels(result="rejected").inc()
            raise HTTPException(status_code=400, detail=str(e)) from e

        job_id = store.create_job(req.code, source_version=req.source_version)
        enqueue_migration(job_id, eager=eager, store=store)
        MIGRATE_REQUESTS.labels(result="accepted").inc()
        job = store.get(job_id)
        assert job is not None
        return MigrateResponse(job_id=job_id, status=job.status)

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    def get_job(job_id: str, store: JobStore = Depends(get_store)) -> JobResponse:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        result = (
            MigrationResult.model_validate(json.loads(job.result_code)) if job.result_code else None
        )
        return JobResponse(id=job.id, status=job.status, result=result, error=job.error)

    # Serve the bundled single-page web UI (src/app/web) at /ui, with / -> /ui/.
    # Mounted last so it never shadows the JSON API routes above.
    if _WEB_DIR.is_dir():

        @app.get("/", include_in_schema=False)
        def _root() -> RedirectResponse:
            return RedirectResponse(url="/ui/")

        app.mount("/ui", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")

    return app


app = create_app()
