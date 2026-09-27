from __future__ import annotations

import unittest

import httpx

from backend.app.analysis import InMemoryAnalysisJobStore, InMemoryAnalysisStore
from backend.app.main import app, create_app


class _Scheduler:
    def __init__(self) -> None:
        self.started = 0
        self.closed = 0

    async def start(self) -> None:
        self.started += 1

    async def close(self) -> None:
        self.closed += 1


class MainTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoints_return_ok(self) -> None:
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for path in ("/health", "/api/v1/health"):
                with self.subTest(path=path):
                    response = await client.get(path)

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"status": "ok"})


    async def test_lifespan_starts_and_stops_configured_public_scheduler(self) -> None:
        scheduler = _Scheduler()
        application = create_app(
            analysis_store=InMemoryAnalysisStore(),
            job_store=InMemoryAnalysisJobStore(),
            configure_public_repository_catalog=False,
            analysis_scheduler=scheduler,  # type: ignore[arg-type]
        )

        async with application.router.lifespan_context(application):
            self.assertEqual(scheduler.started, 1)
            self.assertEqual(scheduler.closed, 0)

        self.assertEqual(scheduler.closed, 1)
