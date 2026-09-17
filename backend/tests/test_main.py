from __future__ import annotations

import unittest

import httpx

from backend.app.main import app


class MainTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_endpoints_return_ok(self) -> None:
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for path in ("/health", "/api/v1/health"):
                with self.subTest(path=path):
                    response = await client.get(path)

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {"status": "ok"})
